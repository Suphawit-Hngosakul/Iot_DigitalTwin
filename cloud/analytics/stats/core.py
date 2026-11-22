"""Core statistical routines for Issue #33 (Spec 60 §3).

Implemented purely with NumPy and Pandas so it runs deterministically inside AWS Lambda
using the standard AWS SDK for pandas layer without extra binary dependencies.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_VARIABLES = [
    "lst_mean",
    "ndvi_mean",
    "ndbi_mean",
    "builtup_pct",
    "air_temp_mean",
    "db_spl_mean",
]
DEFAULT_REGRESSORS_S3 = ["ndvi_mean", "ndbi_mean", "builtup_pct"]
DEFAULT_REGRESSORS_S4 = ["lst_mean", "ndvi_mean", "builtup_pct"]

GRID_SIZE_M = 90.0
GI_DISTANCE_M = 300.0
RF_TREES = 300
RANDOM_SEED = 42
BLOCK_SIZE_CELLS = 5  # 5 x 90m = 450m spatial blocks (Roberts et al., 2017)

_CELL_RE = re.compile(r"^c(?P<col>\d{4})_r(?P<row>\d{4})$")

IOT_VALIDATION_CAVEATS = [
    "ข้อมูลเซนเซอร์ภาคสนามมีเพียง 1 วัน (1 มี.ค. 2026) ระยะเวลาประมาณ 1 ชั่วโมง 18 นาที ตามเส้นทางเดินเดียว ครอบคลุมเซลล์ขนาด 90 ม. เพียงไม่กี่สิบเซลล์ ทำให้พลังทางสถิติ (statistical power) ต่ำ",
    "ค่า LST ในตารางเป็นค่าจากภาพถ่ายดาวเทียมช่วงเดือน มี.ค.–พ.ค. ปี 2021–2025 ส่วนข้อมูลเซนเซอร์เก็บในวันเดียวของปี 2026 ช่วงเวลาจึงไม่ตรงกันโดยสมบูรณ์",
    "LST คืออุณหภูมิพื้นผิว (Land Surface Temperature) ขณะที่เซนเซอร์วัดอุณหภูมิอากาศ (Air Temperature) ซึ่งแตกต่างกันตามธรรมชาติ จึงควรดูความสอดคล้องของอันดับสูง–ต่ำ (rank order) ไม่ใช่การเทียบตัวเลขให้เท่ากัน",
]


def sanitize(obj: Any) -> Any:
    """Recursively convert NumPy scalars and NaN/Inf floats to strict JSON-safe types."""
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        val = float(obj)
        return None if (math.isnan(val) or math.isinf(val)) else val
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    return obj


def _r(val: float | None, digits: int = 4) -> float | None:
    if val is None or math.isnan(val) or math.isinf(val):
        return None
    return round(float(val), digits)


def parse_cell_coords(cell_ids: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    cols = np.empty(len(cell_ids), dtype=np.int32)
    rows = np.empty(len(cell_ids), dtype=np.int32)
    for i, cid in enumerate(cell_ids):
        m = _CELL_RE.match(str(cid))
        if not m:
            raise ValueError(f"Invalid cell_id format: {cid!r}")
        cols[i] = int(m["col"])
        rows[i] = int(m["row"])
    return cols, rows


# ---------------------------------------------------------------------------
# Exact p-values (Student's t & Normal) via regularized incomplete beta
# ---------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    max_iter = 200
    eps = 3e-14
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_pvalue(t_stat: float, df: int) -> float:
    if df <= 0 or math.isnan(t_stat):
        return 1.0
    if math.isinf(t_stat):
        return 0.0
    x = df / (df + t_stat * t_stat)
    return float(min(1.0, max(0.0, betainc(0.5 * df, 0.5, x))))


def normal_pvalue(z_stat: float) -> float:
    if math.isnan(z_stat):
        return 1.0
    return float(math.erfc(abs(z_stat) / math.sqrt(2.0)))


def t_crit_95(df: int) -> float:
    """Two-tailed 95% critical value for Student's t via binary search on t_pvalue."""
    if df <= 0:
        return 1.96
    lo, hi = 1.95, 15.0
    for _ in range(45):
        mid = 0.5 * (lo + hi)
        if t_pvalue(mid, df) > 0.05:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# S1: Descriptive Statistics
# ---------------------------------------------------------------------------
def compute_summary(df: pd.DataFrame, variables: list[str]) -> list[dict]:
    rows = []
    for var in variables:
        if var not in df.columns:
            rows.append({"variable": var, "n": 0, "mean": None, "sd": None, "min": None,
                         "p25": None, "median": None, "p75": None, "max": None, "p90": None})
            continue
        s = pd.to_numeric(df[var], errors="coerce").dropna()
        n = int(len(s))
        if n == 0:
            rows.append({"variable": var, "n": 0, "mean": None, "sd": None, "min": None,
                         "p25": None, "median": None, "p75": None, "max": None, "p90": None})
            continue
        arr = s.to_numpy(dtype=np.float64)
        rows.append({
            "variable": var,
            "n": n,
            "mean": _r(float(np.mean(arr)), 3),
            "sd": _r(float(np.std(arr, ddof=1)) if n > 1 else 0.0, 3),
            "min": _r(float(np.min(arr)), 3),
            "p25": _r(float(np.percentile(arr, 25)), 3),
            "median": _r(float(np.percentile(arr, 50)), 3),
            "p75": _r(float(np.percentile(arr, 75)), 3),
            "max": _r(float(np.max(arr)), 3),
            "p90": _r(float(np.percentile(arr, 90)), 3),
        })
    return rows


