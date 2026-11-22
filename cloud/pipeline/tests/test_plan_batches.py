import importlib.util
from pathlib import Path


def _load_function(name):
    """functions/<name>/handler.py as its own module (every Lambda file is handler.py)."""
    path = Path(__file__).resolve().parents[1] / "functions" / name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"fn_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from common import schema

pb = _load_function("plan_batches")

BUCKET = "test-data"


def dev(device_id, valid_from="2026-11-01", valid_to=None, device_type="Mobile"):
    return schema.Device(device_id, device_type, None, None, "",
                         datetime.fromisoformat(valid_from),
                         datetime.fromisoformat(valid_to) if valid_to else None, "")


DEVICES = [dev("IOTW-A001"), dev("IOTW-A002")]


@pytest.fixture
def s3(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        monkeypatch.setenv("DATA_BUCKET", BUCKET)
        monkeypatch.setattr(pb, "_s3", client)
        yield client


def put(s3, device_id, day, hour):
    s3.put_object(Bucket=BUCKET, Body=b"x",
                  Key=f"raw/csv/device_id={device_id}/date={day}/hour={hour}/part-1-1-1.csv")


# ── time ─────────────────────────────────────────────────────────────────────

def test_scheduled_time_is_thai_hour():
    # 18:05 UTC = 01:05 Thai next day -> H = 01:00
    assert pb.scheduled_hour_local("2026-11-17T18:05:00Z") == datetime(2026, 11, 18, 1)


def test_unsubstituted_placeholder_falls_back_to_now():
    # Scheduler left "<aws.scheduler.scheduled-time>" as-is -> use now, don't crash
    now = datetime(2026, 11, 18, 0, 5, tzinfo=timezone.utc)   # 07:05 Thai
    assert pb.scheduled_hour_local("<aws.scheduler.scheduled-time>", now=now) \
        == datetime(2026, 11, 18, 7)


def test_lookback_crosses_midnight():
    hours = pb.lookback_hours(datetime(2026, 11, 18, 1), 3)
    assert hours == [datetime(2026, 11, 17, 22), datetime(2026, 11, 17, 23),
                     datetime(2026, 11, 18, 0)]


# ── scheduled plan ───────────────────────────────────────────────────────────

def test_only_hours_with_data(s3):
    put(s3, "IOTW-A001", "2026-11-17", "23")
    put(s3, "IOTW-A001", "2026-11-18", "00")
    put(s3, "IOTW-A002", "2026-11-17", "10")
    out = pb.plan(s3, BUCKET, DEVICES, datetime(2026, 11, 18, 1), 24)
    assert out == ["IOTW-A002__2026-11-17__10",
                   "IOTW-A001__2026-11-17__23",
                   "IOTW-A001__2026-11-18__00"]


def test_current_hour_and_outside_window_excluded(s3):
    put(s3, "IOTW-A001", "2026-11-18", "01")     # H itself: still being written
    put(s3, "IOTW-A001", "2026-11-17", "00")     # H-25
    assert pb.plan(s3, BUCKET, DEVICES, datetime(2026, 11, 18, 1), 24) == []


def test_unknown_partition_ignored(s3):
    s3.put_object(Bucket=BUCKET, Body=b"x",
                  Key="raw/csv/device_id=IOTW-A001/date=unknown/hour=unknown/part-1-1-1.csv")
    assert pb.plan(s3, BUCKET, DEVICES, datetime(2026, 11, 18, 1), 24) == []


def test_inactive_device_skipped(s3):
    put(s3, "IOTW-A009", "2026-11-17", "23")
    devices = DEVICES + [dev("IOTW-A009", valid_from="2026-11-01", valid_to="2026-11-17")]
    assert pb.plan(s3, BUCKET, devices, datetime(2026, 11, 18, 1), 24) == []


def test_handler_scheduled(s3, monkeypatch):
    put(s3, "IOTW-A001", "2026-11-17", "23")
    monkeypatch.setattr(pb.schema, "load_devices", lambda: DEVICES)
    out = pb.handler({"mode": "normal", "scheduled_time": "2026-11-17T18:05:00Z"})
    assert out == {"mode": "normal", "count": 1,
                   "batches": [{"batch_id": "IOTW-A001__2026-11-17__23", "mode": "normal"}]}


def test_handler_uses_real_devices_csv(s3):
    out = pb.handler({"scheduled_time": "2026-11-17T18:05:00Z"})
    assert out["count"] == 0 and out["mode"] == "normal"


# ── manual run ───────────────────────────────────────────────────────────────

def test_manual_batch_ids_pass_unfiltered(s3):
    out = pb.handler({"batch_ids": ["IOTW-A001__2026-11-16__14", "bad"], "mode": "force"})
    assert out["batches"] == [{"batch_id": "IOTW-A001__2026-11-16__14", "mode": "force"},
                              {"batch_id": "bad", "mode": "force"}]


def test_station_moved_inside_hour_is_one_batch(s3):
    put(s3, "IOTW-S001", "2026-11-17", "14")
    devices = [dev("IOTW-S001", valid_from="2026-11-01", valid_to="2026-11-17 14:30:00",
                   device_type="Stationary"),
               dev("IOTW-S001", valid_from="2026-11-17 14:30:00", device_type="Stationary")]
    assert pb.plan(s3, BUCKET, devices, datetime(2026, 11, 17, 15), 2) == ["IOTW-S001__2026-11-17__14"]
