"""PlanBatches Lambda (spec 30 §2, D21) — first state: builds the list for Map.

Scheduled run (EventBridge Scheduler input):
    {"mode": "normal", "scheduled_time": "<aws.scheduler.scheduled-time>"}
  -> every device in devices.csv x Thai-local hours H-1 .. H-LOOKBACK_HOURS,
     only the hours that have raw files (H = scheduled hour; falls back to now).
Manual run:
    {"batch_ids": ["IOTW-A001__2026-11-16__14", ...], "mode": "normal" | "force"}
  -> exactly those, unfiltered (CheckBatch reports bad ids / no data).

Output: {"mode": ..., "count": n, "batches": [{"batch_id": ..., "mode": ...}, ...]}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

from common import schema

log = logging.getLogger()
log.setLevel(logging.INFO)

DEFAULT_LOOKBACK_HOURS = 24

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def scheduled_hour_local(scheduled_time: str | None, now: datetime | None = None) -> datetime:
    """Thai-local, naive, truncated to the hour (matches raw date=/hour=).

    An unsubstituted Scheduler placeholder ("<aws.scheduler...>") falls back
    to now, so a misconfigured schedule still processes data.
    """
    if scheduled_time and scheduled_time.startswith("<"):
        log.warning("scheduled_time not substituted (%s); using now", scheduled_time)
        scheduled_time = None
    if scheduled_time:
        ts = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = now or datetime.now(timezone.utc)
    local = ts.astimezone(schema.LOCAL_TZ).replace(tzinfo=None)
    return local.replace(minute=0, second=0, microsecond=0)


def lookback_hours(h: datetime, n: int) -> list[datetime]:
    """H-n .. H-1, oldest first."""
    return [h - timedelta(hours=k) for k in range(n, 0, -1)]


def hours_with_data(s3, bucket: str, device_id: str, day: str) -> set[int]:
    """Hours that have at least one object under raw/csv/device_id=<id>/date=<day>/."""
    prefix = f"raw/csv/device_id={device_id}/date={day}/"
    found = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            part = cp["Prefix"][len(prefix):].rstrip("/")      # "hour=14"
            if part.startswith("hour=") and part[5:].isdigit():
                found.add(int(part[5:]))
    return found


def active_in_hour(d, hour: datetime) -> bool:
    """Registry row overlaps [hour, hour + 1h) (valid_to exclusive)."""
    return d.valid_from < hour + timedelta(hours=1) and (
        d.valid_to is None or d.valid_to > hour)


def plan(s3, bucket: str, devices, h: datetime, n: int) -> list[str]:
    """batch_ids with raw data in the lookback window, oldest hour first."""
    candidates = [(hour, d.device_id)
                  for hour in lookback_hours(h, n)
                  for d in devices if active_in_hour(d, hour)]
    # one S3 listing per device per day, not per hour
    cache: dict[tuple[str, str], set[int]] = {}
    out = []
    for hour, device_id in candidates:
        day = hour.date().isoformat()
        if (device_id, day) not in cache:
            cache[(device_id, day)] = hours_with_data(s3, bucket, device_id, day)
        if hour.hour in cache[(device_id, day)]:
            out.append(schema.make_batch_id(device_id, hour.date(), hour.hour))
    return out


def handler(event, context=None):
    mode = event.get("mode") or "normal"
    if event.get("batch_ids") is not None:
        ids = list(event["batch_ids"])
        source = "manual"
    else:
        n = int(os.environ.get("LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS))
        h = scheduled_hour_local(event.get("scheduled_time"))
        ids = plan(_client(), os.environ["DATA_BUCKET"], schema.load_devices(), h, n)
        source = f"schedule H={h.isoformat(sep=' ')} lookback={n}"
    log.info(json.dumps({"source": source, "mode": mode, "count": len(ids)}))
    return {
        "mode": mode,
        "count": len(ids),
        "batches": [{"batch_id": b, "mode": mode} for b in ids],
    }
