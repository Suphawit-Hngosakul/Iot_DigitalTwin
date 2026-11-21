"""WriteTrusted — satellite pipeline (issue #31, spec 51 §4).

scene / season : pixels.csv (30 m) -> one satellite_grid row per 90 m AOI cell
                 -> trusted/satellite_grid/period_type=<t>/year=<y>/<period>.parquet
buildings      : buildings_grid.csv (already per cell)
                 -> trusted/buildings_grid/year=<y>/buildings_<y>.parquet

One file per batch, overwritten whole (idempotent). Needs pyarrow (pandas layer).
Output: the input plus {"trusted": {"key", "rows"}}
"""

from __future__ import annotations

import io
import json
import logging
import os

import boto3

from common import satellite_batch as sb
from common import satellite_grid as sg
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def build(s3, bucket: str, event: dict) -> tuple[str, str, list[dict]]:
    """(table, key, rows) for a batch — shared with VerifyBatch."""
    batch_id = event["batch_id"]
    res, metadata = sb.evaluate(s3, bucket, batch_id, event["check"]["manifest"])
    if res.status != "PASS":
        raise RuntimeError(f"{batch_id}: contract no longer passes: {res.fail_reasons}")
    if ss.batch_kind(batch_id) == "buildings":
        year = sb.buildings_year(metadata)
        return "buildings_grid", ss.s3_key_buildings_grid(year), sg.buildings_rows(res.rows)
    return "satellite_grid", ss.s3_key_satellite_grid(batch_id), sg.aggregate_pixels(res.rows, batch_id)


def parquet_bytes(table: str, rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = ss.arrow_schema(table)
    data = {name: [r.get(name) for r in rows] for name in schema.names}
    buf = io.BytesIO()
    pq.write_table(pa.table(data, schema=schema), buf, compression="snappy")
    return buf.getvalue()


def write(s3, bucket: str, event: dict) -> dict:
    table, key, rows = build(s3, bucket, event)
    if not rows:
        raise RuntimeError(f"{event['batch_id']}: no rows to write")
    s3.put_object(Bucket=bucket, Key=key, Body=parquet_bytes(table, rows),
                  ContentType="application/vnd.apache.parquet")
    return {"table": table, "key": key, "rows": len(rows)}


def handler(event, context=None):
    result = write(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "trusted": result}
