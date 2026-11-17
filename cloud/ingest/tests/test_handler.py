"""Unit tests for cloud/ingest/handler.py (spec 20 §5). S3 is mocked by moto."""

import base64
import json

import boto3
import pytest
from moto import mock_aws

import handler
from common.schema import CSV_COLUMNS

BUCKET = "test-iot-dt"
TOKEN = "tok-walk-01-secret"
OTHER_TOKEN = "tok-walk-02-secret"
DEV = "IOTW-A001"
OTHER_DEV = "IOTW-A002"
HEADER = ",".join(CSV_COLUMNS)


def row(rec, dt, dev=DEV, session=12, time_valid=1):
    return (f"{dev},Mobile,{session},{rec},{dt},55.21,62.40,10234.50,31.2,65.4,"
            f"14.0717281,100.6052143,4.2,3.10,9,0.92,1,1,1,{time_valid},1,OK")


def csv_body(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


def event(body, token=TOKEN, device=DEV, chunk="12-1-3", b64=False, extra=None):
    headers = {"content-type": "text/csv"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    if device is not None:
        headers["x-device-id"] = device
    if chunk is not None:
        headers["x-chunk-id"] = chunk
    headers.update(extra or {})
    if b64:
        body = base64.b64encode(body.encode()).decode()
    return {"headers": headers, "body": body, "isBase64Encoded": b64}


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", BUCKET)
    monkeypatch.setenv("DEVICE_TOKENS", json.dumps({
        handler.hash_token(TOKEN): DEV,
        handler.hash_token(OTHER_TOKEN): OTHER_DEV,
    }))
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        monkeypatch.setattr(handler, "_s3", client)
        yield client


def keys(s3):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def body_of(s3, key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()


# ── 200 ──────────────────────────────────────────────────────────────────────

def test_ok_single_hour(s3):
    body = csv_body(row(1, "2026-11-16 08:30:00"), row(2, "2026-11-16 08:30:01"),
                    row(3, "2026-11-16 08:30:02"))
    r = handler.handler(event(body))
    assert r["statusCode"] == 200
    out = json.loads(r["body"])
    key = f"raw/csv/device_id={DEV}/date=2026-11-16/hour=08/part-12-1-3.csv"
    assert out == {"written": [key], "rows": 3}
    assert keys(s3) == [key]
    assert body_of(s3, key) == body   # verbatim, header included


def test_chunk_across_hour_split_into_two_files(s3):
    body = csv_body(row(1, "2026-11-16 08:59:59"), row(2, "2026-11-16 09:00:00"),
                    row(3, "2026-11-16 09:00:01"))
    r = handler.handler(event(body))
    assert r["statusCode"] == 200
    k8 = f"raw/csv/device_id={DEV}/date=2026-11-16/hour=08/part-12-1-3.csv"
    k9 = f"raw/csv/device_id={DEV}/date=2026-11-16/hour=09/part-12-1-3.csv"
    assert keys(s3) == [k8, k9]
    assert body_of(s3, k8).splitlines() == [HEADER, row(1, "2026-11-16 08:59:59")]
    assert body_of(s3, k9).splitlines()[0] == HEADER
    assert len(body_of(s3, k9).splitlines()) == 3


def test_across_midnight_changes_date(s3):
    body = csv_body(row(1, "2026-11-16 23:59:59"), row(2, "2026-11-17 00:00:00"))
    r = handler.handler(event(body, chunk="12-1-2"))
    assert r["statusCode"] == 200
    assert keys(s3) == [
        f"raw/csv/device_id={DEV}/date=2026-11-16/hour=23/part-12-1-2.csv",
        f"raw/csv/device_id={DEV}/date=2026-11-17/hour=00/part-12-1-2.csv",
    ]


def test_no_rtc_goes_to_unknown(s3):
    body = csv_body(row(1, "12345", time_valid=0), row(2, "12346", time_valid=0))
    r = handler.handler(event(body, chunk="12-1-2"))
    assert r["statusCode"] == 200
    assert keys(s3) == [
        f"raw/csv/device_id={DEV}/date=unknown/hour=unknown/part-12-1-2.csv"]


def test_resend_three_times_is_idempotent(s3):
    body = csv_body(row(1, "2026-11-16 08:59:59"), row(2, "2026-11-16 09:00:00"))
    for _ in range(3):
        assert handler.handler(event(body, chunk="12-1-2"))["statusCode"] == 200
    assert len(keys(s3)) == 2


def test_base64_body_and_crlf(s3):
    body = csv_body(row(1, "2026-11-16 08:30:00")).replace("\n", "\r\n")
    r = handler.handler(event(body, chunk="12-1-1", b64=True))
    assert r["statusCode"] == 200
    assert body_of(s3, json.loads(r["body"])["written"][0]).splitlines()[1] == \
        row(1, "2026-11-16 08:30:00")


def test_header_names_case_insensitive(s3):
    ev = event(csv_body(row(1, "2026-11-16 08:30:00")), chunk="12-1-1")
    ev["headers"] = {k.title(): v for k, v in ev["headers"].items()}
    assert handler.handler(ev)["statusCode"] == 200


def test_300_row_chunk_about_36kb(s3):
    rows = [row(i, f"2026-11-16 08:{(i - 1) // 60:02d}:{(i - 1) % 60:02d}")
            for i in range(1, 301)]
    body = csv_body(*rows)
    assert 30_000 < len(body) < 45_000
    r = handler.handler(event(body, chunk="12-1-300"))
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["rows"] == 300


# ── 400 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("chunk", [None, "", "abc", "12-1", "12-1-2-3", "12-a-3", "12-5-3"])
def test_bad_chunk_id(s3, chunk):
    r = handler.handler(event(csv_body(row(1, "2026-11-16 08:30:00")), chunk=chunk))
    assert r["statusCode"] == 400
    assert keys(s3) == []


@pytest.mark.parametrize("body", ["", "\n\n", HEADER + "\n"])
def test_empty_body_or_no_rows(s3, body):
    assert handler.handler(event(body))["statusCode"] == 400


def test_bad_header_missing_column(s3):
    header = ",".join(c for c in CSV_COLUMNS if c != "Lux")
    r = handler.handler(event(csv_body(row(1, "2026-11-16 08:30:00"), header=header),
                              chunk="12-1-1"))
    assert r["statusCode"] == 400
    assert "Lux" in json.loads(r["body"])["error"]


def test_bad_header_out_of_order(s3):
    cols = list(CSV_COLUMNS)
    cols[5], cols[6] = cols[6], cols[5]
    r = handler.handler(event(csv_body(row(1, "2026-11-16 08:30:00"),
                                       header=",".join(cols)), chunk="12-1-1"))
    assert r["statusCode"] == 400


def test_row_outside_chunk_id(s3):
    body = csv_body(row(1, "2026-11-16 08:30:00"), row(9, "2026-11-16 08:30:08"))
    assert handler.handler(event(body, chunk="12-1-3"))["statusCode"] == 400
    body = csv_body(row(1, "2026-11-16 08:30:00", session=13))
    assert handler.handler(event(body, chunk="12-1-1"))["statusCode"] == 400
    assert keys(s3) == []


def test_bad_base64(s3):
    ev = event("x", chunk="12-1-1")
    ev.update(body="not base64!!", isBase64Encoded=True)
    assert handler.handler(ev)["statusCode"] == 400


# ── 401 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("auth", [None, "", "Bearer ", "Basic abc", "Bearer wrong"])
def test_bad_token(s3, auth):
    ev = event(csv_body(row(1, "2026-11-16 08:30:00")), token=None, chunk="12-1-1")
    if auth is not None:
        ev["headers"]["authorization"] = auth
    assert handler.handler(ev)["statusCode"] == 401
    assert keys(s3) == []


# ── 403 ──────────────────────────────────────────────────────────────────────

def test_x_device_id_not_token_device(s3):
    r = handler.handler(event(csv_body(row(1, "2026-11-16 08:30:00")),
                              device=OTHER_DEV, chunk="12-1-1"))
    assert r["statusCode"] == 403


def test_row_device_id_mismatch_rejects_whole_chunk(s3):
    body = csv_body(row(1, "2026-11-16 08:30:00"),
                    row(2, "2026-11-16 08:30:01", dev=OTHER_DEV))
    assert handler.handler(event(body, chunk="12-1-2"))["statusCode"] == 403
    assert keys(s3) == []


# ── 413 ──────────────────────────────────────────────────────────────────────

def test_body_too_large(s3, monkeypatch):
    monkeypatch.setattr(handler, "MAX_BODY_BYTES", 100)
    r = handler.handler(event(csv_body(row(1, "2026-11-16 08:30:00")), chunk="12-1-1"))
    assert r["statusCode"] == 413
    assert keys(s3) == []


# ── 503 ──────────────────────────────────────────────────────────────────────

def test_s3_failure_returns_503(s3, monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", "bucket-that-does-not-exist")
    r = handler.handler(event(csv_body(row(1, "2026-11-16 08:30:00")), chunk="12-1-1"))
    assert r["statusCode"] == 503
