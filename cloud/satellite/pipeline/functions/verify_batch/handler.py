"""VerifyBatch — satellite pipeline (issue #31, spec 51 §3).

Checks, then writes the publish marker curated/_success/<source>/<batch_id>.json:
  1. raw folder unchanged since CheckBatch (same manifest hash)
  2. raw rows = trusted-input rows + quarantine/rows + quarantine/duplicate
  3. trusted Parquet num_rows = number of AOI cells (scene/season) or CSV rows (buildings)
  4. mean of cell lst_mean within MAX_LST_DIFF_C of the mean of the same cells' pixels
  5. every layer file exists
Any mismatch raises -> PipelineFailed (SNS).
"""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from common import satellite_batch as sb
from common import satellite_grid as sg
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_LST_DIFF_C = 0.5
LAYER_FILES = {
    "buildings": ["building_density.png", "building_density.json"],
    "pixels": [f"{n}.{e}" for n in ("lst", "ndvi", "ndbi") for e in ("png", "json")],
}

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


class VerifyError(RuntimeError):
    pass


def _parquet_rows(s3, bucket: str, key: str) -> int:
    import pyarrow.parquet as pq

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pq.ParquetFile(io.BytesIO(body)).metadata.num_rows


def lst_diff(pixels: list[dict], cells: list[dict]) -> float | None:
    """|mean(cell lst_mean) - mean(valid pixel lst over those same cells)| in °C."""
    kept = {c["cell_id"] for c in cells if c.get("lst_mean") is not None}
    if not kept:
        return None
    by_cell = sg.group_by_cell(pixels)
    kind = ss.parse_period(cells[0]["period"]).period_type
    cell_means = [c["lst_mean"] for c in cells if c["cell_id"] in kept]
    px = [p["lst_c"] for cid in kept for p in by_cell.get(cid, []) if sg.pixel_is_valid(p, kind)]
    return abs(sum(cell_means) / len(cell_means) - sum(px) / len(px))


def verify(s3, bucket: str, event: dict, now: datetime | None = None) -> dict:
    batch_id = event["batch_id"]
    kind = ss.batch_kind(batch_id)
    check = event["check"]

    files = sb.list_files(s3, bucket, batch_id)
    if sb.manifest_hash(files) != check["manifest_hash"]:
        raise VerifyError(f"{batch_id}: raw folder changed during the run; re-run")

    res, metadata = sb.evaluate(s3, bucket, batch_id, files)
    split = len(res.rows) + len(res.rejected) + len(res.duplicates)
    if split != res.total_rows:
        raise VerifyError(f"{batch_id}: raw rows {res.total_rows} != kept+rejected+duplicate {split}")

    trusted_key = event["trusted"]["key"]
    n = _parquet_rows(s3, bucket, trusted_key)
    if kind == "buildings":
        want = len(res.rows)
        diff = None
    else:
        cells = sg.aggregate_pixels(res.rows, batch_id)
        want = len(cells)
        diff = lst_diff(res.rows, cells)
        if diff is not None and diff > MAX_LST_DIFF_C:
            raise VerifyError(f"{batch_id}: cell LST mean differs from pixels by {diff:.2f} °C")
        bad = [(c["cell_id"], p) for c in cells for p in ss.check_satellite_row(c)]
        if bad:
            raise VerifyError(f"{batch_id}: bad satellite_grid rows, e.g. {bad[:3]}")
    if n != want:
        raise VerifyError(f"{batch_id}: {trusted_key} has {n} rows, expected {want}")

    prefix = ss.s3_prefix_layers(batch_id)
    missing = [f for f in LAYER_FILES["buildings" if kind == "buildings" else "pixels"]
               if not sb.exists(s3, bucket, prefix + f)]
    if missing:
        raise VerifyError(f"{batch_id}: missing layer files {missing}")

    marker = {
        "batch_id": batch_id,
        "kind": kind,
        "manifest_hash": check["manifest_hash"],
        "published_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "raw_rows": res.total_rows,
        "trusted_rows": n,
        "quarantined_rows": len(res.rejected),
        "duplicate_rows": len(res.duplicates),
        "trusted_key": trusted_key,
        "layers_prefix": prefix,
        "lst_diff_c": None if diff is None else round(diff, 4),
        "contract_version": res.contract_version,
        "valid_ratio": metadata.get("valid_ratio"),
    }
    s3.put_object(Bucket=bucket, Key=ss.s3_key_success(batch_id), ContentType="application/json",
                  Body=json.dumps(marker, ensure_ascii=False, indent=1).encode())
    return marker


def handler(event, context=None):
    result = verify(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps(result))
    return {**event, "verify": {"status": "PUBLISHED", "success_key": ss.s3_key_success(event["batch_id"])}}
