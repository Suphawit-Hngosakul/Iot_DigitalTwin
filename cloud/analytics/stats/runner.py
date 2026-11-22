"""Orchestrate S1-S8 calculation across match sets ('window', 'all') and build Parquet/JSON outputs."""

from __future__ import annotations

import pandas as pd

from .core import RANDOM_SEED, analyze_period_match

MATCH_SETS = ("window", "all")


def build_analytics_outputs(
    df_period: pd.DataFrame,
    period: str,
    seed: int = RANDOM_SEED,
    n_boot: int = 500,
) -> dict:
    """Compute S1-S8 + cell_attribution for both 'window' and 'all' match sets.

    Returns:
        {
          "period": period,
          "json": { "<report_name>": { "window": {...}, "all": {...} } },
          "tables": { "<report_name>": pd.DataFrame },
        }
    """
    by_match: dict[str, dict[str, dict]] = {}
    for m in MATCH_SETS:
        if "match_type" in df_period.columns:
            sub = df_period[df_period["match_type"] == m].reset_index(drop=True)
        else:
            sub = df_period.reset_index(drop=True)
        by_match[m] = analyze_period_match(sub, period=period, match=m, seed=seed, n_boot=n_boot)

    report_names = list(by_match["all"].keys())
    json_out: dict[str, dict[str, dict]] = {name: {} for name in report_names}
    for m in MATCH_SETS:
        for name in report_names:
            json_out[name][m] = by_match[m][name]

    # Flatten key reports into Parquet-ready DataFrames (Spec 60 §4)
    tables: dict[str, pd.DataFrame] = {}

    summary_rows = []
    for m in MATCH_SETS:
        for r in by_match[m]["summary"]["rows"]:
            summary_rows.append({"period": period, "match_type": m, **r})
    tables["summary"] = pd.DataFrame(summary_rows)

    reg_rows = []
    for m in MATCH_SETS:
        for mod in by_match[m]["regression"]["models"]:
            for t in mod.get("terms", []):
                reg_rows.append({
                    "period": period,
                    "match_type": m,
                    "model": mod["model"],
                    "formula": mod["formula"],
                    "n": mod["n"],
                    "r2": mod["r2"],
                    "adj_r2": mod["adj_r2"],
                    **t,
                })
    tables["regression"] = pd.DataFrame(reg_rows)

    imp_rows = []
    for m in MATCH_SETS:
        for f in by_match[m]["importance"].get("features", []):
            imp_rows.append({"period": period, "match_type": m, **f})
    tables["importance"] = pd.DataFrame(imp_rows)

    hs_rows = []
    for m in MATCH_SETS:
        for c in by_match[m]["hotspot"].get("cells", []):
            hs_rows.append({"period": period, "match_type": m, **c})
    tables["hotspot"] = pd.DataFrame(hs_rows)

    sa_rows = []
    for m in MATCH_SETS:
        for r in by_match[m]["spatial_autocorr"].get("rows", []):
            sa_rows.append({"period": period, "match_type": m, **r})
    tables["spatial_autocorr"] = pd.DataFrame(sa_rows)

    grp_rows = []
    for m in MATCH_SETS:
        for g in by_match[m]["groups"].get("groups", []):
            grp_rows.append({"period": period, "match_type": m, **g})
    tables["groups"] = pd.DataFrame(grp_rows)

    return {
        "period": period,
        "json": json_out,
        "tables": tables,
    }
