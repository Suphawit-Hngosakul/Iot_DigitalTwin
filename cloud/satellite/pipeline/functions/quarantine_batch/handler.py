"""QuarantineBatch — satellite pipeline (issue #30, spec 51 §3).

Copies every raw file of a FAILed batch to quarantine/batch/<source>/<batch_id>/
and writes reason.json there. Raw is never deleted: fix the cause, then re-run
with mode=force (or let satellite_ingest rewrite the folder).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import satellite_schema as ss

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
    prefix = ss.s3_prefix_quarantine_batch(batch_id)
    manifest = event["check"]["manifest"]
    for f in manifest:
        s3.copy_object(Bucket=bucket, Key=prefix + f["key"].rsplit("/", 1)[-1],
                       CopySource={"Bucket": bucket, "Key": f["key"]})
    validation = event.get("validation", {})
    reason = {
        "batch_id": batch_id,
        "reason": validation.get("reason"),
        "report_key": validation.get("report_key"),
        "quarantined_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "manifest_hash": event["check"].get("manifest_hash"),
        "source_files": [f["key"] for f in manifest],
    }
    s3.put_object(Bucket=bucket, Key=prefix + "reason.json", ContentType="application/json",
                  Body=json.dumps(reason, ensure_ascii=False, indent=1).encode())
    return {"prefix": prefix, "files": len(manifest)}


def handler(event, context=None):
    result = quarantine(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "quarantine": result}
