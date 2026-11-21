"""CheckSources (SourcesReady?) — are the trusted inputs of a period published? (spec 60 §2.1)

Needs the satellite marker of `sat_period` and the buildings marker. IoT is optional: without it
the IoT columns are simply null.

Input : {"period", "sat_period"}
Output: the same + {"check": {"ready": bool, "missing": [...], "bldg_year", "started_at"}}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import curated as cu
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _read(s3, bucket: str, key: str):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def check(s3, bucket: str, event: dict, now: datetime | None = None) -> dict:
    period, sat_period = event["period"], event["sat_period"]
    cu.parse_merge_period(period)
    missing, bldg_year = [], None
    if _read(s3, bucket, ss.s3_key_success(sat_period)) is None:
        missing.append(f"satellite:{sat_period}")
    b = _read(s3, bucket, cu.SUCCESS_BUILDINGS_KEY)
    if b is None:
        missing.append("buildings")
    else:
        bldg_year = cu.year_from_trusted_key(b["trusted_key"])
    started = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {**event, "check": {"ready": not missing, "missing": missing,
                               "bldg_year": bldg_year, "started_at": started}}


def handler(event, context=None):
    out = check(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps(out["check"]))
    return out
