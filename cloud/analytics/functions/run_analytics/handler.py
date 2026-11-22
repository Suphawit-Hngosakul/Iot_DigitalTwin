"""RunAnalytics — computes S1-S8 statistics for one period and writes outputs to S3."""

from __future__ import annotations

import json
import logging
import os

import boto3

from common import curated as cu
from stats import runner, storage as st

log = logging.getLogger()
log.setLevel(logging.INFO)

_athena = _s3 = None


def _clients():
    global _athena, _s3
    if _athena is None:
        _athena, _s3 = boto3.client("athena"), boto3.client("s3")
    return _athena, _s3


def run_for_period(
    athena,
    s3,
    event: dict,
    bucket: str,
    workgroup: str,
    database: str = cu.DATABASE,
) -> dict:
    period = event["period"]
    df = st.load_cell_period_df(athena, s3, period=period, workgroup=workgroup, database=database)
    bundle = runner.build_analytics_outputs(df, period=period)
    marker = st.write_analytics_bundle(s3, bucket=bucket, bundle=bundle)
    return {
        **event,
        "analytics": {
            "status": "PUBLISHED",
            "rows_loaded": int(len(df)),
            "marker": marker,
        },
    }


def handler(event, context=None):
    athena, s3 = _clients()
    out = run_for_period(
        athena,
        s3,
        event,
        bucket=os.environ["DATA_BUCKET"],
        workgroup=os.environ["ATHENA_WORKGROUP"],
        database=os.environ.get("ATHENA_DATABASE", cu.DATABASE),
    )
    log.info(json.dumps(out["analytics"]))
    return out
