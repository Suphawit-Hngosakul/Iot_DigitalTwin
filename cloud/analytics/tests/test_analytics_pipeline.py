"""Integration tests for plan_analytics and run_analytics handlers (Issue #33)."""

from __future__ import annotations

import json
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

            def paginate(self, Bucket, Prefix):
                from datetime import datetime, timezone
                contents = [
                    {"Key": k, "LastModified": datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)}
                    for k in self.objs
                    if k.startswith(Prefix)
                ]
                return [{"Contents": contents}]

        return Paginator(self.objects)


def test_plan_analytics_from_s3_merge_event():
    s3 = FakeS3()
    res = plan_h.plan(s3, {"s3_key": "curated/_success/merge/season_2025_03-05.json"}, bucket="test-bucket")
    assert res["plan"]["count"] == 1
    assert res["plan"]["periods"] == [{"period": "season_2025_03-05", "mode": "normal"}]


def test_run_analytics_writes_json_parquet_and_marker(monkeypatch):
    s3 = FakeS3()
    rows = []
    for r in range(8):
        for c in range(8):
            rows.append({
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
            } for m in ("window", "all"))
    flat_rows = [item for sublist in rows for item in sublist]
    df_mock = pd.DataFrame(flat_rows)

    monkeypatch.setattr(run_h.st, "load_cell_period_df", lambda *a, **kw: df_mock)
    monkeypatch.setattr(
        run_h.runner,
        "build_analytics_outputs",
        lambda df, period: runner.build_analytics_outputs(df, period=period, n_boot=30),
    )

    out = run_h.run_for_period(None, s3, {"period": "season_2025_03-05"}, bucket="test-bucket", workgroup="wg")
    assert out["analytics"]["status"] == "PUBLISHED"
    assert "curated/_success/analytics/season_2025_03-05.json" in s3.objects
    assert "curated/analytics/regression/period=season_2025_03-05/match=all.json" in s3.objects
    assert "curated/analytics/regression/period=season_2025_03-05/part.parquet" in s3.objects

    reg_json = json.loads(s3.objects["curated/analytics/regression/period=season_2025_03-05/match=all.json"])
    assert reg_json["period"] == "season_2025_03-05"
    assert reg_json["match"] == "all"
