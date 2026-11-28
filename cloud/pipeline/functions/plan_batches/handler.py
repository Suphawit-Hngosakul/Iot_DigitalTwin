"""PlanBatches Lambda (spec 30 §2, D21) — first state: builds the list for Map.

Scheduled run (EventBridge Scheduler input):
    {"mode": "normal", "scheduled_time": "<aws.scheduler.scheduled-time>"}
  -> every batch with a pending marker (raw/_pending/iot/<batch_id>, written by ingest
     after the raw files; CheckBatch removes it once the files are confirmed processed),
     however old, plus every device in devices.csv x Thai-local hours
     H-1 .. H-LOOKBACK_HOURS that have raw files (safety net for a lost marker).
     Hour H itself is left out (still being written). Oldest hour first, at most
     MAX_BATCHES per run; the rest stays pending for the next run.
Manual run:
    {"batch_ids": ["IOTW-A001__2026-11-16__14", ...], "mode": "normal" | "force"}
  -> exactly those, unfiltered (CheckBatch reports bad ids / no data).

Output: {"mode": ..., "count": n, "batches": [{"batch_id": ..., "mode": ...}, ...]}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta, timezone

import boto3

from common import schema

log = logging.getLogger()
log.setLevel(logging.INFO)

DEFAULT_LOOKBACK_HOURS = 6  # safety net only: late uploads are found by their pending marker
# keeps one execution well under the Step Functions 25,000-event history limit
DEFAULT_MAX_BATCHES = 500

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


def pending_batches(s3, bucket: str, h: datetime) -> list[str]:
    """batch_ids with a pending marker, hours before H only (bad names are skipped)."""
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=schema.PENDING_PREFIX):
        for obj in page.get("Contents", []):
            batch_id = obj["Key"][len(schema.PENDING_PREFIX):]
            try:
                b = schema.parse_batch_id(batch_id)
            except ValueError:
                log.warning("ignoring bad pending marker %s", obj["Key"])
                continue
            if datetime.combine(b.date, time(b.hour)) < h:
                out.append(batch_id)
    return out


def _hour_key(batch_id: str):
    b = schema.parse_batch_id(batch_id)
    return (b.date, b.hour)


def plan_all(s3, bucket: str, devices, h: datetime, n: int,
             limit: int = DEFAULT_MAX_BATCHES) -> tuple[list[str], int]:
    """(pending + lookback batch_ids, oldest hour first, at most `limit`; count left over)."""
    ids = list(dict.fromkeys(plan(s3, bucket, devices, h, n) + pending_batches(s3, bucket, h)))
    ids.sort(key=_hour_key)       # stable: lookback order kept inside an hour
    return ids[:limit], max(0, len(ids) - limit)


def plan(s3, bucket: str, devices, h: datetime, n: int) -> list[str]:
    """batch_ids with raw data in the lookback window, oldest hour first."""
    # dict.fromkeys: a station that moved inside the hour has 2 registry rows
    # active in it, but is still one batch
    candidates = list(dict.fromkeys(
        (hour, d.device_id)
        for hour in lookback_hours(h, n)
        for d in devices if active_in_hour(d, hour)))
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
        limit = int(os.environ.get("MAX_BATCHES", DEFAULT_MAX_BATCHES))
        h = scheduled_hour_local(event.get("scheduled_time"))
        ids, left = plan_all(_client(), os.environ["DATA_BUCKET"], schema.load_devices(),
                             h, n, limit)
        source = f"schedule H={h.isoformat(sep=' ')} lookback={n} left_for_next_run={left}"
    log.info(json.dumps({"source": source, "mode": mode, "count": len(ids)}))
    return {
        "mode": mode,
        "count": len(ids),
        "batches": [{"batch_id": b, "mode": mode} for b in ids],
    }
