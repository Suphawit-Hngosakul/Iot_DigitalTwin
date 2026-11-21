"""curated-merge (#32): period rules, SQL text, PlanMerge / CheckSources / Verify on moto S3.

Athena MERGE itself cannot run here (moto has no Iceberg) — it is tested on Learner Lab.
These tests pin the SQL (time windows, keys) and the orchestration around it.
"""
import importlib.util
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from common import curated as cu
from common import satellite_schema as ss

BASE = Path(__file__).resolve().parents[1]
BUCKET = "test-data"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"cm_{name}", BASE / "functions" / name / "handler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


plan_fn, check_fn = _load("plan_merge"), _load("check_sources")
merge_fn, verify_fn = _load("merge_curated"), _load("verify_curated")


# ---------------------------------------------------------------- periods
def test_scene_period_ranges():
    mp = cu.parse_merge_period("scene_2024-03-17")
    assert (mp.period_type, mp.pass_date) == ("scene", date(2024, 3, 17))
    assert (mp.iot_start, mp.iot_end) == (date(2024, 3, 10), date(2024, 3, 24))


def test_season_and_month_ranges():
    s = cu.parse_merge_period("season_2025_03-05")
    assert (s.iot_start, s.iot_end) == (date(2025, 3, 1), date(2025, 5, 31))
    m = cu.parse_merge_period("month_2026-02")
    assert (m.iot_start, m.iot_end) == (date(2026, 2, 1), date(2026, 2, 28))


@pytest.mark.parametrize("bad", ["month_2026-13", "month_26-01", "year_2024", "scene_2024-13-01", "x"])
def test_bad_periods(bad):
    with pytest.raises(ValueError):
        cu.parse_merge_period(bad)


def test_sat_period_for_month_uses_latest_season_not_after_year():
    seasons = ["season_2022_03-05", "season_2024_03-05", "season_2025_03-05"]
    assert cu.sat_period_for("month_2026-11", seasons) == "season_2025_03-05"
    assert cu.sat_period_for("month_2024-07", seasons) == "season_2024_03-05"
    assert cu.sat_period_for("month_2021-01", seasons) is None
    assert cu.sat_period_for("scene_2024-03-17", seasons) == "scene_2024-03-17"


# ---------------------------------------------------------------- SQL
def test_window_predicate_mobile_only_scene():
    p = cu.iot_predicate(cu.parse_merge_period("scene_2024-03-17"), "window", [])
    assert "date BETWEEN '2024-03-10' AND '2024-03-24'" in p
    assert ">= 570" in p and "< 690" in p        # 09:30-11:30
    assert "device_id" not in p


def test_window_predicate_station_uses_pass_date_10_to_11():
    p = cu.iot_predicate(cu.parse_merge_period("scene_2024-03-17"), "window", ["IOTW-S001"])
    assert "device_id IN ('IOTW-S001') AND date = '2024-03-17'" in p
    assert ">= 600" in p and "< 660" in p        # 10:00-11:00
    assert "device_id NOT IN ('IOTW-S001')" in p and ">= 570" in p


def test_all_predicate_has_no_time_of_day_and_season_window_ignores_station():
    scene = cu.iot_predicate(cu.parse_merge_period("scene_2024-03-17"), "all", ["IOTW-S001"])
    assert "minute_local" not in scene
    season = cu.iot_predicate(cu.parse_merge_period("season_2025_03-05"), "window", ["IOTW-S001"])
    assert "device_id" not in season and ">= 570" in season


def test_bad_match_and_unsafe_values():
    mp = cu.parse_merge_period("month_2026-11")
    with pytest.raises(ValueError):
        cu.iot_predicate(mp, "nope", [])
    with pytest.raises(ValueError):
        cu.iot_predicate(cu.parse_merge_period("scene_2024-03-17"), "window", ["x'; DROP TABLE t;--"])


