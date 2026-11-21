"""VerifyCurated — checks the merged period, then writes curated/_success/merge/<period>.json.

Checks (spec 60 §2.1): both match sets exist, rows per set = cells of the satellite period,
no duplicate cell_id. Coverage of each column goes into the marker (report, not a failure).
A mismatch raises -> PipelineFailed (SNS).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import curated as cu

log = logging.getLogger()
log.setLevel(logging.INFO)

_athena = _s3 = None


def _clients():
    global _athena, _s3
    if _athena is None:
        _athena, _s3 = boto3.client("athena"), boto3.client("s3")
    return _athena, _s3


class VerifyError(RuntimeError):
    pass


def verify(athena, s3, event: dict, bucket: str, workgroup: str,
           database: str = cu.DATABASE, now: datetime | None = None) -> dict:
    period, sat_period = event["period"], event["sat_period"]
    expected = cu.fetch_rows(athena, cu.run_query(athena, cu.build_sat_cell_count(sat_period),
                                                  workgroup, database))[0]["n"]
    rows = cu.fetch_rows(athena, cu.run_query(athena, cu.build_verify_query(period), workgroup, database))
    problems = cu.verify_counts(rows, expected)
    if problems:
        raise VerifyError(f"{period}: {'; '.join(problems)}")
    marker = {
        "period": period,
        "sat_period": sat_period,
        "bldg_year": event["check"]["bldg_year"],
        "started_at": event["check"]["started_at"],
        "published_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "cells": expected,
        "coverage": {r["match_type"]: {k: v for k, v in r.items() if k != "match_type"} for r in rows},
    }
    s3.put_object(Bucket=bucket, Key=cu.key_merge_marker(period), ContentType="application/json",
                  Body=json.dumps(marker, ensure_ascii=False, indent=1).encode())
    return marker


def handler(event, context=None):
    athena, s3 = _clients()
    marker = verify(athena, s3, event, os.environ["DATA_BUCKET"], os.environ["ATHENA_WORKGROUP"],
                    os.environ.get("ATHENA_DATABASE", cu.DATABASE))
    log.info(json.dumps(marker))
    return {**event, "verify": {"status": "PUBLISHED", "marker_key": cu.key_merge_marker(event["period"])}}
