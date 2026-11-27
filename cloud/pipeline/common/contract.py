"""Data contracts (spec 32, D23): validation rules as YAML, one file per source.

Rule levels (one YAML file per source in `contracts/`):

  BLOCK  in batch_checks -> the whole batch FAILs (quarantine/batch/)
  BLOCK  in columns      -> that row is dropped     (quarantine/rows/)
  STANDARDIZE            -> value fixed, row kept    (counted in the report)
  WARN                   -> row kept, logged         (report.warnings)

Pure Python (no pandas) so ValidateBatch runs without a Lambda layer. The
handler reads and writes S3; this module only decides.

Lambda zips get contracts/<source>.json (Terraform: jsonencode(yamldecode(...)))
because PyYAML is not in the Lambda runtime; tests read the .yaml directly.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import grid

PASS = "PASS"
FAIL = "FAIL"
LEVELS = ("BLOCK", "STANDARDIZE", "WARN")
SAMPLE_ROWS = 20

# cloud/pipeline/contracts/ in the repo; contracts/ next to common/ in a Lambda zip.
_DIRS = (Path(__file__).resolve().parent.parent / "contracts",)


# =============================================================================
#  Loading
# =============================================================================

def load(source: str, directory: str | Path | None = None) -> dict:
    """contracts/<source>.json (Lambda) or contracts/<source>.yaml (repo/tests)."""
    dirs = [Path(directory)] if directory else list(_DIRS)
    for d in dirs:
        js = d / f"{source}.json"
        if js.exists():
            return _checked(json.loads(js.read_text(encoding="utf-8")), source)
        ym = d / f"{source}.yaml"
        if ym.exists():
            import yaml  # dev only

            return _checked(yaml.safe_load(ym.read_text(encoding="utf-8")), source)
    raise FileNotFoundError(f"no contract for source {source!r} in {[str(d) for d in dirs]}")


def _checked(c: dict, source: str) -> dict:
    if c.get("source") != source:
        raise ValueError(f"contract source {c.get('source')!r} != {source!r}")
    # A bare y / n / on / off in YAML 1.1 (Terraform yamldecode) becomes a bool: quote it.
    names = [col.get("name") for col in c.get("columns", [])] + list(c.get("key") or [])
    bad = [n for n in names if not isinstance(n, str)]
    if bad:
        raise ValueError(f"{source}: column/key names must be strings, got {bad!r} (quote them in YAML)")
    for col in c.get("columns", []):
        for chk in col.get("checks", []):
            if chk.get("level") not in LEVELS:
                raise ValueError(f"{source}.{col['name']}: bad level {chk.get('level')!r}")
    for chk in c.get("batch_checks", []):
        if chk.get("level") not in LEVELS:
            raise ValueError(f"{source}: bad level {chk.get('level')!r} in batch_checks")
    return c


def columns(contract: Mapping) -> list[str]:
    return [c["name"] for c in contract["columns"]]


# =============================================================================
#  Result
# =============================================================================

@dataclass
class Result:
    source: str
    contract_version: Any
    status: str = PASS
    total_rows: int = 0
    rows: list[dict] = field(default_factory=list)          # typed rows that passed
    rejected: list[dict] = field(default_factory=list)      # raw row + "_rule"
    duplicates: list[dict] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)
    violations: dict[str, dict] = field(default_factory=dict)
    warnings: dict[str, dict] = field(default_factory=dict)
    standardized: dict[str, int] = field(default_factory=dict)

    @property
    def invalid_ratio(self) -> float:
        base = self.total_rows - len(self.duplicates)
        return len(self.rejected) / base if base else 0.0

    def report(self, batch_id: str, generated_at: str) -> dict:
        return {
            "batch_id": batch_id,
            "source": self.source,
            "contract_version": self.contract_version,
            "status": self.status,
            "fail_reasons": self.fail_reasons,
            "total_rows": self.total_rows,
            "passed": len(self.rows),
            "quarantined_rows": len(self.rejected),
            "duplicate_rows": len(self.duplicates),
            "invalid_ratio": round(self.invalid_ratio, 4),
            "violations": [{"rule": k, **v} for k, v in sorted(self.violations.items())],
            "warnings": [{"rule": k, **v} for k, v in sorted(self.warnings.items())],
            "standardized": self.standardized,
            "sample_rejected": self.rejected[:SAMPLE_ROWS],
            "generated_at": generated_at,
        }


def _bump(d: dict, rule: str, level: str, scope: str, detail: str | None = None) -> None:
    e = d.setdefault(rule, {"level": level, "scope": scope, "rows": 0})
    e["rows"] += 1
    if detail and "detail" not in e:
        e["detail"] = detail


# =============================================================================
#  Standardize + cast
# =============================================================================

_TRUE = {"true", "1", "yes", "t"}
_FALSE = {"false", "0", "no", "f"}


def _cast(value: Any, ctype: str) -> Any:
    if value is None:
        return None
    if ctype == "string":
        return str(value)
    if ctype == "float":
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            raise ValueError("not finite")
        return v
    if ctype == "int":
        f = float(value)
        if not f.is_integer():
            raise ValueError("not an integer")
        return int(f)
    if ctype == "bool":
        s = str(value).strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise ValueError("not a bool")
    raise ValueError(f"unknown type {ctype!r}")


def _std_steps(contract: Mapping) -> list[dict]:
    return [s if isinstance(s, dict) else {"rule": s} for s in contract.get("standardize", [])]


def clean_header(header: Iterable[str], contract: Mapping) -> list[str]:
    names = list(header)
    steps = {s["rule"] for s in _std_steps(contract)}
    if "strip_bom_from_header" in steps and names:
        names[0] = names[0].lstrip("﻿")
    if "trim_whitespace" in steps:
        names = [n.strip() for n in names]
    return names


def _standardize(raw: Mapping, contract: Mapping, res: Result) -> dict:
    row = dict(raw)
    for step in _std_steps(contract):
        rule = step["rule"]
        if rule == "trim_whitespace":
            row = {k: v.strip() if isinstance(v, str) else v for k, v in row.items()}
        elif rule == "nodata_to_null":
            nd = float(step["value"])
            for c in step.get("columns", []):
                v = row.get(c)
                try:
                    if v not in (None, "") and float(v) == nd:
                        row[c] = None
                        res.standardized[f"{c}: nodata -> null"] = res.standardized.get(
                            f"{c}: nodata -> null", 0) + 1
                except ValueError:
                    pass
        elif rule == "empty_to_null":
            for c in step.get("columns", []):
                if row.get(c) == "":
                    row[c] = None
    # Empty string means "no value" for every column.
    return {k: (None if v == "" else v) for k, v in row.items()}


# =============================================================================
#  Row rules
# =============================================================================

def _row_check(chk: Mapping, v: Any) -> bool:
    """True = OK. Null values pass every rule except not_null (nullable handles them)."""
    r = chk["rule"]
    if r == "not_null":
        return v is not None
    if v is None:
        return True
    if r == "range":
        return chk["min"] <= v <= chk["max"]
    if r == "min":
        return v >= chk["value"]
    if r == "max":
        return v <= chk["value"]
    if r == "allowed_values":
        return v in chk["values"]
    if r == "not_equal":
        return v != chk["value"]
    if r == "cell_id":
        try:
            grid.parse_cell_id(v)
            return True
        except ValueError:
            return False
    if r == "in_master_list":
        return v in set(chk["values"])
    raise ValueError(f"unknown column rule {r!r}")


def rule_label(col: str, chk: Mapping) -> str:
    r = chk["rule"]
    if r == "range":
        return f"{col} in [{chk['min']}, {chk['max']}]"
    if r in ("min", "max"):
        return f"{col} {'>=' if r == 'min' else '<='} {chk['value']}"
    if r == "allowed_values":
        return f"{col} in {chk['values']}"
    return f"{col} {r}"


# =============================================================================
#  Evaluate
# =============================================================================

def evaluate(
    contract: Mapping,
    header: Iterable[str],
    raw_rows: Iterable[Mapping],
    files: Iterable[str] = (),
    metadata: Mapping | None = None,
) -> Result:
    """Check one batch. header = CSV header as read; raw_rows = csv.DictReader rows
    (keys from that header); files = file names present in the batch folder."""
    res = Result(contract["source"], contract.get("version"))
    metadata = metadata or {}
    original = list(header)
    header = clean_header(original, contract)
    rename = dict(zip(original, header))
    raw_rows = [{rename.get(k, k): v for k, v in r.items()} for r in raw_rows]
    res.total_rows = len(raw_rows)
    cols = contract["columns"]
    names = [c["name"] for c in cols]
    key = contract.get("key") or []

    def fail(rule: str, detail: str, level: str = "BLOCK") -> None:
        if level == "BLOCK":
            res.fail_reasons.append(f"{rule}: {detail}")
            res.violations[rule] = {"level": "BLOCK", "scope": "batch", "rows": res.total_rows,
                                    "detail": detail}
        else:
            res.warnings[rule] = {"level": level, "scope": "batch", "rows": res.total_rows,
                                  "detail": detail}

    # --- batch checks that need no row parsing ---------------------------------
    batch_checks = list(contract.get("batch_checks", []))
    present = set(files)
    for chk in batch_checks:
        r, lvl = chk["rule"], chk["level"]
        if r == "required_files":
            missing = [f for f in chk["values"] if f not in present]
            if missing:
                fail(r, f"missing {missing}", lvl)
        elif r == "required_columns":
            missing = [n for n in names if n not in header]
            extra = [h for h in header if h not in names]
            if missing:
                fail(r, f"missing columns {missing}", lvl)
            if extra:
                res.warnings["no unexpected columns"] = {
                    "level": "WARN", "scope": "batch", "rows": res.total_rows,
                    "detail": f"extra columns {extra} (not written to trusted)"}
        elif r == "row_count_min":
            if res.total_rows < chk["value"]:
                fail(r, f"{res.total_rows} rows < {chk['value']}", lvl)
        elif r == "row_count_equals_metadata":
            want = metadata.get(chk["field"])
            if want != res.total_rows:
                fail(r, f"{res.total_rows} rows != metadata.{chk['field']}={want}", lvl)
        elif r == "metadata_equals":
            got = metadata.get(chk["field"])
            if not _equal(got, chk["value"]):
                fail(r, f"metadata.{chk['field']}={got!r} != {chk['value']!r}", lvl)
        elif r == "metadata_min":
            got = metadata.get(chk["field"])
            if got is None or got < chk["value"]:
                fail(r, f"metadata.{chk['field']}={got!r} < {chk['value']}", lvl)
    if res.fail_reasons:      # columns missing etc.: rows cannot be trusted
        res.status = FAIL
        return res

    # --- rows -------------------------------------------------------------------
    seen: set[tuple] = set()
    unique = next((c for c in batch_checks if c["rule"] == "unique_key"), None)
    dup_count = 0
    for raw in raw_rows:
        row = _standardize(raw, contract, res)
        typed: dict = {}
        broken: list[str] = []
        for c in cols:
            v = row.get(c["name"])
            try:
                v = _cast(v, c["type"])
            except (TypeError, ValueError):
                broken.append(f"{c['name']} is {c['type']}")
                _bump(res.violations, f"{c['name']} is {c['type']}", "BLOCK", "row")
                continue
            if v is None and not c.get("nullable", True):
                broken.append(f"{c['name']} not_null")
                _bump(res.violations, f"{c['name']} not_null", "BLOCK", "row")
                continue
            typed[c["name"]] = v
            for chk in c.get("checks", []):
                if _row_check(chk, v):
                    continue
                label = rule_label(c["name"], chk)
                if chk["level"] == "BLOCK":
                    broken.append(label)
                    _bump(res.violations, label, "BLOCK", "row")
                elif chk["level"] == "STANDARDIZE":
                    typed[c["name"]] = None
                    res.standardized[f"{label} -> null"] = res.standardized.get(f"{label} -> null", 0) + 1
                else:
                    _bump(res.warnings, label, "WARN", "row")
        if broken:
            res.rejected.append({**raw, "_rule": "; ".join(broken)})
            continue
        if key:
            k = tuple(typed.get(n) for n in key)
            if k in seen:
                dup_count += 1
                res.duplicates.append(dict(raw))
                continue
            seen.add(k)
        res.rows.append(typed)

    if unique and dup_count:
        msg = f"{dup_count} rows repeat key {key}"
        if unique["level"] == "BLOCK":
            fail("unique_key", msg)
        else:
            _bump(res.warnings, "unique_key", unique["level"], "row", msg)

    mir = next((c for c in batch_checks if c["rule"] == "max_invalid_ratio"), None)
    if mir and res.total_rows and res.invalid_ratio > mir["value"]:
        fail("max_invalid_ratio",
             f"{len(res.rejected)} rejected rows = {res.invalid_ratio:.0%} > {mir['value']:.0%}",
             mir["level"])
    res.status = FAIL if res.fail_reasons else PASS
    return res


def _equal(a: Any, b: Any) -> bool:
    if isinstance(b, list):
        return isinstance(a, list) and len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a == b