def test_source_select_covers_every_column_once_per_match():
    sql = cu.build_source_select("month_2026-11", "season_2025_03-05", 2025, ["IOTW-S001"])
    assert sql.count("UNION ALL") == 1
    assert "period = 'season_2025_03-05'" in sql and "year = 2025" in sql
    assert "period_type = 'season'" in sql
    for col in cu.COLUMN_NAMES:
        assert sql.count(f" AS {col}") >= 2, col
    assert "'month_2026-11' AS period" in sql
    assert f">= {cu.MIN_IOT_MINUTES}" in sql


def test_merge_statement_key_and_columns():
    sql = cu.build_merge("scene_2024-03-17", "scene_2024-03-17", 2025, [])
    assert sql.startswith("MERGE INTO iot_walking.cell_period t")
    assert "t.cell_id = s.cell_id AND t.period = s.period AND t.match_type = s.match_type" in sql
    upd = sql.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
    for k in cu.KEY_COLUMNS:
        assert f"{k} = s.{k}" not in upd
    assert "lst_mean = s.lst_mean" in upd and "n_iot_minutes = s.n_iot_minutes" in upd
    assert sql.count("INSERT (") == 1 and "s.merged_at" in sql


def test_create_table_is_iceberg_partitioned_by_period():
    sql = cu.build_create_table("my-bucket")
    assert "PARTITIONED BY (period)" in sql
    assert "'table_type' = 'ICEBERG'" in sql
    assert "LOCATION 's3://my-bucket/curated/cell_period/'" in sql
    assert re.search(r"^\s+cell_id string,", sql, re.M)


def test_verify_counts():
    ok = [{"match_type": m, "n_rows": 10, "n_cells": 10} for m in cu.MATCH_TYPES]
    assert cu.verify_counts(ok, 10) == []
    assert cu.verify_counts(ok[:1], 10) == ["all: no rows"]
    bad = [{"match_type": "window", "n_rows": 9, "n_cells": 8}, ok[1]]
    assert len(cu.verify_counts(bad, 10)) == 2


class FakeAthena:
    def __init__(self, states, rows=None):
        self.states, self.calls, self.rows = list(states), [], rows or {}

    def start_query_execution(self, QueryString, WorkGroup, QueryExecutionContext):
        self.calls.append(QueryString)
        return {"QueryExecutionId": f"q{len(self.calls)}"}

    def get_query_execution(self, QueryExecutionId):
        s = self.states.pop(0) if self.states else ("SUCCEEDED", "")
        return {"QueryExecution": {"Status": {"State": s[0], "StateChangeReason": s[1]}}}

    def get_query_results(self, QueryExecutionId):
        head, *body = self.rows[QueryExecutionId] if QueryExecutionId in self.rows else self.rows["*"]
        mk = lambda r: {"Data": [{"VarCharValue": str(v)} for v in r]}
        return {"ResultSet": {"Rows": [mk(head)] + [mk(r) for r in body]}}


def test_run_query_retries_commit_conflicts_then_succeeds():
    a = FakeAthena([("FAILED", "ICEBERG_COMMIT_ERROR: conflict"), ("SUCCEEDED", "")])
    assert cu.run_query(a, "MERGE", "wg", sleep=lambda s: None) == "q2"
    assert len(a.calls) == 2


def test_run_query_does_not_retry_syntax_errors():
    a = FakeAthena([("FAILED", "SYNTAX_ERROR: line 1")])
    with pytest.raises(cu.AthenaError):
        cu.run_query(a, "BAD", "wg", sleep=lambda s: None)
    assert len(a.calls) == 1


# ---------------------------------------------------------------- S3 side
def _put(s3, key, body=b"{}"):
    s3.put_object(Bucket=BUCKET, Key=key, Body=body if isinstance(body, bytes) else json.dumps(body).encode())


@pytest.fixture
def s3():
    with mock_aws():
        c = boto3.client("s3")
        c.create_bucket(Bucket=BUCKET)
        yield c