# ---------------------------------------------------------------------------
# S2: Correlation Matrix (Pearson + Spearman Rank)
# ---------------------------------------------------------------------------
def _pair_corr(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    n = len(x)
    if n < 3:
        return None, None
    dx = x - np.mean(x)
    dy = y - np.mean(y)
    denom = math.sqrt(float(np.sum(dx * dx) * np.sum(dy * dy)))
    if denom <= 1e-15:
        return None, None
    r = float(np.clip(np.sum(dx * dy) / denom, -1.0, 1.0))
    if abs(r) >= 1.0 - 1e-12:
        return r, 0.0
    t_val = r * math.sqrt((n - 2) / (1.0 - r * r))
    return r, t_pvalue(t_val, n - 2)


def compute_correlation(df: pd.DataFrame, variables: list[str]) -> dict:
    k = len(variables)
    r_mat = [[None] * k for _ in range(k)]
    p_mat = [[None] * k for _ in range(k)]
    sr_mat = [[None] * k for _ in range(k)]
    sp_mat = [[None] * k for _ in range(k)]
    n_mat = [[0] * k for _ in range(k)]

    numeric = {
        v: pd.to_numeric(df[v], errors="coerce") if v in df.columns else pd.Series(np.nan, index=df.index)
        for v in variables
    }

    for i, a in enumerate(variables):
        for j, b in enumerate(variables):
            pair = pd.DataFrame({"a": numeric[a], "b": numeric[b]}).dropna()
            n = int(len(pair))
            n_mat[i][j] = n
            if n == 0:
                continue
            if i == j:
                r_mat[i][j] = 1.0
                p_mat[i][j] = 0.0
                sr_mat[i][j] = 1.0
                sp_mat[i][j] = 0.0
                continue
            xa = pair["a"].to_numpy(dtype=np.float64)
            xb = pair["b"].to_numpy(dtype=np.float64)
            pr, pp = _pair_corr(xa, xb)
            ra = pair["a"].rank(method="average").to_numpy(dtype=np.float64)
            rb = pair["b"].rank(method="average").to_numpy(dtype=np.float64)
            sr, sp = _pair_corr(ra, rb)
            r_mat[i][j] = _r(pr, 3)
            p_mat[i][j] = _r(pp, 4)
            sr_mat[i][j] = _r(sr, 3)
            sp_mat[i][j] = _r(sp, 4)

    return {
        "method": "pearson",
        "variables": list(variables),
        "r": r_mat,
        "p_value": p_mat,
        "spearman_r": sr_mat,
        "spearman_p": sp_mat,
        "n": n_mat,
        "iot_lst_validation": validate_iot_vs_lst(df),
    }


def validate_iot_vs_lst(df: pd.DataFrame) -> dict:
    """Independent rank-order comparison between field IoT air temp and satellite LST."""
    if "lst_mean" not in df.columns or "air_temp_mean" not in df.columns:
        return {
            "n_matched_cells": 0,
            "spearman_r": None,
            "spearman_p": None,
            "pearson_r": None,
            "pearson_p": None,
            "status": "no_iot_overlap",
            "caveats": IOT_VALIDATION_CAVEATS,
        }
    sub = df[["lst_mean", "air_temp_mean"]].apply(pd.to_numeric, errors="coerce").dropna()
    n = int(len(sub))
    if n < 3:
        return {
            "n_matched_cells": n,
            "spearman_r": None,
            "spearman_p": None,
            "pearson_r": None,
            "pearson_p": None,
            "status": "insufficient_matched_cells",
            "caveats": IOT_VALIDATION_CAVEATS,
        }
    lst = sub["lst_mean"].to_numpy(dtype=np.float64)
    air = sub["air_temp_mean"].to_numpy(dtype=np.float64)
    pr, pp = _pair_corr(lst, air)
    sr, sp = _pair_corr(
        sub["lst_mean"].rank(method="average").to_numpy(dtype=np.float64),
        sub["air_temp_mean"].rank(method="average").to_numpy(dtype=np.float64),
    )
    return {
        "n_matched_cells": n,
        "spearman_r": _r(sr, 3),
        "spearman_p": _r(sp, 4),
        "pearson_r": _r(pr, 3),
        "pearson_p": _r(pp, 4),
        "evaluation_basis": "rank_order_consistency",
        "status": "evaluated_with_caveats",
        "caveats": IOT_VALIDATION_CAVEATS,
    }


# ---------------------------------------------------------------------------
# S3 & S4: Multiple Linear Regression + VIF + Bootstrap + Spatial Block CV
# ---------------------------------------------------------------------------
def _fit_ols_arrays(y: np.ndarray, X: np.ndarray) -> dict:
    n, p = X.shape
    X_design = np.column_stack([np.ones(n, dtype=np.float64), X])
    beta, _, _, _ = np.linalg.lstsq(X_design, y, rcond=None)
    yhat = X_design @ beta
    res = y - yhat
    sse = float(np.sum(res * res))
    sst = float(np.sum((y - np.mean(y)) ** 2))
    df_resid = max(1, n - (p + 1))
    s2 = sse / df_resid
    cov_beta = s2 * np.linalg.pinv(X_design.T @ X_design)
    se = np.sqrt(np.maximum(0.0, np.diag(cov_beta)))
    r2 = 1.0 - (sse / sst) if sst > 1e-15 else 0.0
    adj_r2 = 1.0 - (1.0 - r2) * ((n - 1) / df_resid) if n > p + 1 else r2
    return {
        "beta": beta,
        "se": se,
        "r2": r2,
        "adj_r2": adj_r2,
        "rmse": math.sqrt(sse / max(1, n)),
        "residuals": res,
        "fitted": yhat,
        "df_resid": df_resid,
    }


def _compute_vifs(X: np.ndarray) -> list[float]:
    n, p = X.shape
    if p <= 1:
        return [1.0] * p
    vifs = []
    for j in range(p):
        yj = X[:, j]
        others = np.delete(X, j, axis=1)
        fit = _fit_ols_arrays(yj, others)
        r2_j = min(0.999999, max(0.0, fit["r2"]))
        vifs.append(1.0 / (1.0 - r2_j))
    return vifs


def _bootstrap_slopes(y: np.ndarray, X: np.ndarray, n_boot: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    n, p = X.shape
    rng = np.random.default_rng(seed)
    slopes = np.empty((n_boot, p), dtype=np.float64)
    X_design = np.column_stack([np.ones(n, dtype=np.float64), X])
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        beta_b, _, _, _ = np.linalg.lstsq(X_design[idx], y[idx], rcond=None)
        slopes[b] = beta_b[1:]
    lo = np.percentile(slopes, 2.5, axis=0)
    hi = np.percentile(slopes, 97.5, axis=0)
    return lo, hi


def spatial_block_cv(
    df: pd.DataFrame,
    y_col: str,
    x_cols: list[str],
    block_size_cells: int = BLOCK_SIZE_CELLS,
) -> dict:
    """Spatial block cross-validation (Roberts et al., 2017) using grid block coordinates."""
    if "cell_id" not in df.columns or len(df) < len(x_cols) + 10:
        return {
            "method": "spatial_block_cv",
            "reference": "Roberts et al. (2017)",
            "block_size_cells": block_size_cells,
            "block_size_m": int(block_size_cells * GRID_SIZE_M),
            "n_blocks": 0,
            "cv_r2": None,
            "cv_rmse": None,
        }
    cols, rows = parse_cell_coords(df["cell_id"])
    block_ids = (cols // block_size_cells) * 10000 + (rows // block_size_cells)
    unique_blocks = np.unique(block_ids)
    if len(unique_blocks) < 3:
        return {
            "method": "spatial_block_cv",
            "reference": "Roberts et al. (2017)",
            "block_size_cells": block_size_cells,
            "block_size_m": int(block_size_cells * GRID_SIZE_M),
            "n_blocks": int(len(unique_blocks)),
            "cv_r2": None,
            "cv_rmse": None,
        }

    y = df[y_col].to_numpy(dtype=np.float64)
    X = df[x_cols].to_numpy(dtype=np.float64)
    oof_pred = np.full(len(df), np.nan, dtype=np.float64)

    # Group blocks deterministically into up to 10 spatial folds
    n_folds = min(10, len(unique_blocks))
    block_to_fold = {b: i % n_folds for i, b in enumerate(sorted(unique_blocks))}
    folds = np.array([block_to_fold[b] for b in block_ids])

    for f in range(n_folds):
        test_mask = folds == f
        train_mask = ~test_mask
        if np.sum(train_mask) <= len(x_cols) + 2 or np.sum(test_mask) == 0:
            continue
        fit = _fit_ols_arrays(y[train_mask], X[train_mask])
        X_test_design = np.column_stack([np.ones(np.sum(test_mask)), X[test_mask]])
        oof_pred[test_mask] = X_test_design @ fit["beta"]

    valid = ~np.isnan(oof_pred)
    if np.sum(valid) < 5:
        return {
            "method": "spatial_block_cv",
            "reference": "Roberts et al. (2017)",
            "block_size_cells": block_size_cells,
            "block_size_m": int(block_size_cells * GRID_SIZE_M),
            "n_blocks": int(len(unique_blocks)),
            "cv_r2": None,
            "cv_rmse": None,
        }
    yv = y[valid]
    pv = oof_pred[valid]
    sse = float(np.sum((yv - pv) ** 2))
    sst = float(np.sum((yv - np.mean(yv)) ** 2))
    cv_r2 = 1.0 - (sse / sst) if sst > 1e-15 else 0.0
    cv_rmse = math.sqrt(sse / len(yv))
    return {
        "method": "spatial_block_cv",
        "reference": "Roberts et al. (2017)",
        "block_size_cells": block_size_cells,
        "block_size_m": int(block_size_cells * GRID_SIZE_M),
        "n_blocks": int(len(unique_blocks)),
        "n_folds": int(n_folds),
        "cv_r2": _r(cv_r2, 3),
        "cv_rmse": _r(cv_rmse, 3),
    }


def compute_regression(
    df: pd.DataFrame,
    y_col: str,
    x_cols: list[str],
    model_name: str,
    seed: int = RANDOM_SEED,
    n_boot: int = 500,
) -> tuple[dict, pd.DataFrame]:
    cols_needed = [c for c in ["cell_id", y_col, *x_cols] if c in df.columns]
    if y_col not in df.columns or any(x not in df.columns for x in x_cols):
        return {
            "model": model_name,
            "formula": f"{y_col} ~ {' + '.join(x_cols)}",
            "n": 0,
            "r2": None,
            "adj_r2": None,
            "terms": [],
            "vif_warning": False,
            "warnings": ["คอลัมน์ตัวแปรไม่ครบในชุดข้อมูล"],
        }, pd.DataFrame(columns=["cell_id", "fitted", "residual"])

    sub = df[cols_needed].copy()
    for col in [y_col, *x_cols]:
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[y_col, *x_cols]).reset_index(drop=True)
    n = int(len(sub))
    formula = f"{y_col} ~ {' + '.join(x_cols)}"

    if n < len(x_cols) + 5:
        out = {
            "model": model_name,
            "formula": formula,
            "n": n,
            "r2": None,
            "adj_r2": None,
            "terms": [],
            "vif_warning": False,
            "warnings": [f"ข้อมูลมีจำนวนเซลล์ที่สมบูรณ์เพียง {n} เซลล์ ไม่เพียงพอสำหรับถดถอยพหุ"],
        }
        if y_col == "air_temp_mean":
            out["caveats"] = IOT_VALIDATION_CAVEATS
        return out, pd.DataFrame(columns=["cell_id", "fitted", "residual"])

    y = sub[y_col].to_numpy(dtype=np.float64)
    X = sub[x_cols].to_numpy(dtype=np.float64)
    fit = _fit_ols_arrays(y, X)
    vifs = _compute_vifs(X)
    boot_lo, boot_hi = _bootstrap_slopes(y, X, n_boot=n_boot, seed=seed)
    t_crit = t_crit_95(fit["df_resid"])
    sy = float(np.std(y, ddof=1))

    terms = []
    warnings = []
    vif_warning = False

    for j, name in enumerate(x_cols):
        coef = float(fit["beta"][j + 1])
        se = float(fit["se"][j + 1])
        t_stat = coef / se if se > 1e-15 else 0.0
        p_val = t_pvalue(t_stat, fit["df_resid"])
        sx = float(np.std(X[:, j], ddof=1))
        std_coef = (coef * sx / sy) if sy > 1e-15 else 0.0
        vif = float(vifs[j])
        b_lo = float(boot_lo[j])
        b_hi = float(boot_hi[j])

        # Project-defined descriptive P75 vs P25 shift (explicitly labeled as project-defined)
        p25_x = float(np.percentile(X[:, j], 25))
        p75_x = float(np.percentile(X[:, j], 75))
        p75_p25_shift = coef * (p75_x - p25_x)

        if vif > 5.0:
            vif_warning = True
            warnings.append(f"ตัวแปร {name} มีค่า VIF = {vif:.2f} (> 5.0) แสดงถึงภาวะร่วมเส้นตรงพหุ (multicollinearity) สูง")

        boot_excludes_zero = (b_lo > 0.0 and b_hi > 0.0) or (b_lo < 0.0 and b_hi < 0.0)
        if p_val < 0.05 and vif <= 5.0 and boot_excludes_zero:
            status = "strong_statistical_evidence"
            status_th = "มีหลักฐานทางสถิติชัดเจนในชุดข้อมูลนี้"
        elif p_val < 0.05 and vif > 5.0:
            status = "collinear_hypothesis"
            status_th = "สมมติฐานที่ต้องตรวจสอบต่อ (ค่าซ้อนทับกับตัวแปรอื่น VIF > 5)"
        else:
            status = "hypothesis_requires_further_investigation"
            status_th = "สมมติฐานที่ต้องตรวจสอบต่อ (ยังสรุปผลเชิงสาเหตุไม่ได้ชัดเจน)"

        terms.append({
            "term": name,
            "coef": _r(coef, 4),
            "se": _r(se, 4),
            "std_coef": _r(std_coef, 3),
            "p_value": _r(p_val, 4),
            "ci_95_low": _r(coef - t_crit * se, 4),
            "ci_95_high": _r(coef + t_crit * se, 4),
            "boot_ci_95_low": _r(b_lo, 4),
            "boot_ci_95_high": _r(b_hi, 4),
            "vif": _r(vif, 2),
            "project_defined_p25_to_p75_effect": _r(p75_p25_shift, 3),
            "evidence_status": status,
            "evidence_label_th": status_th,
        })

    block_cv = spatial_block_cv(sub, y_col, x_cols)
    resid_df = pd.DataFrame({
        "cell_id": sub["cell_id"] if "cell_id" in sub.columns else np.arange(n),
        "fitted": fit["fitted"],
        "residual": fit["residuals"],
    })

    result = {
        "model": model_name,
        "formula": formula,
        "n": n,
        "intercept": _r(float(fit["beta"][0]), 4),
        "r2": _r(fit["r2"], 3),
        "adj_r2": _r(fit["adj_r2"], 3),
        "rmse": _r(fit["rmse"], 3),
        "terms": terms,
        "vif_warning": vif_warning,
        "warnings": warnings,
        "spatial_block_cv": block_cv,
        "methodology_note": "ใช้วิธีทางสถิติมาตรฐาน (OLS, VIF, Bootstrap 95% CI, Spatial Block CV; Roberts et al., 2017) ส่วนการเปรียบเทียบช่วง P25–P75 เป็นตัวชี้วัดเชิงพรรณนาที่โครงการกำหนดขึ้นเอง",
    }
    if y_col == "air_temp_mean":
        result["caveats"] = IOT_VALIDATION_CAVEATS
    return result, resid_df


# ---------------------------------------------------------------------------
# S5: Random Forest Regression + OOB Permutation Importance (Pure NumPy)
# ---------------------------------------------------------------------------
class _FastRegressionTree:
    """Lightweight deterministic CART regression tree for tabular RF importance."""

    def __init__(self, max_depth: int = 8, min_samples_leaf: int = 10, max_features: int = 2):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.max_features = max_features
        self.tree: dict[str, Any] = {}

    def fit(self, X: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> None:
        self.tree = self._build(X, y, depth=0, rng=rng)

    def _build(self, X: np.ndarray, y: np.ndarray, depth: int, rng: np.random.Generator) -> dict:
        n, p = X.shape
        mean_val = float(np.mean(y))
        if depth >= self.max_depth or n < 2 * self.min_samples_leaf or np.var(y) <= 1e-12:
            return {"leaf": True, "val": mean_val}

        feat_candidates = rng.choice(p, size=min(p, self.max_features), replace=False)
        best_gain = 0.0
        best_feat = -1
        best_thresh = 0.0
        total_sum = float(np.sum(y))
        parent_score = (total_sum * total_sum) / n

        for f in feat_candidates:
            col = X[:, f]
            order = np.argsort(col)
            col_s = col[order]
            y_s = y[order]
            # Evaluate candidate quantile thresholds for speed and stability
            step = max(1, (n - 2 * self.min_samples_leaf) // 16)
            cand_indices = range(self.min_samples_leaf, n - self.min_samples_leaf + 1, step)
            csum = np.cumsum(y_s)
            for idx in cand_indices:
                if col_s[idx - 1] == col_s[idx]:
                    continue
                left_sum = float(csum[idx - 1])
                right_sum = total_sum - left_sum
                score = (left_sum * left_sum) / idx + (right_sum * right_sum) / (n - idx)
                gain = score - parent_score
                if gain > best_gain:
                    best_gain = gain
                    best_feat = int(f)
                    best_thresh = 0.5 * float(col_s[idx - 1] + col_s[idx])

        if best_feat == -1:
            return {"leaf": True, "val": mean_val}

        left_mask = X[:, best_feat] <= best_thresh
        return {
            "leaf": False,
            "feat": best_feat,
            "thresh": best_thresh,
            "left": self._build(X[left_mask], y[left_mask], depth + 1, rng),
            "right": self._build(X[~left_mask], y[~left_mask], depth + 1, rng),
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        out = np.empty(len(X), dtype=np.float64)
        for i in range(len(X)):
            node = self.tree
            row = X[i]
            while not node["leaf"]:
                node = node["left"] if row[node["feat"]] <= node["thresh"] else node["right"]
            out[i] = node["val"]
        return out


def compute_rf_importance(
    df: pd.DataFrame,
    y_col: str = "lst_mean",
    x_cols: list[str] = DEFAULT_REGRESSORS_S3,
    n_trees: int = RF_TREES,
    seed: int = RANDOM_SEED,
    n_repeats: int = 10,
) -> dict:
    cols = [y_col, *x_cols]
    if any(c not in df.columns for c in cols):
        return {
            "model": "random_forest",
            "trees": n_trees,
            "random_seed": seed,
            "method": "permutation",
            "features": [],
            "note": "คอลัมน์ตัวแปรไม่ครบ",
        }
    sub = df[cols].apply(pd.to_numeric, errors="coerce").dropna().reset_index(drop=True)
    n = int(len(sub))
    if n < 20:
        return {
            "model": "random_forest",
            "trees": n_trees,
            "random_seed": seed,
            "method": "permutation",
            "features": [],
            "note": f"ข้อมูลมี {n} แถว ไม่เพียงพอสำหรับ Random Forest",
        }

    y = sub[y_col].to_numpy(dtype=np.float64)
    X = sub[x_cols].to_numpy(dtype=np.float64)
    p = len(x_cols)
    rng = np.random.default_rng(seed)

    oob_sum = np.zeros(n, dtype=np.float64)
    oob_cnt = np.zeros(n, dtype=np.int32)
    trees: list[tuple[_FastRegressionTree, np.ndarray]] = []
    max_feat = max(1, int(round(math.sqrt(p))))

    for _ in range(n_trees):
        boot_idx = rng.integers(0, n, size=n)
        in_bag = np.zeros(n, dtype=bool)
        in_bag[boot_idx] = True
        oob_idx = np.where(~in_bag)[0]

        tree = _FastRegressionTree(max_depth=8, min_samples_leaf=max(5, n // 200), max_features=max_feat)
        tree.fit(X[boot_idx], y[boot_idx], rng)
        trees.append((tree, oob_idx))
        if len(oob_idx) > 0:
            preds = tree.predict(X[oob_idx])
            oob_sum[oob_idx] += preds
            oob_cnt[oob_idx] += 1

    valid_oob = oob_cnt > 0
    oob_pred = np.where(valid_oob, oob_sum / np.maximum(1, oob_cnt), np.mean(y))
    base_mse = float(np.mean((y[valid_oob] - oob_pred[valid_oob]) ** 2))
    sst = float(np.sum((y[valid_oob] - np.mean(y[valid_oob])) ** 2))
    oob_r2 = 1.0 - (float(np.sum((y[valid_oob] - oob_pred[valid_oob]) ** 2)) / sst) if sst > 1e-15 else 0.0

    # Permutation importance across OOB predictions
    raw_deltas = np.zeros((n_repeats, p), dtype=np.float64)
    for rep in range(n_repeats):
        for j in range(p):
            X_perm = X.copy()
            X_perm[:, j] = rng.permutation(X_perm[:, j])
            p_sum = np.zeros(n, dtype=np.float64)
            for tree, oob_idx in trees:
                if len(oob_idx) > 0:
                    p_sum[oob_idx] += tree.predict(X_perm[oob_idx])
            perm_pred = p_sum[valid_oob] / oob_cnt[valid_oob]
            perm_mse = float(np.mean((y[valid_oob] - perm_pred) ** 2))
            raw_deltas[rep, j] = max(0.0, perm_mse - base_mse)

    mean_deltas = np.mean(raw_deltas, axis=0)
    std_deltas = np.std(raw_deltas, axis=0, ddof=1) if n_repeats > 1 else np.zeros(p)
    tot_imp = float(np.sum(mean_deltas))

    features = []
    for j, name in enumerate(x_cols):
        rel_imp = float(mean_deltas[j] / tot_imp) if tot_imp > 1e-15 else 0.0
        rel_std = float(std_deltas[j] / tot_imp) if tot_imp > 1e-15 else 0.0
        features.append({
            "feature": name,
            "importance": _r(rel_imp, 3),
            "std": _r(rel_std, 3),
            "mse_increase": _r(float(mean_deltas[j]), 4),
        })

    features.sort(key=lambda item: item["importance"] or 0.0, reverse=True)
    return {
        "model": "random_forest",
        "target": y_col,
        "trees": n_trees,
        "random_seed": seed,
        "method": "permutation",
        "oob_r2": _r(oob_r2, 3),
        "oob_rmse": _r(math.sqrt(base_mse), 3),
        "features": features,
        "note": "คำนวณจาก Random Forest (OOB permutation importance, seed=42) เพื่อตรวจสอบลำดับความสำคัญแบบไม่เป็นเส้นตรงเทียบกับ S3",
    }


# ---------------------------------------------------------------------------
# S6: Getis-Ord Gi* Hot Spot Analysis (300m radius on 90m grid)
# ---------------------------------------------------------------------------
def compute_hotspot_gi_star(
    df: pd.DataFrame,
    value_col: str = "lst_mean",
    distance_m: float = GI_DISTANCE_M,
) -> dict:
    if "cell_id" not in df.columns or value_col not in df.columns:
        return {"variable": value_col, "distance_m": int(distance_m), "cells": []}

    sub = df[["cell_id", value_col]].copy()
    sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce")
    sub = sub.dropna(subset=[value_col]).reset_index(drop=True)
    n = int(len(sub))
    if n < 5:
        return {"variable": value_col, "distance_m": int(distance_m), "cells": []}

    cols, rows = parse_cell_coords(sub["cell_id"])
    vals = sub[value_col].to_numpy(dtype=np.float64)
    x_bar = float(np.mean(vals))
    s_std = float(np.sqrt(np.mean(vals * vals) - x_bar * x_bar))
    if s_std <= 1e-15:
        return {
            "variable": value_col,
            "distance_m": int(distance_m),
            "cells": [{"cell_id": cid, "gi_z": 0.0, "gi_p": 1.0, "class": "not_significant"} for cid in sub["cell_id"]],
        }

    max_cells = distance_m / GRID_SIZE_M
    max_cells_sq = max_cells * max_cells
    r_int = int(math.floor(max_cells))
    coord_map = {(int(cols[i]), int(rows[i])): i for i in range(n)}

    offsets = [
        (dc, dr)
        for dc in range(-r_int, r_int + 1)
        for dr in range(-r_int, r_int + 1)
        if (dc * dc + dr * dr) <= max_cells_sq
    ]

    cells_out = []
    for i in range(n):
        c0, r0 = int(cols[i]), int(rows[i])
        sx = 0.0
        w_sum = 0
        for dc, dr in offsets:
            j = coord_map.get((c0 + dc, r0 + dr))
            if j is not None:
                sx += vals[j]
                w_sum += 1
        denom_inner = (n * w_sum - w_sum * w_sum) / max(1, n - 1)
        if denom_inner <= 1e-15:
            z = 0.0
        else:
            z = (sx - x_bar * w_sum) / (s_std * math.sqrt(denom_inner))
        p_val = normal_pvalue(z)
        if p_val < 0.01:
            cls = "hot_99" if z > 0 else "cold_99"
        elif p_val < 0.05:
            cls = "hot_95" if z > 0 else "cold_95"
        else:
            cls = "not_significant"
        cells_out.append({
            "cell_id": str(sub["cell_id"].iloc[i]),
            "gi_z": _r(z, 2),
            "gi_p": _r(p_val, 4),
            "class": cls,
        })

    return {
        "variable": value_col,
        "distance_m": int(distance_m),
        "cells": cells_out,
    }


# ---------------------------------------------------------------------------
# S7: Global Moran's I (Queen Contiguity, Exact Randomization Variance)
# ---------------------------------------------------------------------------
def compute_morans_i(df: pd.DataFrame, values: pd.Series, target_name: str) -> dict:
    if "cell_id" not in df.columns:
        return {"target": target_name, "morans_i": None, "expected_i": None, "z_score": None, "p_value": None}

    sub = pd.DataFrame({"cell_id": df["cell_id"], "val": pd.to_numeric(values, errors="coerce")}).dropna().reset_index(drop=True)
    n = int(len(sub))
    if n < 5:
        return {"target": target_name, "morans_i": None, "expected_i": None, "z_score": None, "p_value": None}

    cols, rows = parse_cell_coords(sub["cell_id"])
    x = sub["val"].to_numpy(dtype=np.float64)
    z = x - np.mean(x)
    m2 = float(np.sum(z * z))
    if m2 <= 1e-15:
        return {"target": target_name, "morans_i": 0.0, "expected_i": _r(-1.0 / (n - 1), 4), "z_score": 0.0, "p_value": 1.0}

    coord_map = {(int(cols[i]), int(rows[i])): i for i in range(n)}
    queen_offsets = [(dc, dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1) if not (dc == 0 and dr == 0)]

    num = 0.0
    s0 = 0
    degrees = np.zeros(n, dtype=np.float64)
    for i in range(n):
        c0, r0 = int(cols[i]), int(rows[i])
        deg = 0
        for dc, dr in queen_offsets:
            j = coord_map.get((c0 + dc, r0 + dr))
            if j is not None:
                num += z[i] * z[j]
                deg += 1
        degrees[i] = deg
        s0 += deg

    if s0 == 0:
        return {"target": target_name, "morans_i": None, "expected_i": None, "z_score": None, "p_value": None}

    # Symmetric binary weights: w_ij = w_ji in {0, 1} -> S1 = 2 * S0, S2 = sum((2 * deg_i)^2) = 4 * sum(deg_i^2)
    s1 = 2.0 * s0
    s2 = 4.0 * float(np.sum(degrees * degrees))
    b2 = (n * float(np.sum(z ** 4))) / (m2 * m2)

    moran_i = (n / s0) * (num / m2)
    exp_i = -1.0 / (n - 1)
    num_ei2 = (
        n * ((n * n - 3 * n + 3) * s1 - n * s2 + 3.0 * s0 * s0)
        - b2 * ((n * n - n) * s1 - 2 * n * s2 + 6.0 * s0 * s0)
    )
    den_ei2 = (n - 1) * (n - 2) * (n - 3) * s0 * s0
    var_i = max(1e-15, (num_ei2 / den_ei2) - exp_i * exp_i)
    z_score = (moran_i - exp_i) / math.sqrt(var_i)
    p_val = normal_pvalue(z_score)

    if p_val < 0.05 and moran_i > 0.1:
        interp = "กระจุกตัวเชิงพื้นที่อย่างมีนัยสำคัญ (positive spatial autocorrelation)"
    elif p_val < 0.05 and moran_i < -0.1:
        interp = "กระจายสลับกันเชิงพื้นที่อย่างมีนัยสำคัญ (negative spatial autocorrelation)"
    else:
        interp = "ไม่พบการกระจุกตัวเชิงพื้นที่อย่างมีนัยสำคัญ"

    return {
        "target": target_name,
        "morans_i": _r(moran_i, 3),
        "expected_i": _r(exp_i, 4),
        "z_score": _r(z_score, 2),
        "p_value": _r(p_val, 4),
        "interpretation": interp,
    }


# ---------------------------------------------------------------------------
# S8: Group Comparison (Tertiles + Kruskal-Wallis Test)
# ---------------------------------------------------------------------------
def compute_groups(
    df: pd.DataFrame,
    group_col: str = "ndvi_mean",
    target_col: str = "lst_mean",
) -> dict:
    if group_col not in df.columns or target_col not in df.columns:
        return {"by": group_col, "groups": [], "diff_dense_minus_green": None, "test": {"name": "kruskal_wallis", "p_value": None}}

    sub = df[[group_col, target_col]].apply(pd.to_numeric, errors="coerce").dropna().reset_index(drop=True)
    n = int(len(sub))
    if n < 6:
        return {"by": group_col, "groups": [], "diff_dense_minus_green": None, "test": {"name": "kruskal_wallis", "p_value": None}}

    # Sort descending for NDVI (high NDVI = green, low NDVI = dense)
    ascending = group_col != "ndvi_mean"
    sub = sub.sort_values(group_col, ascending=ascending).reset_index(drop=True)
    k = n // 3
    slices = [
        ("green" if group_col == "ndvi_mean" else "low", sub.iloc[:k]),
        ("medium", sub.iloc[k : 2 * k]),
        ("dense" if group_col == "ndvi_mean" else "high", sub.iloc[2 * k :]),
    ]

    groups_out = []
    sample_arrays = []
    for label, part in slices:
        g_vals = part[group_col].to_numpy(dtype=np.float64)
        t_vals = part[target_col].to_numpy(dtype=np.float64)
        sample_arrays.append(t_vals)
        groups_out.append({
            "group": label,
            "n": int(len(part)),
            "ndvi_range" if group_col == "ndvi_mean" else "range": [
                _r(float(np.min(g_vals)), 3),
                _r(float(np.max(g_vals)), 3),
            ],
            "lst_mean": _r(float(np.mean(t_vals)), 2),
            "lst_sd": _r(float(np.std(t_vals, ddof=1)) if len(t_vals) > 1 else 0.0, 2),
            "lst_median": _r(float(np.median(t_vals)), 2),
            "lst_iqr": _r(float(np.percentile(t_vals, 75) - np.percentile(t_vals, 25)), 2),
        })

    # Kruskal-Wallis H statistic with tie correction
    all_t = np.concatenate(sample_arrays)
    ranks = pd.Series(all_t).rank(method="average").to_numpy(dtype=np.float64)
    idx = 0
    h_num = 0.0
    for arr in sample_arrays:
        ni = len(arr)
        ri_mean = float(np.mean(ranks[idx : idx + ni]))
        h_num += ni * ((ri_mean - (n + 1.0) / 2.0) ** 2)
        idx += ni
    h_stat = (12.0 / (n * (n + 1.0))) * h_num
    _, tie_counts = np.unique(all_t, return_counts=True)
    tie_corr = 1.0 - float(np.sum(tie_counts ** 3 - tie_counts)) / (n ** 3 - n)
    if tie_corr > 1e-12:
        h_stat /= tie_corr
    # Exact p-value for chi-square with df = 2 (3 groups - 1): P(X >= H) = exp(-H / 2)
    kw_p = float(math.exp(-0.5 * max(0.0, h_stat)))

    diff = None
    if groups_out[0]["lst_mean"] is not None and groups_out[2]["lst_mean"] is not None:
        diff = _r(groups_out[2]["lst_mean"] - groups_out[0]["lst_mean"], 2)

    return {
        "by": group_col,
        "groups": groups_out,
        "diff_dense_minus_green": diff,
        "test": {
            "name": "kruskal_wallis",
            "statistic": _r(h_stat, 3),
            "p_value": _r(kw_p, 6),
        },
    }


# ---------------------------------------------------------------------------
# Cell-Level Attribution (Micro View for Urban Planners clicking a 90m cell)
# ---------------------------------------------------------------------------
def compute_cell_attribution(
    df: pd.DataFrame,
    s3_result: dict,
    hotspot_result: dict,
) -> list[dict]:
    if not s3_result.get("terms") or "cell_id" not in df.columns:
        return []
    x_cols = [t["term"] for t in s3_result["terms"]]
    coefs = {t["term"]: float(t["coef"]) for t in s3_result["terms"]}
    intercept = float(s3_result["intercept"])

    sub = df[["cell_id", "lst_mean", *x_cols]].copy()
    for col in ["lst_mean", *x_cols]:
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=["lst_mean", *x_cols]).reset_index(drop=True)
    if sub.empty:
        return []

    area_means = {col: float(sub[col].mean()) for col in ["lst_mean", *x_cols]}
    hs_map = {c["cell_id"]: c for c in hotspot_result.get("cells", [])}

    out = []
    for _, row in sub.iterrows():
        cid = str(row["cell_id"])
        pred = intercept + sum(coefs[x] * float(row[x]) for x in x_cols)
        actual = float(row["lst_mean"])
        contribs = {
            x: _r(coefs[x] * (float(row[x]) - area_means[x]), 3)
            for x in x_cols
        }
        hs = hs_map.get(cid, {})
        out.append({
            "cell_id": cid,
            "lst_actual": _r(actual, 2),
            "lst_diff_from_area_mean": _r(actual - area_means["lst_mean"], 2),
            "s3_predicted_lst": _r(pred, 2),
            "s3_residual": _r(actual - pred, 2),
            "factor_contributions_c": contribs,
            "gi_z": hs.get("gi_z"),
            "hotspot_class": hs.get("class", "not_significant"),
        })
    return out


# ---------------------------------------------------------------------------
# Full Period + Match Orchestrator
# ---------------------------------------------------------------------------
def analyze_period_match(
    df_all: pd.DataFrame,
    period: str,
    match: str,
    seed: int = RANDOM_SEED,
    n_boot: int = 500,
) -> dict[str, dict]:
    """Run S1-S8 + cell attribution for one (period, match_type) slice (Spec 60 §3)."""
    df = df_all.copy()
    if "in_cover" in df.columns:
        df = df[df["in_cover"].fillna(False).astype(bool)].reset_index(drop=True)

    base = {"period": period, "match": match}

    summary_rows = compute_summary(df, DEFAULT_VARIABLES)
    corr_body = compute_correlation(df, DEFAULT_VARIABLES)
    s3_model, s3_resid_df = compute_regression(df, "lst_mean", DEFAULT_REGRESSORS_S3, "s3", seed=seed, n_boot=n_boot)
    s4_model, _ = compute_regression(df, "air_temp_mean", DEFAULT_REGRESSORS_S4, "s4", seed=seed, n_boot=n_boot)
    imp_body = compute_rf_importance(df, "lst_mean", DEFAULT_REGRESSORS_S3, n_trees=RF_TREES, seed=seed)
    hs_body = compute_hotspot_gi_star(df, "lst_mean", distance_m=GI_DISTANCE_M)

    mi_lst = compute_morans_i(df, df["lst_mean"] if "lst_mean" in df.columns else pd.Series(dtype=float), "lst_mean")
    if not s3_resid_df.empty:
        mi_res = compute_morans_i(s3_resid_df, s3_resid_df["residual"], "s3_residual")
    else:
        mi_res = {"target": "s3_residual", "morans_i": None, "expected_i": None, "z_score": None, "p_value": None}

    spatial_warnings = []
    if mi_res.get("morans_i") is not None and mi_res.get("p_value") is not None:
        if mi_res["morans_i"] > 0.1 and mi_res["p_value"] < 0.05:
            spatial_warnings.append(
                f"ค่าคลาดเคลื่อนของโมเดล S3 (s3_residual) ยังมีการกระจุกตัวเชิงพื้นที่อย่างมีนัยสำคัญ (Moran's I = {mi_res['morans_i']}, p = {mi_res['p_value']}) "
                "บ่งชี้ว่าช่วงเชื่อมั่นจาก Bootstrap อาจแคบเกินจริง และควรพิจารณาโมเดลถดถอยเชิงพื้นที่เพิ่มเติมก่อนสรุปผลเชิงสาเหตุ"
            )

    groups_ndvi = compute_groups(df, "ndvi_mean", "lst_mean")
    groups_builtup = compute_groups(df, "builtup_pct", "lst_mean")
    cell_attr = compute_cell_attribution(df, s3_model, hs_body)

    outputs = {
        "summary": {**base, "n_in_cover_cells": int(len(df)), "rows": summary_rows},
        "correlation": {**base, **corr_body},
        "regression": {
            **base,
            "methodology_note": s3_model.get("methodology_note"),
            "models": [s3_model, s4_model],
        },
        "importance": {**base, **imp_body},
        "hotspot": {**base, **hs_body},
        "spatial_autocorr": {
            **base,
            "rows": [mi_lst, mi_res],
            "warnings": spatial_warnings,
        },
        "groups": {
            **base,
            **groups_ndvi,
            "builtup_comparison": groups_builtup,
        },
        "cell_attribution": {
            **base,
            "n_cells": len(cell_attr),
            "cells": cell_attr,
        },
    }
    return sanitize(outputs)
