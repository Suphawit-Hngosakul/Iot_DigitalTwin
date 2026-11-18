"""Tests for the api Lambda (issue #18). Athena is a fake, S3 is moto."""

import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import handler

BUCKET = "iot-dt-test"


class FakeAthena:
    """Enough of the Athena client for run_query: states, then result pages."""

    def __init__(self, columns=(), rows=(), states=("SUCCEEDED",), page_size=1000):
        self.columns = list(columns)          # [(name, type)]
        self.rows = [list(r) for r in rows]   # values as Athena strings (None = NULL)
        self.states = list(states)
        self.page_size = page_size
        self.queries = []
        self.stopped = []

    def start_query_execution(self, **kw):
        self.queries.append(kw)
        return {"QueryExecutionId": f"q{len(self.queries)}"}

    def get_query_execution(self, QueryExecutionId):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"QueryExecution": {"Status": {"State": state, "StateChangeReason": "boom"}}}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped.append(QueryExecutionId)

    def get_paginator(self, name):
        assert name == "get_query_results"
        fake = self

        class P:
            def paginate(self, QueryExecutionId):
                header = {"Data": [{"VarCharValue": c} for c, _ in fake.columns]}
                data = [{"Data": [{} if v is None else {"VarCharValue": v} for v in r]}
                        for r in fake.rows]
                allrows = [header] + data
                meta = {"ColumnInfo": [{"Name": c, "Type": t} for c, t in fake.columns]}
                for i in range(0, max(len(allrows), 1), fake.page_size):
                    yield {"ResultSet": {"Rows": allrows[i:i + fake.page_size],
                                         "ResultSetMetadata": meta}}
        return P()

    @property
    def sql(self):
        return self.queries[-1]["QueryString"]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", BUCKET)
    monkeypatch.setenv("ATHENA_DB", "iot_walking")
    monkeypatch.setenv("ATHENA_WORKGROUP", "iot_walking")
    monkeypatch.delenv("ATHENA_OUTPUT", raising=False)
    handler._cache.clear()
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        monkeypatch.setattr(handler, "_s3", s3)
        yield s3


@pytest.fixture
def athena(monkeypatch):
    fake = FakeAthena()
    monkeypatch.setattr(handler, "_athena", fake)
    monkeypatch.setattr(handler.time, "sleep", lambda s: None)
    return fake


def publish(s3, batch_id, published_at="2026-11-16T02:10:00+00:00"):
    s3.put_object(Bucket=BUCKET, Key=f"processed/_manifests/{batch_id}.json",
                  Body=json.dumps({"batch_id": batch_id, "published_at": published_at}))


def call(path, method="GET", **params):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "path": path}},
             "queryStringParameters": params or None}
    r = handler.handler(event)
    return r["statusCode"], json.loads(r["body"])


# --- time parsing / ranges ---------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2026-11-16T08:00:00", "2026-11-16T08:00:00"),
    ("2026-11-16 08:00", "2026-11-16T08:00:00"),
    ("2026-11-16", "2026-11-16T00:00:00"),
    ("2026-11-16T01:00:00Z", "2026-11-16T08:00:00"),          # UTC -> Thai
    ("2026-11-16T08:00:00+07:00", "2026-11-16T08:00:00"),
])
def test_parse_time_to_thai_local(value, expected):
    assert handler.parse_time(value, "from").isoformat() == expected


@pytest.mark.parametrize("value", ["", None, "yesterday", "2026-13-01"])
def test_parse_time_rejects(value):
    with pytest.raises(handler.ApiError) as e:
        handler.parse_time(value, "from")
    assert e.value.status == 400


def test_partition_dates_cross_midnight_and_exclusive_end():
    from datetime import datetime as dt
    assert handler.partition_dates(dt(2026, 11, 16, 22), dt(2026, 11, 17, 2)) == \
        ["2026-11-16", "2026-11-17"]
    # to = midnight is exclusive -> next day not scanned
    assert handler.partition_dates(dt(2026, 11, 16, 0), dt(2026, 11, 17, 0)) == ["2026-11-16"]


@pytest.mark.parametrize("interval,frm,to,status", [
    ("1s", "2026-11-16T08:00", "2026-11-16T14:00", 200),       # exactly 6 h
    ("1s", "2026-11-16T08:00", "2026-11-16T14:00:01", 400),
    ("1min", "2026-11-01", "2026-12-02", 200),                  # 31 days
    ("1min", "2026-11-01", "2026-12-02T00:00:01", 400),
    ("1min", "2026-11-16T09:00", "2026-11-16T08:00", 400),      # to before from
    ("1min", "2026-11-16T08:00", "2026-11-16T08:00", 400),      # empty range
    ("5min", "2026-11-16T08:00", "2026-11-16T09:00", 400),
])
def test_readings_range_limits(athena, interval, frm, to, status):
    code, body = call("/readings", device="IOTW-A001", interval=interval, **{"from": frm, "to": to})
    assert code == status, body
    if status == 400:
        assert athena.queries == []           # rejected before Athena


