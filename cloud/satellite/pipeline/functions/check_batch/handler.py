"""CheckBatch — satellite pipeline (issue #30, spec 51 §3).

Input : {"s3_key": "raw/satellite/scene_2026-04-12/_COMPLETE", "mode": "normal"}   (EventBridge)
     or {"batch_id": "scene_2026-04-12", "mode": "normal" | "force"}             (manual)
Output: the input plus batch_id and
        {"check": {"status", "kind", "manifest": [{key, etag, size}], "manifest_hash", "reason"}}

status:
  INVALID_INPUT      batch_id / key malformed or unknown mode
  NO_DATA            folder empty, _COMPLETE not written yet, or the date is in skipped/
  ALREADY_PROCESSED  manifest hash equals the published one (mode != force)
  NEW                never published
  CHANGED            published, but files changed since (e.g. force rebuild by ingest), or mode == force
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from common import satellite_batch as sb
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

MODES = ("normal", "force")

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def resolve_batch_id(event: dict) -> str:
    if event.get("batch_id"):
        bid = event["batch_id"]
        ss.batch_kind(bid)
        return bid
    if event.get("s3_key"):
        return ss.batch_id_from_key(event["s3_key"])
    raise ValueError("need batch_id or s3_key")


def check(s3, bucket: str, batch_id: str, mode: str) -> dict:
    kind = ss.batch_kind(batch_id)
    out = {"kind": kind}
    skipped = ss.s3_key_raw_skipped(batch_id)
    if skipped and sb.exists(s3, bucket, skipped):
        return {"status": "NO_DATA", "reason": f"date skipped by ingest ({skipped})", **out}
    files = sb.list_files(s3, bucket, batch_id)
    if sb.COMPLETE not in sb.names(files):
        return {"status": "NO_DATA", "manifest": files, "manifest_hash": None,
                "reason": "no _COMPLETE (ingest not finished)", **out}
    digest = sb.manifest_hash(files)
    out.update(manifest=files, manifest_hash=digest)
    published = sb.read_published(s3, bucket, batch_id)
    if published is None:
        return {"status": "NEW", **out}
    if mode == "force":
        return {"status": "CHANGED", "reason": "mode=force", **out}
    if published.get("manifest_hash") == digest:
        return {"status": "ALREADY_PROCESSED", **out}
    return {"status": "CHANGED", "reason": "raw files differ from published manifest", **out}


def handler(event, context=None):
    mode = event.get("mode") or "normal"
    try:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        batch_id = resolve_batch_id(event)
    except ValueError as e:
        result = {"status": "INVALID_INPUT", "reason": str(e)}
        log.info(json.dumps({"event": event, **result}))
        return {**event, "mode": mode, "check": result}
    result = check(_client(), os.environ["DATA_BUCKET"], batch_id, mode)
    log.info(json.dumps({"batch_id": batch_id, "mode": mode, "status": result["status"],
                         "files": len(result.get("manifest", [])), "reason": result.get("reason")}))
    return {**event, "batch_id": batch_id, "mode": mode, "check": result}
