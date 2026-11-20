"""Read one satellite-pipeline batch from S3 (spec 51, D23).

A batch is one folder written by satellite_ingest (spec 50):
  raw/satellite/scene_<date>/      pixels.csv, metadata.json, bands.tif, _COMPLETE
  raw/satellite/season_<y>_03-05/  same
  raw/satellite/static=buildings/  buildings_grid.csv, metadata.json, _COMPLETE

Every step re-reads the raw files and re-runs the contract instead of passing
rows through Step Functions (256 KB limit) — the result is deterministic, the
same idea as batch_eval for IoT.

boto3 is passed in (Lambda runtime has it; tests use moto).
"""

from __future__ import annotations

import csv
import io
import json

from . import contract as ct
from . import satellite_schema as ss
from .batch_manifest import manifest_hash  # noqa: F401  (re-exported for handlers)

COMPLETE = "_COMPLETE"
CSV_NAME = {"scene": "pixels.csv", "season": "pixels.csv", "buildings": "buildings_grid.csv"}


def list_files(s3, bucket: str, batch_id: str) -> list[dict]:
    """[{key, etag, size}] of the files directly in the batch folder, sorted by key."""
    prefix = ss.s3_prefix_raw_batch(batch_id)
    files = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "/" in key[len(prefix):]:
                continue
            files.append({"key": key, "etag": obj["ETag"].strip('"'), "size": obj["Size"]})
    files.sort(key=lambda f: f["key"])
    return files


def names(files: list[dict]) -> set[str]:
    return {f["key"].rsplit("/", 1)[-1] for f in files}


def _get(s3, bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def read_json(s3, bucket: str, key: str) -> dict | None:
    try:
        return json.loads(_get(s3, bucket, key))
    except s3.exceptions.NoSuchKey:
        return None


def read_published(s3, bucket: str, batch_id: str) -> dict | None:
    return read_json(s3, bucket, ss.s3_key_success(batch_id))


def load(s3, bucket: str, batch_id: str, files: list[dict] | None = None):
    """(contract, header, rows, metadata, file_names) of a batch."""
    kind = ss.batch_kind(batch_id)
    prefix = ss.s3_prefix_raw_batch(batch_id)
    files = files if files is not None else list_files(s3, bucket, batch_id)
    present = names(files)
    contract = ct.load(ss.contract_source(batch_id))
    metadata = read_json(s3, bucket, prefix + "metadata.json") if "metadata.json" in present else {}
    header: list[str] = []
    rows: list[dict] = []
    if CSV_NAME[kind] in present:
        text = _get(s3, bucket, prefix + CSV_NAME[kind]).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        header = list(reader.fieldnames or [])
        rows = list(reader)
    return contract, header, rows, metadata or {}, present


def evaluate(s3, bucket: str, batch_id: str, files: list[dict] | None = None):
    """(contract.Result, metadata) — the contract run every step relies on."""
    contract, header, rows, metadata, present = load(s3, bucket, batch_id, files)
    return ct.evaluate(contract, header, rows, present, metadata), metadata


def to_csv(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    cols = list(rows[0].keys())
    for r in rows[1:]:
        cols += [k for k in r if k not in cols]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def buildings_year(metadata: dict) -> int:
    """Year a static building snapshot belongs to (from metadata.processed_at)."""
    ts = metadata.get("processed_at") or ""
    if len(ts) < 4 or not ts[:4].isdigit():
        raise ValueError("buildings metadata.json has no processed_at")
    return int(ts[:4])
