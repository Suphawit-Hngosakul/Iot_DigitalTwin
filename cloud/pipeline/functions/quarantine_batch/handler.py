"""QuarantineBatch Lambda (issue #14, spec 30 §4).

Copies every raw file in the manifest to quarantine/batch/iot/<batch_id>/<file name>
and writes reason.json there (with the manifest_hash, so CheckBatch skips the same
files next hour: ALREADY_QUARANTINED). Raw is never deleted, so the batch can be
fixed and re-run (mode=force).

If an earlier version of the batch was published, its outputs (the 3 Parquet files,
quarantine/rows|duplicate and the publish marker) are removed: the API must not keep
serving an hour whose current raw files fail validation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import schema

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def quarantine(s3, bucket: str, event: dict, now: datetime | None = None) -> dict:
    batch_id = event["batch_id"]
    prefix = schema.s3_prefix_quarantine(batch_id)
    copied = []
    for f in event["check"]["manifest"]:
        dest = prefix + f["key"].rsplit("/", 1)[-1]
        s3.copy_object(Bucket=bucket, Key=dest, CopySource={"Bucket": bucket, "Key": f["key"]})
        copied.append(dest)
    validation = event.get("validation", {})
    reason = {
        "batch_id": batch_id,
        "reason": validation.get("reason"),
        "report": validation.get("report"),
        "quarantined_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "manifest_hash": event["check"].get("manifest_hash"),
        "source_files": [f["key"] for f in event["check"]["manifest"]],
    }
    s3.put_object(Bucket=bucket, Key=prefix + "reason.json", ContentType="application/json",
                  Body=json.dumps(reason, ensure_ascii=False, indent=1).encode())
    return {"prefix": prefix, "files": len(copied), "unpublished": unpublish(s3, bucket, batch_id)}


def published_keys(batch_id: str) -> list[str]:
    """Every object a successful run writes for the batch (marker last)."""
    return [*(schema.s3_key_table(t, batch_id) for t in schema.TABLES),
            schema.s3_key_quarantine_rows(batch_id),
            schema.s3_key_quarantine_duplicate(batch_id),
            schema.s3_key_manifest(batch_id)]


def unpublish(s3, bucket: str, batch_id: str) -> list[str]:
    """Delete the outputs of an earlier successful run; returns the keys that existed."""
    removed = []
    for key in published_keys(batch_id):
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.ClientError:
            continue
        s3.delete_object(Bucket=bucket, Key=key)
        removed.append(key)
    return removed


def handler(event, context=None):
    result = quarantine(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "quarantine": result}
