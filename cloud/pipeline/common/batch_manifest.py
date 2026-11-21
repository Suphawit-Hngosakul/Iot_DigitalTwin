"""Raw-file manifest of a batch and the published manifest (spec 30 §4).

A batch's manifest is the sorted list of raw chunk files under its prefix,
each with its ETag. Its hash tells CheckBatch whether anything changed since
the batch was last published (VerifyBatch writes the same hash).

boto3 is passed in (Lambda runtime has it; tests use moto).
"""

from __future__ import annotations

import hashlib
import json

from common import schema

RAW_SUFFIX = ".csv"


def list_raw_files(s3, bucket: str, batch_id: str) -> list[dict]:
    """[{key, etag, size}] of the batch's raw CSV chunks, sorted by key."""
    prefix = schema.raw_prefix(batch_id)
    files = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Only direct children ending in .csv (no sub-folders, no markers).
            if not key.endswith(RAW_SUFFIX) or "/" in key[len(prefix):]:
                continue
            files.append({"key": key, "etag": obj["ETag"].strip('"'), "size": obj["Size"]})
    files.sort(key=lambda f: f["key"])
    return files


def manifest_hash(files: list[dict]) -> str:
    """sha256 over "key<TAB>etag" lines in key order (size is not part of it)."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f["key"]):
        h.update(f"{f['key']}\t{f['etag']}\n".encode())
    return h.hexdigest()


def read_published(s3, bucket: str, batch_id: str) -> dict | None:
    """curated/_success/iot/<batch_id>.json, or None if never published."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=schema.s3_key_manifest(batch_id))
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(obj["Body"].read())
