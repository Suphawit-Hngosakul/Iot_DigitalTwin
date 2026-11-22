"""Unit tests for cloud/analytics/stats/core.py (Issue #33)."""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from stats import core


def _synthetic_grid(n_cols: int = 12, n_rows: int = 12, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(n_rows):
        for c in range(n_cols):
            # Create spatial gradient: NW is green/cool, SE is dense/hot
            grad = (c + r) / (n_cols + n_rows - 2)
            ndvi = float(np.clip(0.65 - 0.50 * grad + rng.normal(0, 0.04), -0.1, 0.9))
            ndbi = float(np.clip(-0.25 + 0.55 * grad + rng.normal(0, 0.04), -0.5, 0.6))
            builtup = float(np.clip(10.0 + 75.0 * grad + rng.normal(0, 4.0), 0.0, 100.0))
            lst = 33.0 + 7.5 * ndbi - 3.2 * ndvi + 0.02 * builtup + float(rng.normal(0, 0.35))
            # Simulate IoT on a subset of cells (~25 cells)
            has_iot = (c == r) or (abs(c - r) == 1 and c < 8)
            air_temp = (29.0 + 0.35 * (lst - 35.0) + float(rng.normal(0, 0.25))) if has_iot else np.nan
            db_spl = (58.0 + 0.15 * builtup + float(rng.normal(0, 1.5))) if has_iot else np.nan
            rows.append({
                "cell_id": f"c{c:04d}_r{r:04d}",
                "period": "season_2025_03-05",
                "match_type": "window",
                "in_cover": True,
                "lst_mean": round(lst, 3),
                "ndvi_mean": round(ndvi, 4),
                "ndbi_mean": round(ndbi, 4),
                "builtup_pct": round(builtup, 2),
                "air_temp_mean": round(air_temp, 2) if has_iot else np.nan,
                "db_spl_mean": round(db_spl, 1) if has_iot else np.nan,
                "n_iot_minutes": 12 if has_iot else 0,
            })
    return pd.DataFrame(rows)


def test_full_analytics_is_json_safe_and_reproducible():
    df = _synthetic_grid()
    out1 = core.analyze_period_match(df, period="season_2025_03-05", match="window", seed=42, n_boot=100)
    out2 = core.analyze_period_match(df, period="season_2025_03-05", match="window", seed=42, n_boot=100)

    # Strict RFC 8259 JSON serialization (allow_nan=False)
    raw_json = json.dumps(out1, allow_nan=False)
    assert len(raw_json) > 1000

    # Deterministic reproducibility
    assert out1["regression"] == out2["regression"]
    assert out1["importance"] == out2["importance"]
    assert out1["spatial_autocorr"] == out2["spatial_autocorr"]


def test_s3_regression_reports_vif_and_bootstrap_and_block_cv():
    df = _synthetic_grid()
    # Make builtup_pct strongly collinear with ndbi_mean to trigger VIF > 5
    df["builtup_pct"] = 40.0 + 120.0 * df["ndbi_mean"] + np.random.default_rng(1).normal(0, 0.5, len(df))

    reg, resid_df = core.compute_regression(df, "lst_mean", core.DEFAULT_REGRESSORS_S3, "s3", seed=42, n_boot=100)
    assert reg["n"] == len(df)
    assert reg["r2"] > 0.85
    assert reg["vif_warning"] is True
    assert any("VIF" in w for w in reg["warnings"])
    assert reg["spatial_block_cv"]["n_blocks"] >= 4
    assert reg["spatial_block_cv"]["cv_r2"] is not None
    assert len(resid_df) == len(df)

    for term in reg["terms"]:
        assert term["boot_ci_95_low"] <= term["coef"] <= term["boot_ci_95_high"]
        assert "evidence_status" in term


def test_hotspot_and_morans_i_detect_spatial_clustering():
    df = _synthetic_grid()
    hs = core.compute_hotspot_gi_star(df, "lst_mean", distance_m=300.0)
    classes = {c["class"] for c in hs["cells"]}
    assert "hot_99" in classes
    assert "cold_99" in classes

    mi = core.compute_morans_i(df, df["lst_mean"], "lst_mean")
    assert mi["morans_i"] > 0.5
    assert mi["p_value"] < 0.001


def test_groups_kruskal_wallis_and_iot_caveats():
    df = _synthetic_grid()
    grp = core.compute_groups(df, "ndvi_mean", "lst_mean")
    assert len(grp["groups"]) == 3
    assert grp["diff_dense_minus_green"] > 2.0
    assert grp["test"]["p_value"] < 0.001

    val = core.validate_iot_vs_lst(df)
    assert val["n_matched_cells"] > 10
    assert val["spearman_r"] is not None
    assert len(val["caveats"]) == 3


def test_historical_period_without_iot_does_not_crash():
    df = _synthetic_grid()
    df["air_temp_mean"] = np.nan
    df["db_spl_mean"] = np.nan
    out = core.analyze_period_match(df, period="season_2024_03-05", match="all", seed=42, n_boot=50)
    s4 = [m for m in out["regression"]["models"] if m["model"] == "s4"][0]
    assert s4["n"] == 0
    assert s4["r2"] is None
    assert "caveats" in s4
