import importlib.util
from pathlib import Path


def _load_function(name):
    """functions/<name>/handler.py as its own module (every Lambda file is handler.py)."""
    path = Path(__file__).resolve().parents[1] / "functions" / name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"fn_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
import json

import boto3
import pytest
from moto import mock_aws

from common import batch_manifest, schema

cb = _load_function("check_batch")

BUCKET = "test-data"
BATCH = "IOTW-A001__2026-11-16__14"
GOOD_CSV = (Path(__file__).with_name("fixtures") / "good.csv").read_bytes()


@pytest.fixture
def s3(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        monkeypatch.setenv("DATA_BUCKET", BUCKET)
        monkeypatch.setattr(cb, "_s3", client)
        yield client


def put_chunk(s3, chunk_id, body=GOOD_CSV, batch_id=BATCH):
    key = schema.raw_prefix(batch_id) + f"part-{chunk_id}.csv"
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)
    return key


def publish(s3, batch_id, digest):
    """What VerifyBatch (#16) will write."""
    s3.put_object(Bucket=BUCKET, Key=schema.s3_key_manifest(batch_id),
                  Body=json.dumps({"batch_id": batch_id, "manifest_hash": digest}))


def run(event):
    return cb.handler(event)


# ── INVALID_INPUT ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("event", [
    {"batch_id": "IOTW-A001/2026-11-16/14"},          # old spec format
    {"batch_id": "IOTW-A001__2026-11-16__24"},        # hour 24
    {"batch_id": "IOTW-A001__2026-13-16__14"},        # month 13
    {},                                               # missing
    {"batch_id": BATCH, "mode": "correction"},        # mode removed (D21)
])
def test_invalid_input(s3, event):
    out = run(event)
    assert out["check"]["status"] == cb.INVALID_INPUT
    assert out["check"]["reason"]


# ── NO_DATA ──────────────────────────────────────────────────────────────────

def test_no_data(s3):
    out = run({"batch_id": BATCH})
    assert out["check"]["status"] == cb.NO_DATA
    assert out["check"]["manifest"] == []


def test_neighbour_hour_and_unknown_are_not_this_batch(s3):
    put_chunk(s3, "12-1-4", batch_id="IOTW-A001__2026-11-16__15")
    put_chunk(s3, "12-1-4", batch_id="IOTW-A002__2026-11-16__14")
    s3.put_object(Bucket=BUCKET, Body=GOOD_CSV,
                  Key="raw/csv/device_id=IOTW-A001/date=unknown/hour=unknown/part-12-1-4.csv")
    assert run({"batch_id": BATCH})["check"]["status"] == cb.NO_DATA


# ── NEW ──────────────────────────────────────────────────────────────────────

def test_new_lists_files_sorted(s3):
    k2 = put_chunk(s3, "12-5-8")
    k1 = put_chunk(s3, "12-1-4")
    out = run({"batch_id": BATCH})
    check = out["check"]
    assert check["status"] == cb.NEW
    assert [f["key"] for f in check["manifest"]] == [k1, k2]
    assert all(f["etag"] and not f["etag"].startswith('"') for f in check["manifest"])
    assert check["manifest"][0]["size"] == len(GOOD_CSV)
    assert out["batch_id"] == BATCH and out["mode"] == "normal"


def test_force_on_unpublished_is_new(s3):
    put_chunk(s3, "12-1-4")
    assert run({"batch_id": BATCH, "mode": "force"})["check"]["status"] == cb.NEW


# ── ALREADY_PROCESSED ────────────────────────────────────────────────────────

def test_already_processed(s3):
    put_chunk(s3, "12-1-4")
    publish(s3, BATCH, run({"batch_id": BATCH})["check"]["manifest_hash"])
    assert run({"batch_id": BATCH})["check"]["status"] == cb.ALREADY_PROCESSED


def test_resend_same_chunk_is_still_processed(s3):
    """Device retries a chunk -> ingest overwrites the same key with the same bytes."""
    put_chunk(s3, "12-1-4")
    publish(s3, BATCH, run({"batch_id": BATCH})["check"]["manifest_hash"])
    put_chunk(s3, "12-1-4")
    assert run({"batch_id": BATCH})["check"]["status"] == cb.ALREADY_PROCESSED


# ── CHANGED ──────────────────────────────────────────────────────────────────

def test_backfill_chunk_makes_changed(s3):
    put_chunk(s3, "12-1-4")
    publish(s3, BATCH, run({"batch_id": BATCH})["check"]["manifest_hash"])
    put_chunk(s3, "12-5-8")
    check = run({"batch_id": BATCH})["check"]
    assert check["status"] == cb.CHANGED
    assert len(check["manifest"]) == 2


def test_replaced_content_makes_changed(s3):
    put_chunk(s3, "12-1-4")
    publish(s3, BATCH, run({"batch_id": BATCH})["check"]["manifest_hash"])
    put_chunk(s3, "12-1-4", body=GOOD_CSV + b"\n")
    assert run({"batch_id": BATCH})["check"]["status"] == cb.CHANGED


def test_force_on_published_is_changed(s3):
    put_chunk(s3, "12-1-4")
    publish(s3, BATCH, run({"batch_id": BATCH})["check"]["manifest_hash"])
    check = run({"batch_id": BATCH, "mode": "force"})["check"]
    assert check["status"] == cb.CHANGED
    assert check["reason"] == "mode=force"


# ── manifest hash ────────────────────────────────────────────────────────────

def test_hash_ignores_order_and_size():
    a = [{"key": "k1", "etag": "e1", "size": 1}, {"key": "k2", "etag": "e2", "size": 2}]
    b = [{"key": "k2", "etag": "e2", "size": 99}, {"key": "k1", "etag": "e1", "size": 1}]
    assert batch_manifest.manifest_hash(a) == batch_manifest.manifest_hash(b)
    assert batch_manifest.manifest_hash(a) != batch_manifest.manifest_hash(a[:1])


def test_input_fields_pass_through(s3):
    out = run({"batch_id": BATCH, "mode": "normal", "extra": 1})
    assert out["extra"] == 1
