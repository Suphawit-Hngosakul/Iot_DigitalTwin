"""Integration tests for plan_analytics and run_analytics handlers (Issue #33)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from functions.plan_analytics import handler as plan_h
from functions.run_analytics import handler as run_h
from stats import runner


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode("utf-8")

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        class Paginator:
            def __init__(self, objs):
                self.objs = objs

            def paginate(self, Bucket, Prefix, Delimiter=None):
                from datetime import datetime, timezone
                keys = [k for k in self.objs if k.startswith(Prefix)]
                if Delimiter:
                    dirs = sorted({Prefix + k[len(Prefix):].split(Delimiter, 1)[0] + Delimiter
                                   for k in keys if Delimiter in k[len(Prefix):]})
                    keys = [k for k in keys if Delimiter not in k[len(Prefix):]]
                    page = {"CommonPrefixes": [{"Prefix": d} for d in dirs]}
                else:
                    page = {}
                page["Contents"] = [
                    {"Key": k, "LastModified": datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)}
                    for k in keys
                ]
                return [page]

        return Paginator(self.objects)

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


def test_plan_analytics_from_s3_merge_event():
    s3 = FakeS3()
    res = plan_h.plan(s3, {"s3_key": "curated/_success/merge/season_2025_03-05.json"}, bucket="test-bucket")
    assert res["plan"]["count"] == 1
    assert res["plan"]["periods"] == [{"period": "season_2025_03-05", "mode": "normal"}]


def _cell_period_df() -> pd.DataFrame:
    rows = []
    for r in range(8):
        for c in range(8):
            # a list, not a generator: a generator would read c/r after the loops end
            rows.extend([{
                "cell_id": f"c{c:04d}_r{r:04d}",
                "period": "season_2025_03-05",
                "match_type": m,
                "in_cover": True,
                "lst_mean": 35.0 + 0.4 * c + 0.3 * r,
                "ndvi_mean": 0.5 - 0.03 * c,
                "ndbi_mean": -0.1 + 0.04 * c,
                "builtup_pct": 20.0 + 5.0 * c,
                "air_temp_mean": None,
                "db_spl_mean": None,
            } for m in ("window", "all")])
    return pd.DataFrame(rows)


def _patch(monkeypatch, df):
    monkeypatch.setattr(run_h.st, "load_cell_period_df", lambda *a, **kw: df)
    # keep a reference to the real function: run_h.runner IS runner, so calling
    # runner.build_analytics_outputs inside the lambda would call the lambda itself
    real_build = runner.build_analytics_outputs
    monkeypatch.setattr(
        run_h.runner,
        "build_analytics_outputs",
        lambda df, period: real_build(df, period=period, n_boot=30),
    )


def test_run_analytics_writes_json_parquet_and_marker(monkeypatch):
    s3 = FakeS3()
    _patch(monkeypatch, _cell_period_df())

    out = run_h.run_for_period(None, s3, {"period": "season_2025_03-05"}, bucket="test-bucket", workgroup="wg")
    assert out["analytics"]["status"] == "PUBLISHED"
    assert "curated/_success/analytics/season_2025_03-05.json" in s3.objects
    assert "curated/analytics/regression/period=season_2025_03-05/match=all.json" in s3.objects
    assert "curated/analytics/regression/period=season_2025_03-05/part.parquet" in s3.objects

    reg_json = json.loads(s3.objects["curated/analytics/regression/period=season_2025_03-05/match=all.json"])
    assert reg_json["period"] == "season_2025_03-05"
    assert reg_json["match"] == "all"
    assert out["analytics"]["rows_loaded"] == 128
    summary = json.loads(s3.objects["curated/analytics/summary/period=season_2025_03-05/match=window.json"])
    assert summary["n_in_cover_cells"] == 64


def test_rerun_removes_files_of_the_previous_run(monkeypatch):
    s3 = FakeS3()
    stale = "curated/analytics/importance/period=season_2025_03-05/part.parquet"
    other = "curated/analytics/importance/period=season_2024_03-05/part.parquet"
    s3.objects[stale] = b"old"
    s3.objects[other] = b"keep"
    # 4 x 4 = 16 cells < 20 rows -> the RF importance table is empty this run
    df = _cell_period_df()
    df = df[df["cell_id"].str.match(r"c000[0-3]_r000[0-3]")].reset_index(drop=True)
    _patch(monkeypatch, df)

    out = run_h.run_for_period(None, s3, {"period": "season_2025_03-05"}, bucket="test-bucket", workgroup="wg")
    assert out["analytics"]["status"] == "PUBLISHED"
    assert stale not in s3.objects
    assert other in s3.objects
    assert "curated/analytics/summary/period=season_2025_03-05/part.parquet" in s3.objects


def test_period_without_rows_is_no_data_and_writes_no_marker(monkeypatch):
    s3 = FakeS3()
    marker = "curated/_success/analytics/season_2025_03-05.json"
    old = "curated/analytics/summary/period=season_2025_03-05/part.parquet"
    s3.objects[marker] = b"{}"
    s3.objects[old] = b"old"
    _patch(monkeypatch, pd.DataFrame(columns=["cell_id", "period", "match_type", "in_cover", "lst_mean"]))

    out = run_h.run_for_period(None, s3, {"period": "season_2025_03-05"}, bucket="test-bucket", workgroup="wg")
    assert out["analytics"]["status"] == "NO_DATA"
    assert out["analytics"]["removed_keys"] == 2
    assert marker not in s3.objects and old not in s3.objects


def test_statemachine_tolerates_one_failed_period_and_alerts():
    asl = json.loads((Path(__file__).resolve().parents[1] / "statemachine" / "analytics.asl.json").read_text())
    m = asl["States"]["AnalyzePeriods"]
    assert m["ToleratedFailurePercentage"] == 100
    inner = m["ItemProcessor"]["States"]
    assert inner["RunAnalytics"]["Catch"][0]["Next"] == "NotifyIterationFailure"
    assert inner["NotifyIterationFailure"]["Next"] == "IterationFailed"
    assert inner["IterationFailed"]["Type"] == "Fail"
    assert inner["RunAnalytics"]["Next"] == "PublishedOrNoData?"
    choice = inner["PublishedOrNoData?"]
    assert choice["Choices"][0]["StringEquals"] == "NO_DATA" and choice["Choices"][0]["Next"] == "NoData"
    assert inner["NoData"]["Type"] == "Succeed" and inner["Published"]["Type"] == "Succeed"
