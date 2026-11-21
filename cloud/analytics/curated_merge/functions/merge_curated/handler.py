"""MergeCurated — Athena MERGE INTO Iceberg iot_walking.cell_period (spec 60 §2.1).

CREATE TABLE IF NOT EXISTS, then one MERGE for both match sets of the period (key = cell_id,
period, match_type). Safe to repeat: matched rows are overwritten.
Stationary device ids (IoT window of scenes, spec 60 §2) come from devices.csv.

Input : CheckSources output. Output: the same + {"merge": {"query_id"}}
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from common import curated as cu
from common import schema

log = logging.getLogger()
log.setLevel(logging.INFO)

_athena = None


def _client():
    global _athena
    if _athena is None:
        _athena = boto3.client("athena")
    return _athena


def stationary_ids() -> list[str]:
    return sorted({d.device_id for d in schema.load_devices() if d.device_type == "Stationary"})


def merge(athena, event: dict, bucket: str, workgroup: str, database: str = cu.DATABASE) -> dict:
    chk = event["check"]
    if not chk["ready"]:
        raise RuntimeError(f"{event['period']}: sources not ready: {chk['missing']}")
    cu.run_query(athena, cu.build_create_table(bucket), workgroup, database)
    sql = cu.build_merge(event["period"], event["sat_period"], chk["bldg_year"], stationary_ids())
    qid = cu.run_query(athena, sql, workgroup, database)
    return {**event, "merge": {"query_id": qid}}


def handler(event, context=None):
    out = merge(_client(), event, os.environ["DATA_BUCKET"], os.environ["ATHENA_WORKGROUP"],
                os.environ.get("ATHENA_DATABASE", cu.DATABASE))
    log.info(json.dumps(out["merge"]))
    return out
