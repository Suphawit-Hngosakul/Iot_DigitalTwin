"""Athena reader for iot_walking.cell_period and S3 writer for curated/analytics/."""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from common import curated as cu

ANALYTICS_PREFIX = "curated/analytics"
SUCCESS_ANALYTICS_PREFIX = "curated/_success/analytics"
# Lambda run_analytics times out at 600 s; stop waiting on Athena early enough to fail cleanly.
QUERY_TIMEOUT_S = 480.0


def key_analytics_marker(period: str) -> str:
    cu.parse_merge_period(period)
    return f"{SUCCESS_ANALYTICS_PREFIX}/{period}.json"


def load_cell_period_df(
    athena,
    s3,
    period: str,
    workgroup: str,
    database: str = cu.DATABASE,
) -> pd.DataFrame:
    """Query all rows for `period` from iot_walking.cell_period without 1,000-row truncation."""
    cu.parse_merge_period(period)
    sql = (
        f"SELECT * FROM {database}.{cu.TABLE} "
        f"WHERE period = '{period}' AND in_cover = true"
    )
    try:
        qid = cu.run_query(athena, sql, workgroup, database, timeout_s=QUERY_TIMEOUT_S)
    except cu.AthenaError as exc:
        # curated-merge creates cell_period on its first MERGE; before that there is simply no data
        if "TABLE_NOT_FOUND" in str(exc):
            return pd.DataFrame()
        raise
    exec_info = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
    out_uri = exec_info.get("ResultConfiguration", {}).get("OutputLocation", "")

    if out_uri.startswith("s3://"):
        parsed = urlparse(out_uri)
        obj = s3.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
        raw_bytes = obj["Body"].read()
        if not raw_bytes:
            return pd.DataFrame()
        df = pd.read_csv(io.BytesIO(raw_bytes))
    else:
        rows = cu.fetch_rows(athena, qid)
        df = pd.DataFrame(rows)

    if "in_cover" in df.columns:
        df["in_cover"] = df["in_cover"].map(
            lambda v: True if str(v).lower() in ("true", "1", "t") else False
        )
    return df


def existing_period_keys(s3, bucket: str, period: str) -> list[str]:
    """Every object of an earlier run of `period`: curated/analytics/<report>/period=<p>/... + its marker."""
    cu.parse_merge_period(period)
    paginator = s3.get_paginator("list_objects_v2")
    report_dirs: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{ANALYTICS_PREFIX}/", Delimiter="/"):
        report_dirs += [cp["Prefix"] for cp in page.get("CommonPrefixes", [])]
    keys: list[str] = []
    for d in report_dirs:
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{d}period={period}/"):
            keys += [o["Key"] for o in page.get("Contents", [])]
    for page in paginator.paginate(Bucket=bucket, Prefix=key_analytics_marker(period)):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"] == key_analytics_marker(period)]
    return keys


def clear_period(s3, bucket: str, period: str) -> int:
    """Delete the previous results of `period` so a rerun replaces the whole set (Spec 60 §4)."""
    keys = existing_period_keys(s3, bucket, period)
    for i in range(0, len(keys), 1000):
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True},
        )
    return len(keys)


def write_analytics_bundle(
    s3,
    bucket: str,
    bundle: dict,
    now: datetime | None = None,
) -> dict:
    """Write Parquet tables, ready-to-serve JSON payloads, and the _success marker."""
    period = bundle["period"]
    written_keys: list[str] = []
    # 0. Remove the earlier run first: a table that is empty this time must not keep last run's file
    clear_period(s3, bucket, period)

    # 1. Ready-to-serve JSON files for API (#34) & Web (#37)
    for report_name, by_match in bundle["json"].items():
        for match_type, payload in by_match.items():
            key = f"{ANALYTICS_PREFIX}/{report_name}/period={period}/match={match_type}.json"
            s3.put_object(
                Bucket=bucket,
                Key=key,
                ContentType="application/json",
                Body=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            )
            written_keys.append(key)

    # 2. Parquet tables (Spec 60 §4)
    for table_name, df in bundle["tables"].items():
        if df is None or df.empty:
            continue
        buf = io.BytesIO()
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, buf, compression="snappy")
        key = f"{ANALYTICS_PREFIX}/{table_name}/period={period}/part.parquet"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            ContentType="application/octet-stream",
            Body=buf.getvalue(),
        )
        written_keys.append(key)

    # 3. Completion marker written last
    marker = {
        "period": period,
        "published_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "reports": sorted(bundle["json"].keys()),
        "files_written": len(written_keys),
    }
    marker_key = key_analytics_marker(period)
    s3.put_object(
        Bucket=bucket,
        Key=marker_key,
        ContentType="application/json",
        Body=json.dumps(marker, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return marker
