"""VerifyBatch Lambda (issue #16, spec 30 §4, D21: counts in Python, no Athena).

Checks  raw rows == readings_1s rows + dropped rows  where
  raw rows     = data lines of every raw file in the CheckBatch manifest
  readings_1s  = num_rows from the Parquet footer
  dropped      = sum(duplicate_rows + invalid_rows) of the batch's quality rows
Match    -> write processed/_manifests/<batch_id>.json (the publish marker;
            CheckBatch compares its manifest_hash next hour) -> Published
Mismatch -> raise VerifyMismatch -> NotifyFailure -> PipelineFailed

The manifest records the hash CheckBatch saw, not a fresh listing: a chunk that
lands during the run makes the next run see CHANGED and redo the hour.
"""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import batch_eval, schema

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


class VerifyMismatch(Exception):
    pass


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _read(s3, bucket, key) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def verify(s3, bucket: str, event: dict, now: datetime | None = None) -> dict:
    import pyarrow.parquet as pq

    batch_id = event["batch_id"]
    check = event["check"]

    raw_rows = sum(batch_eval.count_data_lines(_read(s3, bucket, f["key"]))
                   for f in check["manifest"])
    processed = pq.read_metadata(
        io.BytesIO(_read(s3, bucket, schema.s3_key_processed("readings_1s", batch_id)))).num_rows
    quality = pq.read_table(
        io.BytesIO(_read(s3, bucket, schema.s3_key_processed("quality", batch_id))),
        columns=["duplicate_rows", "invalid_rows"]).to_pylist()
    dropped = sum(q["duplicate_rows"] + q["invalid_rows"] for q in quality)

    counts = {"raw": raw_rows, "processed": processed, "dropped": dropped}
    if raw_rows != processed + dropped:
        raise VerifyMismatch(f"{batch_id}: raw {raw_rows} != processed {processed} "
                             f"+ dropped {dropped}")

    manifest = {
        "batch_id": batch_id,
        "manifest_hash": check["manifest_hash"],
        "files": check["manifest"],
        "rows": counts,
        "tables": {t: schema.s3_key_processed(t, batch_id) for t in schema.TABLES},
        "published_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    key = schema.s3_key_manifest(batch_id)
    s3.put_object(Bucket=bucket, Key=key, ContentType="application/json",
                  Body=json.dumps(manifest, indent=1).encode())
    return {"manifest": key, "rows": counts}


def handler(event, context=None):
    result = verify(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "verify": result}
