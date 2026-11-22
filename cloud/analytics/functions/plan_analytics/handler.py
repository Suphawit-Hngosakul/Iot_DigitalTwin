"""PlanAnalytics — determines which merged periods need S1-S8 statistical analysis."""

from __future__ import annotations

import json
import logging
import os
import re

import boto3

from common import curated as cu
from stats import storage as st

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = None
_MERGE_KEY_RE = re.compile(r"^curated/_success/merge/(?P<period>[^/]+)\.json$")


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _list_markers(s3, bucket: str, prefix: str) -> dict[str, str]:
    """Return {period: LastModified_iso} for all JSON markers under prefix."""
    out: dict[str, str] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            period = key.rsplit("/", 1)[-1][:-5]
            out[period] = obj["LastModified"].isoformat()
    return out


def plan(s3, event: dict, bucket: str) -> dict:
    mode = event.get("mode", "normal")
    periods: list[str] = []

    # Case 1: Explicit periods requested manually
    if event.get("periods"):
        periods = [p for p in event["periods"] if p]
    # Case 2: Triggered by S3 EventBridge when curated/_success/merge/<period>.json is created
    elif event.get("s3_key"):
        m = _MERGE_KEY_RE.match(event["s3_key"])
        if m:
            periods = [m["period"]]
    # Case 3: Scheduled / full scan — find merged periods newer than analytics markers
    else:
        merged = _list_markers(s3, bucket, cu.MERGE_MARKER_PREFIX)
        analyzed = {} if mode == "force" else _list_markers(s3, bucket, f"{st.SUCCESS_ANALYTICS_PREFIX}/")
        for p, m_ts in sorted(merged.items()):
            a_ts = analyzed.get(p)
            if a_ts is None or m_ts > a_ts:
                periods.append(p)

    # Validate period format early
    valid_periods = []
    for p in dict.fromkeys(periods):
        cu.parse_merge_period(p)
        valid_periods.append({"period": p, "mode": mode})

    return {
        **event,
        "plan": {
            "count": len(valid_periods),
            "periods": valid_periods,
        },
    }


def handler(event, context=None):
    out = plan(_client(), event or {}, os.environ["DATA_BUCKET"])
    log.info(json.dumps(out["plan"]))
    return out