def test_device_required_and_known(athena):
    assert call("/readings", **{"from": "2026-11-16", "to": "2026-11-17"})[0] == 400
    code, body = call("/readings", device="IOTW-Z999", **{"from": "2026-11-16", "to": "2026-11-17"})
    assert code == 404
    code, _ = call("/readings", device="x' OR 1=1 --", **{"from": "2026-11-16", "to": "2026-11-17"})
    assert code == 404
    assert athena.queries == []


# --- /readings ---------------------------------------------------------------

def test_readings_1min_query_has_partitions_and_typed_rows(athena):
    athena.columns = [("minute_local", "timestamp"), ("n_rows", "integer"),
                      ("db_spl_mean", "double"), ("cell_id", "varchar")]
    athena.rows = [["2026-11-16 23:59:00.000", "60", "61.5", None]]
    code, body = call("/readings", device="IOTW-A001",
                      **{"from": "2026-11-16T23:00", "to": "2026-11-17T01:00"})
    assert code == 200
    sql = athena.sql
    assert "FROM readings_1min" in sql
    assert "device_id = 'IOTW-A001'" in sql
    assert "date IN ('2026-11-16', '2026-11-17')" in sql
    assert "minute_local >= TIMESTAMP '2026-11-16 23:00:00'" in sql
    assert "minute_local < TIMESTAMP '2026-11-17 01:00:00'" in sql
    assert athena.queries[-1]["WorkGroup"] == "iot_walking"
    assert athena.queries[-1]["QueryExecutionContext"] == {"Database": "iot_walking"}
    assert body["interval"] == "1min"
    assert body["columns"] == ["minute_local", "n_rows", "db_spl_mean", "cell_id"]
    assert body["rows"] == [["2026-11-16T23:59:00", 60, 61.5, None]]


def test_readings_1s_uses_readings_1s(athena):
    code, body = call("/readings", device="IOTW-A002", interval="1s",
                      **{"from": "2026-11-16T08:00:00+07:00", "to": "2026-11-16T02:00:00Z"})
    assert code == 200
    assert "FROM readings_1s" in athena.sql
    assert "ts_local < TIMESTAMP '2026-11-16 09:00:00'" in athena.sql
    assert body["rows"] == []


def test_readings_many_pages(athena):
    athena.columns = [("ts_local", "timestamp"), ("db_spl", "double")]
    athena.rows = [[f"2026-11-16 08:00:{i % 60:02d}.000", str(i)] for i in range(2500)]
    code, body = call("/readings", device="IOTW-A001", interval="1s",
                      **{"from": "2026-11-16T08:00", "to": "2026-11-16T09:00"})
    assert code == 200
    assert len(body["rows"]) == 2500
    assert body["rows"][-1][1] == 2499.0


def test_athena_output_is_passed_when_set(athena, monkeypatch):
    monkeypatch.setenv("ATHENA_OUTPUT", "s3://b/athena-results/")
    call("/readings", device="IOTW-A001", **{"from": "2026-11-16", "to": "2026-11-17"})
    assert athena.queries[-1]["ResultConfiguration"] == {"OutputLocation": "s3://b/athena-results/"}


# --- Athena failures ---------------------------------------------------------

def test_timeout_gives_504_and_stops_query(athena):
    athena.states = ["RUNNING"]
    t = iter(range(0, 1000, 5))           # each clock() call advances 5 s
    with pytest.raises(handler.ApiError) as e:
        handler.run_query("SELECT 1", athena=athena, timeout_s=25,
                          sleep=lambda s: None, clock=lambda: next(t))
    assert e.value.status == 504
    assert athena.stopped == ["q1"]


def test_timeout_through_handler(athena, monkeypatch):
    athena.states = ["RUNNING"]
    monkeypatch.setattr(handler, "ATHENA_TIMEOUT_S", 0)
    code, body = call("/readings", device="IOTW-A001", **{"from": "2026-11-16", "to": "2026-11-17"})
    assert code == 504
    assert athena.stopped


def test_failed_query_gives_502(athena):
    athena.states = ["QUEUED", "FAILED"]
    code, body = call("/readings", device="IOTW-A001", **{"from": "2026-11-16", "to": "2026-11-17"})
    assert code == 502
    assert "error" in body


# --- cache -------------------------------------------------------------------

