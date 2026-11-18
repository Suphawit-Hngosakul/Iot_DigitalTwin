"""TransformBatch Lambda (issue #15, spec 30 §4, spec 01 §4).

Combines every raw part of the hour into ONE Parquet file per table:
  processed/readings_1s/device_id=<id>/date=<d>/<batch_id>.parquet
  processed/readings_1min/...                    (mean/max/min, no Leq — D19)
  processed/quality/...                          (one row per session)
Overwrites the batch's files (idempotent). Raw is not touched.

Rows: duplicates and DROP-flagged rows removed (batch_eval), values nulled by
flag (schema.clean_row). Mobile: GPS points implying > 30 km/h from the last
accepted point are nulled. Stationary: lat/lon from devices.csv. cell_id stays
null until #29 (D21). Needs pyarrow (Lambda layer AWS SDK for pandas).
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
from collections import defaultdict
from datetime import timedelta

import boto3

from common import batch_eval, schema

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_WALK_SPEED_KMH = 30.0

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _null_gps(rec: dict) -> None:
    rec["lat"] = rec["lon"] = rec["alt_m"] = rec["speed_kmh"] = None


def readings_1s(ev: batch_eval.BatchEval, devices) -> tuple[list[dict], int]:
    """(records, number of GPS points nulled by the speed filter)."""
    recs = []
    for r in ev.kept():
        rec = schema.clean_row(r.row, r.flags)
        rec["ts_utc"] = rec["ts_utc"].replace(tzinfo=None)
        if rec["device_type"] == "Stationary":
            reg = schema.lookup_device(devices, ev.batch.device_id, rec["ts_local"])
            rec["lat"], rec["lon"] = (reg.fixed_lat, reg.fixed_lon) if reg else (None, None)
            rec["alt_m"] = rec["speed_kmh"] = None
        recs.append(rec)
    recs.sort(key=lambda x: (x["ts_local"], x["session"], x["record_id"]))

    nulled = 0
    last: dict = {}                       # session -> last accepted (ts, lat, lon)
    for rec in recs:
        if rec["device_type"] == "Stationary" or rec["lat"] is None or rec["lon"] is None:
            continue
        prev = last.get(rec["session"])
        if prev is not None:
            dt = (rec["ts_local"] - prev[0]).total_seconds()
            if dt > 0:
                kmh = _haversine_m(prev[1], prev[2], rec["lat"], rec["lon"]) / dt * 3.6
                if kmh > MAX_WALK_SPEED_KMH:
                    _null_gps(rec)
                    nulled += 1
                    continue
        last[rec["session"]] = (rec["ts_local"], rec["lat"], rec["lon"])
    return recs, nulled


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def readings_1min(recs: list[dict]) -> list[dict]:
    by_min: dict = defaultdict(list)
    for rec in recs:
        by_min[rec["ts_local"].replace(second=0, microsecond=0)].append(rec)
    out = []
    for minute in sorted(by_min):
        rs = by_min[minute]
        spl = [r["db_spl"] for r in rs if r["db_spl"] is not None]
        first_gps = next((r for r in rs if r["lat"] is not None and r["lon"] is not None), None)
        out.append({
            "minute_local": minute,
            "minute_utc": minute - timedelta(hours=7),
            "n_rows": len(rs),
            "db_spl_mean": _mean(spl),
            "db_spl_max": max(spl) if spl else None,
            "db_spl_min": min(spl) if spl else None,
            "dba_mean": _mean(r["dba"] for r in rs),
            "temp_c_mean": _mean(r["temp_c"] for r in rs),
            "humidity_pct_mean": _mean(r["humidity_pct"] for r in rs),
            "lux_mean": _mean(r["lux"] for r in rs),
            "lat": first_gps["lat"] if first_gps else None,
            "lon": first_gps["lon"] if first_gps else None,
            "cell_id": None,              # #29
        })
    return out


def quality(ev: batch_eval.BatchEval) -> list[dict]:
    return [{
        "batch_id": ev.batch_id,
        "session": s["session"],
        "expected_rows": s["expected_rows"],
        "received_rows": s["received_rows"],
        "missing_rows": s["missing_rows"],
        "duplicate_rows": s["duplicate_rows"],
        "invalid_rows": s["invalid_rows"],
        "loss_rate": s["loss_rate"],
        "flag_counts": json.dumps(s["flag_counts"], sort_keys=True),
    } for s in ev.sessions()]


def to_parquet(table: str, records: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    t = pa.Table.from_pylist(records, schema=schema.arrow_schema(table))
    buf = io.BytesIO()
    pq.write_table(t, buf, compression="snappy")
    return buf.getvalue()


def transform(s3, bucket: str, event: dict, devices) -> dict:
    batch_id = event["batch_id"]
    files = batch_eval.read_files(s3, bucket, event["check"]["manifest"])
    ev = batch_eval.evaluate(batch_id, files, devices)
    recs, nulled = readings_1s(ev, devices)
    tables = {
        "readings_1s": recs,
        "readings_1min": readings_1min(recs),
        "quality": quality(ev),
    }
    keys = {}
    for name, rows in tables.items():
        key = schema.s3_key_processed(name, batch_id)
        s3.put_object(Bucket=bucket, Key=key, Body=to_parquet(name, rows))
        keys[name] = key
    return {
        "raw_rows": ev.raw_rows,
        "kept_rows": len(recs),
        "duplicate_rows": ev.count("duplicate"),
        "invalid_rows": ev.count("invalid"),
        "minutes": len(tables["readings_1min"]),
        "gps_speed_nulled": nulled,
        "keys": keys,
    }


def handler(event, context=None):
    result = transform(_client(), os.environ["DATA_BUCKET"], event, schema.load_devices())
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "transform": result}
