"""ValidateBatch — satellite pipeline (issue #30, spec 51 §3, spec 32).

Runs the batch's data contract (contracts/satellite_scene|satellite_season|buildings)
and writes validation/<source>/<batch_id>/report.json. On PASS, rejected rows go to
quarantine/rows/ and repeated keys to quarantine/duplicate/ (the batch continues);
on FAIL the batch goes to QuarantineBatch.

Output: the input plus {"validation": {"status": "PASS"|"FAIL", "reason", "report_key",
        "total_rows", "passed", "quarantined_rows", "duplicate_rows"}}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import satellite_batch as sb
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def validate(s3, bucket: str, event: dict, now: datetime | None = None) -> dict:
    batch_id = event["batch_id"]
    res, _ = sb.evaluate(s3, bucket, batch_id, event["check"]["manifest"])
    generated = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    report = res.report(batch_id, generated)
    report_key = ss.s3_key_validation(batch_id)
    s3.put_object(Bucket=bucket, Key=report_key, ContentType="application/json",
                  Body=json.dumps(report, ensure_ascii=False, indent=1, default=str).encode())
    if res.status == "PASS":
        for key, rows in ((ss.s3_key_quarantine_rows(batch_id), res.rejected),
                          (ss.s3_key_quarantine_duplicate(batch_id), res.duplicates)):
            if rows:
                s3.put_object(Bucket=bucket, Key=key, ContentType="text/csv", Body=sb.to_csv(rows))
    return {
        "status": res.status,
        "reason": "; ".join(res.fail_reasons) or None,
        "report_key": report_key,
        "total_rows": res.total_rows,
        "passed": len(res.rows),
        "quarantined_rows": len(res.rejected),
        "duplicate_rows": len(res.duplicates),
    }


def handler(event, context=None):
    result = validate(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "validation": result}
