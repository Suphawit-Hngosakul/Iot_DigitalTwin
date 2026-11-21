"""VerifyBatch Lambda (issue #16, spec 30 §4, D21: counts in Python, no Athena).

Checks  raw rows == trusted rows + quarantined rows + duplicate rows  where
  raw rows     = data lines of every raw file in the CheckBatch manifest
  trusted      = num_rows of trusted/iot_readings_1s (Parquet footer)
  quarantined  = data lines of quarantine/rows/iot/<batch_id>/rows.csv (0 if absent)
  duplicate    = data lines of quarantine/duplicate/iot/<batch_id>/rows.csv (0 if absent)
Match    -> write curated/_success/iot/<batch_id>.json (the publish marker;
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


def _count_csv_rows(s3, bucket, key) -> int:
    """Data rows of a quarantine CSV (header excluded); 0 when the file does not exist."""
    try:
        body = _read(s3, bucket, key)
    except s3.exceptions.NoSuchKey:
        return 0
    return batch_eval.count_data_lines(body)


def verify(s3, bucket: str, event: dict, now: datetime | None = None) -> dict:
    import pyarrow.parquet as pq

    batch_id = event["batch_id"]
    check = event["check"]

    raw_rows = sum(batch_eval.count_data_lines(_read(s3, bucket, f["key"]))
                   for f in check["manifest"])
    trusted = pq.read_metadata(
        io.BytesIO(_read(s3, bucket, schema.s3_key_table("readings_1s", batch_id)))).num_rows
    quarantined = _count_csv_rows(s3, bucket, schema.s3_key_quarantine_rows(batch_id))
    duplicate = _count_csv_rows(s3, bucket, schema.s3_key_quarantine_duplicate(batch_id))

    counts = {"raw": raw_rows, "trusted": trusted, "quarantined": quarantined,
              "duplicate": duplicate}
    if raw_rows != trusted + quarantined + duplicate:
        raise VerifyMismatch(f"{batch_id}: raw {raw_rows} != trusted {trusted} "
                             f"+ quarantined {quarantined} + duplicate {duplicate}")

    manifest = {
        "batch_id": batch_id,
        "manifest_hash": check["manifest_hash"],
        "files": check["manifest"],
        "rows": counts,
        "tables": {t: schema.s3_key_table(t, batch_id) for t in schema.TABLES},
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