def test_cache_60s(athena, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(handler.time, "monotonic", lambda: now[0])
    q = {"device": "IOTW-A001", "from": "2026-11-16", "to": "2026-11-17"}
    assert call("/readings", **q)[0] == 200
    assert call("/readings", **q)[0] == 200
    assert len(athena.queries) == 1                     # second answer from memory
    call("/readings", **{**q, "interval": "1s", "to": "2026-11-16T01:00"})
    assert len(athena.queries) == 2                     # other params -> new query
    now[0] += 61
    call("/readings", **q)
    assert len(athena.queries) == 3                     # expired


def test_errors_are_not_cached(athena):
    athena.states = ["FAILED"]
    q = {"device": "IOTW-A001", "from": "2026-11-16", "to": "2026-11-17"}
    assert call("/readings", **q)[0] == 502
    athena.states = ["SUCCEEDED"]
    assert call("/readings", **q)[0] == 200


# --- /devices and /latest ----------------------------------------------------

def test_devices_lists_registry_and_latest_published_hour(env, athena):
    publish(env, "IOTW-A001__2026-11-15__23")
    publish(env, "IOTW-A001__2026-11-16__09", "2026-11-16T03:05:12+00:00")
    publish(env, "IOTW-A001__2026-11-16__10", "2026-11-16T04:05:12+00:00")
    code, body = call("/devices")
    assert code == 200
    devs = {d["device_id"]: d for d in body["devices"]}
    assert set(devs) == {"IOTW-A001", "IOTW-A002"}
    a1 = devs["IOTW-A001"]
    assert a1["device_type"] == "Mobile"
    assert a1["latest_batch_id"] == "IOTW-A001__2026-11-16__10"
    assert a1["latest_hour_local"] == "2026-11-16T10:00:00"
    assert a1["published_at"] == "2026-11-16T04:05:12+00:00"
    assert devs["IOTW-A002"]["latest_batch_id"] is None
    assert athena.queries == []                          # no Athena for /devices


def test_latest_queries_only_the_latest_published_date(env, athena):
    publish(env, "IOTW-A001__2026-11-14__08")
    publish(env, "IOTW-A001__2026-11-16__10")
    athena.columns = [("minute_local", "timestamp"), ("db_spl_mean", "double")]
    athena.rows = [["2026-11-16 10:59:00.000", "58.25"]]
    code, body = call("/latest", device="IOTW-A001")
    assert code == 200
    assert "date = '2026-11-16'" in athena.sql
    assert "ORDER BY minute_local DESC LIMIT 1" in athena.sql
    assert body == {"device": "IOTW-A001",
                    "row": {"minute_local": "2026-11-16T10:59:00", "db_spl_mean": 58.25}}


def test_latest_without_data_is_404(athena):
    code, _ = call("/latest", device="IOTW-A002")
    assert code == 404
    assert athena.queries == []


# --- /quality ----------------------------------------------------------------

def test_quality_per_hour_filters_hours_and_computes_loss(athena):
    athena.columns = [("batch_id", "varchar"), ("expected_rows", "bigint"),
                      ("received_rows", "bigint"), ("missing_rows", "bigint"),
                      ("duplicate_rows", "bigint"), ("invalid_rows", "bigint"),
                      ("sessions", "bigint")]
    athena.rows = [
        ["IOTW-A001__2026-11-16__07", "3600", "3600", "0", "0", "0", "1"],   # before from
        ["IOTW-A001__2026-11-16__08", "3600", "3240", "360", "5", "7", "2"],
        ["IOTW-A001__2026-11-16__09", "0", "0", "0", "0", "0", "1"],
        ["IOTW-A001__2026-11-16__10", "3600", "3600", "0", "0", "0", "1"],   # to is exclusive
    ]
    code, body = call("/quality", device="IOTW-A001",
                      **{"from": "2026-11-16T08:30", "to": "2026-11-16T10:00"})
    assert code == 200
    assert "FROM quality" in athena.sql and "date IN ('2026-11-16')" in athena.sql
    hours = body["hours"]
    assert [h["hour_local"] for h in hours] == ["2026-11-16T08:00:00", "2026-11-16T09:00:00"]
    assert hours[0]["loss_rate"] == pytest.approx(0.1)
    assert hours[0]["dropped_rows"] == 12
    assert hours[1]["loss_rate"] == 0.0


def test_quality_range_limit(athena):
    code, _ = call("/quality", device="IOTW-A001", **{"from": "2026-10-01", "to": "2026-11-02"})
    assert code == 400


# --- routing -----------------------------------------------------------------

def test_unknown_route_and_method():
    assert call("/nope")[0] == 404
    assert call("/devices", method="POST")[0] == 405


def test_openapi_matches_routes():
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((Path(handler.__file__).with_name("openapi.yaml")).read_text("utf-8"))
    assert set(spec["paths"]) == set(handler.ROUTES)
    for path, ops in spec["paths"].items():
        assert set(ops) == {"get"}, path
