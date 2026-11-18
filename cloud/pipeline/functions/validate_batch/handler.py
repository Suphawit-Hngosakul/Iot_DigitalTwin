"""ValidateBatch Lambda (issue #13, spec 30 §4).

Input : CheckBatch output ({"batch_id", "mode", "check": {"manifest": [...], ...}})
Output: input + {"validation": {"status": "PASS" | "FAIL", "reason", "report",
                                "raw_rows", "kept_rows", "invalid_ratio"}}
Writes validation/<batch_id>/report.json (always, PASS or FAIL).

FAIL (batch-level): bad header, Device_ID != path, device not in devices.csv,
Device_Type != registry, station rows outside registry validity, invalid rows
> 20 % (duplicates excluded), header-only files. Row problems are flags only.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import batch_eval, schema

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def validate(s3, bucket: str, event: dict, devices, now: datetime | None = None) -> dict:
    batch_id = event["batch_id"]
    files = batch_eval.read_files(s3, bucket, event["check"]["manifest"])
    ev = batch_eval.evaluate(batch_id, files, devices)
    rep = batch_eval.report(ev, now or datetime.now(timezone.utc))
    key = schema.s3_key_report(batch_id)
    s3.put_object(Bucket=bucket, Key=key, ContentType="application/json",
                  Body=json.dumps(rep, ensure_ascii=False, indent=1, default=str).encode())
    return {
        "status": ev.status,
        "reason": "; ".join(ev.fail_reasons) or None,
        "report": key,
        "raw_rows": ev.raw_rows,
        "kept_rows": ev.count("kept"),
        "invalid_ratio": round(ev.invalid_ratio, 4),
    }


def handler(event, context=None):
    result = validate(_client(), os.environ["DATA_BUCKET"], event, schema.load_devices())
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "validation": result}
