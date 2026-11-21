"""PlanMerge — first state of curated-merge (spec 60 §2.1, D23).

Decides which periods must be merged again. A period is stale when
  - it has no curated/_success/merge/<period>.json yet, or
  - its satellite marker, the buildings marker, or an IoT manifest inside its IoT date range
    is newer than the `started_at` stored in that merge marker, or
  - the run is forced.

Input (any of):
  {"periods": ["scene_2024-03-17", "month_2026-11"], "mode": "force"|"normal"}   manual
  {"s3_key": "curated/_success/satellite/scene_2024-03-17.json"}                 EventBridge
  {"scheduled": true}                                                            daily schedule
Output: {"mode", "count", "periods": [{"period", "sat_period"}]}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import curated as cu
from common import satellite_schema as ss
from common import schema

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _list(s3, bucket: str, prefix: str) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out += page.get("Contents", [])
    return out


def _stem(key: str) -> str:
    return key.rsplit("/", 1)[-1].removesuffix(".json")


def collect(s3, bucket: str) -> dict:
    """Everything PlanMerge needs from S3 (published markers + IoT manifests)."""
    sats = {_stem(o["Key"]): o["LastModified"] for o in _list(s3, bucket, cu.SUCCESS_SATELLITE_PREFIX)}
    bld = None
    try:
        bld = s3.head_object(Bucket=bucket, Key=cu.SUCCESS_BUILDINGS_KEY)["LastModified"]
    except s3.exceptions.ClientError:
        pass
    iot = []   # (date, LastModified) of IoT manifests
    for o in _list(s3, bucket, cu.IOT_MANIFEST_PREFIX):
        try:
            iot.append((schema.parse_batch_id(_stem(o["Key"])).date, o["LastModified"]))
        except ValueError:
            continue
    merged = {}
    for o in _list(s3, bucket, cu.MERGE_MARKER_PREFIX):
        body = json.loads(s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read())
        merged[_stem(o["Key"])] = body
    return {"sats": sats, "buildings": bld, "iot": iot, "merged": merged}


def plan(src: dict, event: dict) -> dict:
    mode = event.get("mode", "normal")
    sats = src["sats"]
    seasons = sorted(b for b in sats if b.startswith("season_"))

    candidates = [b for b in sats if b.startswith(("scene_", "season_"))]
    months = {cu.month_period(d.year, d.month) for d, _ in src["iot"]}
    candidates += sorted(months)

    if event.get("periods"):
        wanted = list(event["periods"])
        for p in wanted:
            cu.parse_merge_period(p)
        candidates = wanted
        mode = event.get("mode", "force")
    only_dep = None
    if event.get("s3_key"):
        only_dep = _stem(event["s3_key"])

    out = []
    for period in sorted(set(candidates)):
        sat_period = cu.sat_period_for(period, seasons)
        if sat_period is None:
            continue
        if only_dep and only_dep != ss.STATIC_BUILDINGS and only_dep != sat_period:
            continue
        mp = cu.parse_merge_period(period)
        if mode == "force" or _stale(src, mp, sat_period):
            out.append({"period": period, "sat_period": sat_period})
    return {"mode": mode, "count": len(out), "periods": out}


def _stale(src: dict, mp: cu.MergePeriod, sat_period: str) -> bool:
    marker = src["merged"].get(mp.period)
    if marker is None:
        return True
    started = datetime.fromisoformat(marker["started_at"])
    if marker.get("sat_period") != sat_period:
        return True
    if src["sats"].get(sat_period) and src["sats"][sat_period] > started:
        return True
    if src["buildings"] and src["buildings"] > started:
        return True
    return any(mp.iot_start <= d <= mp.iot_end and lm > started for d, lm in src["iot"])


def handler(event, context=None):
    result = plan(collect(_client(), os.environ["DATA_BUCKET"]), event or {})
    log.info(json.dumps(result))
    return {"plan": result}