def _published(s3, batch_id, year=2024):
    _put(s3, ss.s3_key_success(batch_id), {"batch_id": batch_id})


def _buildings(s3, year=2025):
    _put(s3, cu.SUCCESS_BUILDINGS_KEY, {"trusted_key": ss.s3_key_buildings_grid(year)})


def _plan(s3, event=None):
    return plan_fn.plan(plan_fn.collect(s3, BUCKET), event or {})


def test_plan_picks_unmerged_scene_and_season_but_not_year_composites(s3):
    _published(s3, "scene_2024-03-17")
    _published(s3, "season_2024_03-05")
    got = {p["period"]: p["sat_period"] for p in _plan(s3)["periods"]}
    assert got == {"scene_2024-03-17": "scene_2024-03-17", "season_2024_03-05": "season_2024_03-05"}


def test_plan_adds_month_for_iot_manifest_with_latest_season(s3):
    _published(s3, "season_2025_03-05")
    _put(s3, "processed/_manifests/IOTW-A001__2026-11-16__08.json")
    got = {p["period"]: p["sat_period"] for p in _plan(s3)["periods"]}
    assert got["month_2026-11"] == "season_2025_03-05"


def test_plan_skips_month_without_any_season(s3):
    _put(s3, "processed/_manifests/IOTW-A001__2026-11-16__08.json")
    assert _plan(s3)["count"] == 0


def _merge_marker(s3, period, sat_period, started):
    _put(s3, cu.key_merge_marker(period), {"period": period, "sat_period": sat_period,
                                           "started_at": started.isoformat()})


def test_plan_skips_period_already_merged_after_all_sources(s3):
    _published(s3, "scene_2024-03-17")
    _buildings(s3)
    _merge_marker(s3, "scene_2024-03-17", "scene_2024-03-17", datetime.now(timezone.utc) + timedelta(minutes=5))
    assert _plan(s3)["count"] == 0
    assert _plan(s3, {"periods": ["scene_2024-03-17"]})["count"] == 1      # explicit = force


def test_plan_remerges_when_buildings_or_iot_arrive_later(s3):
    _published(s3, "scene_2026-11-16")
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    _merge_marker(s3, "scene_2026-11-16", "scene_2026-11-16", old)
    assert _plan(s3)["count"] == 1                                    # satellite marker newer than start
    _merge_marker(s3, "scene_2026-11-16", "scene_2026-11-16", datetime.now(timezone.utc) + timedelta(minutes=1))
    assert _plan(s3)["count"] == 0
    _put(s3, "processed/_manifests/IOTW-A001__2026-11-18__10.json")    # inside +-7 days, newer
    later = datetime.now(timezone.utc) - timedelta(minutes=5)
    _merge_marker(s3, "scene_2026-11-16", "scene_2026-11-16", later)
    assert "scene_2026-11-16" in {p["period"] for p in _plan(s3)["periods"]}
    _put(s3, "processed/_manifests/IOTW-A001__2026-12-30__10.json")    # outside the range
    _merge_marker(s3, "scene_2026-11-16", "scene_2026-11-16", datetime.now(timezone.utc) + timedelta(minutes=1))
    assert "scene_2026-11-16" not in {p["period"] for p in _plan(s3)["periods"]}


def test_plan_event_for_one_batch_only_touches_dependents(s3):
    _published(s3, "scene_2024-03-17")
    _published(s3, "season_2025_03-05")
    ev = {"s3_key": ss.s3_key_success("scene_2024-03-17")}
    assert [p["period"] for p in _plan(s3, ev)["periods"]] == ["scene_2024-03-17"]
    ev = {"s3_key": cu.SUCCESS_BUILDINGS_KEY}
    assert _plan(s3, ev)["count"] == 2


