"""CheckBatch Lambda (issue #12, spec 30 §4).

Input : {"batch_id": "IOTW-A001__2026-11-16__14", "mode": "normal" | "force"}
Output: the input plus
        {"check": {"status": ..., "manifest": [{key, etag, size}], "manifest_hash": ...,
                   "reason": ...}}

status:
  INVALID_INPUT      batch_id malformed or unknown mode
  NO_DATA            no raw file under the batch prefix
  ALREADY_PROCESSED  manifest hash equals the published one (mode != force)
  ALREADY_QUARANTINED manifest hash equals the one QuarantineBatch saw last time
                     (mode != force): the same files would fail again, so skip
  NEW                never published
  CHANGED            published, but files were added/replaced since (e.g. backfill),
                     or mode == force

NO_DATA / ALREADY_PROCESSED / ALREADY_QUARANTINED mean the current raw files need no
more work, so the batch's pending marker is removed -- only if it is older than this
listing: a chunk that lands meanwhile writes a newer marker, which is kept.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from common import batch_manifest, schema

log = logging.getLogger()
log.setLevel(logging.INFO)

MODES = ("normal", "force")

INVALID_INPUT = "INVALID_INPUT"
NO_DATA = "NO_DATA"
ALREADY_PROCESSED = "ALREADY_PROCESSED"
ALREADY_QUARANTINED = "ALREADY_QUARANTINED"
NEW = "NEW"
CHANGED = "CHANGED"
DONE = (NO_DATA, ALREADY_PROCESSED, ALREADY_QUARANTINED)
# S3 LastModified has 1 s resolution; Lambda and S3 clocks may differ a little
PENDING_MARGIN = timedelta(seconds=5)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def check(s3, bucket: str, batch_id, mode, now: datetime | None = None) -> dict:
    """Decide what to do with one batch, then drop its pending marker if it is done."""
    listed_at = now or datetime.now(timezone.utc)
    result = _decide(s3, bucket, batch_id, mode)
    if result["status"] in DONE:
        result["pending_cleared"] = clear_pending(s3, bucket, batch_id, listed_at)
    return result


def clear_pending(s3, bucket: str, batch_id: str, listed_at: datetime) -> bool:
    """Delete the batch's pending marker if it was written before the raw listing."""
    key = schema.s3_key_pending(batch_id)
    try:
        written = s3.head_object(Bucket=bucket, Key=key)["LastModified"]
    except ClientError:
        return False
    if written >= listed_at - PENDING_MARGIN:
        return False
    s3.delete_object(Bucket=bucket, Key=key)
    return True


def _decide(s3, bucket: str, batch_id, mode) -> dict:
    """Decide what to do with one batch. Never raises for bad input."""
    if mode not in MODES:
        return {"status": INVALID_INPUT, "reason": f"unknown mode {mode!r}"}
    if not isinstance(batch_id, str):
        return {"status": INVALID_INPUT, "reason": "batch_id missing"}
    try:
        schema.parse_batch_id(batch_id)
    except ValueError as e:
        return {"status": INVALID_INPUT, "reason": str(e)}

    files = batch_manifest.list_raw_files(s3, bucket, batch_id)
    if not files:
        return {"status": NO_DATA, "manifest": [], "manifest_hash": None}

    digest = batch_manifest.manifest_hash(files)
    out = {"manifest": files, "manifest_hash": digest}
    published = batch_manifest.read_published(s3, bucket, batch_id)
    if mode != "force":
        if published is not None and published.get("manifest_hash") == digest:
            return {"status": ALREADY_PROCESSED, **out}
        if _quarantined_same(s3, bucket, batch_id, digest):
            return {"status": ALREADY_QUARANTINED,
                    "reason": "same raw files already failed validation (mode=force to retry)",
                    **out}
    if published is None:
        return {"status": NEW, **out}
    if mode == "force":
        return {"status": CHANGED, "reason": "mode=force", **out}
    return {"status": CHANGED, "reason": "raw files differ from published manifest", **out}


def _quarantined_same(s3, bucket: str, batch_id: str, digest: str) -> bool:
    q = batch_manifest.read_quarantined(s3, bucket, batch_id)
    return q is not None and q.get("manifest_hash") == digest


def handler(event, context=None):
    batch_id = event.get("batch_id")
    mode = event.get("mode") or "normal"
    result = check(_client(), os.environ["DATA_BUCKET"], batch_id, mode)
    log.info(json.dumps({
        "batch_id": batch_id, "mode": mode, "status": result["status"],
        "files": len(result.get("manifest", [])), "reason": result.get("reason"),
    }))
    return {**event, "batch_id": batch_id, "mode": mode, "check": result}