def test_check_sources_waits_for_buildings_and_satellite(s3):
    ev = {"period": "scene_2024-03-17", "sat_period": "scene_2024-03-17"}
    out = check_fn.check(s3, BUCKET, ev)
    assert out["check"]["ready"] is False
    assert out["check"]["missing"] == ["satellite:scene_2024-03-17", "buildings"]
    _published(s3, "scene_2024-03-17")
    _buildings(s3, 2025)
    out = check_fn.check(s3, BUCKET, ev)
    assert out["check"]["ready"] is True and out["check"]["bldg_year"] == 2025


def test_merge_curated_runs_create_then_merge_and_refuses_unready():
    a = FakeAthena([])
    ev = {"period": "scene_2024-03-17", "sat_period": "scene_2024-03-17",
          "check": {"ready": True, "bldg_year": 2025, "missing": [], "started_at": "x"}}
    out = merge_fn.merge(a, ev, BUCKET, "wg")
    assert a.calls[0].startswith("CREATE TABLE IF NOT EXISTS")
    assert a.calls[1].startswith("MERGE INTO") and out["merge"]["query_id"] == "q2"
    with pytest.raises(RuntimeError):
        merge_fn.merge(a, {**ev, "check": {**ev["check"], "ready": False, "missing": ["buildings"]}}, BUCKET, "wg")


def _verify_athena(n_window, n_all, expected=6829):
    head = ["match_type", "n_rows", "n_cells", "lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct",
            "air_temp_mean", "n_cells_with_iot"]
    rows = [head, ["window", n_window, n_window, 6000, 6000, 6000, 6500, 40, 40],
            ["all", n_all, n_all, 6000, 6000, 6000, 6500, 90, 90]]
    return FakeAthena([], {"q1": [["n"], [expected]], "q2": rows})


def test_verify_writes_marker(s3):
    ev = {"period": "scene_2024-03-17", "sat_period": "scene_2024-03-17",
          "check": {"ready": True, "bldg_year": 2025, "missing": [], "started_at": "2026-11-21T01:00:00+00:00"}}
    m = verify_fn.verify(_verify_athena(6829, 6829), s3, ev, BUCKET, "wg")
    body = json.loads(s3.get_object(Bucket=BUCKET, Key=cu.key_merge_marker("scene_2024-03-17"))["Body"].read())
    assert body["cells"] == 6829 and body["started_at"] == ev["check"]["started_at"]
    assert body["coverage"]["window"]["n_cells_with_iot"] == 40 and m == body


def test_verify_fails_on_wrong_row_count_and_writes_no_marker(s3):
    ev = {"period": "scene_2024-03-17", "sat_period": "scene_2024-03-17",
          "check": {"ready": True, "bldg_year": 2025, "missing": [], "started_at": "x"}}
    with pytest.raises(verify_fn.VerifyError):
        verify_fn.verify(_verify_athena(6000, 6829), s3, ev, BUCKET, "wg")
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix=cu.MERGE_MARKER_PREFIX)


# ---------------------------------------------------------------- state machine
def test_state_machine_is_wired():
    asl = json.loads((BASE / "statemachine" / "curated-merge.asl.json").read_text())

    def check(states, start):
        assert start in states
        for name, st in states.items():
            for tgt in [st.get("Next"), st.get("Default")] + [c.get("Next") for c in st.get("Choices", [])] \
                    + [c["Next"] for c in st.get("Catch", [])]:
                if tgt:
                    assert tgt in states, f"{name} -> {tgt}"
            if st["Type"] == "Map":
                check(st["ItemProcessor"]["States"], st["ItemProcessor"]["StartAt"])

    check(asl["States"], asl["StartAt"])
    inner = asl["States"]["MergePeriods"]["ItemProcessor"]["States"]
    assert "WaitingForOtherSources" in inner
    text = json.dumps(asl)
    assert set(re.findall(r"\$\{(\w+)\}", text)) == {
        "plan_merge_arn", "check_sources_arn", "merge_curated_arn", "verify_curated_arn", "alert_topic_arn"}
