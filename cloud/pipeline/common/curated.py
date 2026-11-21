"""curated-merge: periods, S3 keys and Athena SQL for curated.cell_period (#32, spec 60 §2.1, D23).

One row of cell_period = 1 cell x 1 period x 1 match set:
  period  : scene_<date> | season_<y>_03-05 | month_<YYYY-MM>
  match_type : 'window' = IoT minutes around the satellite pass, 'all' = every IoT minute of the period range

Satellite values come from `sat_period` (scene/season = the period itself, month = latest season
of that year or earlier). IoT values are re-aggregated per cell from readings_1min on each MERGE,
so late IoT data just needs the same period merged again.

Pure functions only (no AWS calls except run_query, which takes the client) so the handlers and
the tests share them.
"""

from __future__ import annotations

import re
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from . import satellite_schema as ss

# --- settings (spec 60 §5) -------------------------------------------------
MATCH_DAYS = 7
WINDOW_START_MIN = 9 * 60 + 30     # 09:30 (inclusive)
WINDOW_END_MIN = 11 * 60 + 30      # 11:30 (exclusive)
STATION_START_MIN = 10 * 60        # 10:00
STATION_END_MIN = 11 * 60          # 11:00
MIN_IOT_MINUTES = 5
MATCH_TYPES = ("window", "all")

DATABASE = "iot_walking"
TABLE = "cell_period"
TABLE_PREFIX = "curated/cell_period/"

# --- cell_period columns (Athena/Iceberg types) ----------------------------
KEY_COLUMNS = ("cell_id", "period", "match_type")
CELL_PERIOD: tuple[tuple[str, str], ...] = (
    ("cell_id", "string"),
    ("period", "string"),
    ("match_type", "string"),
    ("period_type", "string"),
    ("sat_period", "string"),
    ("sat_date_start", "date"),
    ("sat_date_end", "date"),
    ("iot_date_start", "date"),
    ("iot_date_end", "date"),
    # satellite (trusted/satellite_grid)
    ("n_scenes", "int"),
    ("clear_obs_mean", "double"),
    ("valid_pixel_ratio", "double"),
    ("lst_mean", "double"),
    ("lst_p90", "double"),
    ("ndvi_mean", "double"),
    ("ndbi_mean", "double"),
    ("green_ratio", "double"),
    # buildings (trusted/buildings_grid); in_cover=false means "no data", not "no buildings"
    ("builtup_pct", "double"),
    ("building_count", "int"),
    ("floors_mean", "double"),
    ("far", "double"),
    ("in_cover", "boolean"),
    ("bldg_source", "string"),
    # IoT (readings_1min); null when n_iot_minutes < MIN_IOT_MINUTES
    ("air_temp_mean", "double"),
    ("humidity_mean", "double"),
    ("lux_mean", "double"),
    ("db_spl_mean", "double"),
    ("n_iot_minutes", "int"),
    ("n_devices", "int"),
    ("merged_at", "timestamp"),
)
COLUMN_NAMES = tuple(n for n, _ in CELL_PERIOD)

SAT_COLS = ("n_scenes", "clear_obs_mean", "valid_pixel_ratio", "lst_mean", "lst_p90",
            "ndvi_mean", "ndbi_mean", "green_ratio")
BLDG_COLS = ("builtup_pct", "building_count", "floors_mean", "far", "in_cover", "bldg_source")
IOT_AVG = (("air_temp_mean", "temp_c_mean"), ("humidity_mean", "humidity_pct_mean"),
           ("lux_mean", "lux_mean"), ("db_spl_mean", "db_spl_mean"))


# =============================================================================
#  Periods
# =============================================================================
_MONTH_RE = re.compile(r"^month_(?P<y>\d{4})-(?P<m>\d{2})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def month_period(year: int, month: int) -> str:
    return f"month_{year}-{month:02d}"


@dataclass(frozen=True)
class MergePeriod:
    period: str
    period_type: str            # scene | season | month
    date_start: date            # the period itself
    date_end: date
    iot_start: date             # IoT date range (Thai-local dates)
    iot_end: date
    pass_date: date | None      # scene: the day the satellite passed

    @property
    def year(self) -> int:
        return self.date_start.year


def parse_merge_period(period: str) -> MergePeriod:
    m = _MONTH_RE.match(period)
    if m:
        y, mo = int(m["y"]), int(m["m"])
        if not 1 <= mo <= 12:
            raise ValueError(f"bad period: {period!r}")
        start, end = date(y, mo, 1), date(y, mo, monthrange(y, mo)[1])
        return MergePeriod(period, "month", start, end, start, end, None)
    p = ss.parse_period(period)
    if p.period_type == "scene":
        d = p.date_start
        return MergePeriod(period, "scene", d, d, d - timedelta(days=MATCH_DAYS),
                           d + timedelta(days=MATCH_DAYS), d)
    if p.period_type == "season":
        return MergePeriod(period, "season", p.date_start, p.date_end, p.date_start, p.date_end, None)
    raise ValueError(f"year composites are not merged: {period!r}")


def sat_period_for(period: str, seasons: list[str]) -> str | None:
    """Satellite period whose values a period uses.

    scene/season -> itself. month -> the latest season (by year) that is not after the month's
    year, from `seasons` (the season batch_ids that are published). None if there is none.
    """
    mp = parse_merge_period(period)
    if mp.period_type != "month":
        return period
    ok = [s for s in seasons if ss.parse_period(s).year <= mp.year]
    return max(ok, key=lambda s: ss.parse_period(s).year) if ok else None


# =============================================================================
#  S3 keys
# =============================================================================
def key_merge_marker(period: str) -> str:
    parse_merge_period(period)
    return f"curated/_success/merge/{period}.json"


SUCCESS_SATELLITE_PREFIX = "curated/_success/satellite/"
SUCCESS_BUILDINGS_KEY = ss.s3_key_success(ss.STATIC_BUILDINGS)
MERGE_MARKER_PREFIX = "curated/_success/merge/"
IOT_MANIFEST_PREFIX = "curated/_success/iot/"   # = schema.MANIFEST_PREFIX (D23 step 4)


def year_from_trusted_key(key: str) -> int:
    m = re.search(r"/year=(\d{4})/", key)
    if not m:
        raise ValueError(f"no year= in {key!r}")
    return int(m[1])


# =============================================================================
#  SQL
# =============================================================================
def _lit_date(d: date) -> str:
    return f"'{d.isoformat()}'"


def _check_sql_str(v: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-.]+", v):
        raise ValueError(f"unsafe value for SQL: {v!r}")
    return v


def _q(v: str) -> str:
    return "'" + _check_sql_str(v) + "'"


def _minute_of_day() -> str:
    return "(hour(minute_local) * 60 + minute(minute_local))"


def _between(lo: int, hi: int) -> str:
    return f"{_minute_of_day()} >= {lo} AND {_minute_of_day()} < {hi}"


def iot_predicate(mp: MergePeriod, match: str, stationary_ids: list[str]) -> str:
    """WHERE condition on readings_1min for one match set (always prunes `date`)."""
    if match not in MATCH_TYPES:
        raise ValueError(f"bad match_type: {match!r}")
    dates = f"date BETWEEN {_lit_date(mp.iot_start)} AND {_lit_date(mp.iot_end)}"
    if match == "all":
        return dates
    window = _between(WINDOW_START_MIN, WINDOW_END_MIN)
    if mp.period_type != "scene" or not stationary_ids:
        return f"{dates} AND {window}"
    ids = ", ".join(_q(i) for i in sorted(stationary_ids))
    station = (f"(device_id IN ({ids}) AND date = {_lit_date(mp.pass_date)} "
               f"AND {_between(STATION_START_MIN, STATION_END_MIN)})")
    mobile = f"(device_id NOT IN ({ids}) AND {window})"
    return f"{dates} AND ({station} OR {mobile})"


def _iot_cte(name: str, predicate: str) -> str:
    avgs = ",\n    ".join(f"avg({src}) AS {dst}" for dst, src in IOT_AVG)
    return (f"{name} AS (\n  SELECT cell_id,\n    {avgs},\n"
            f"    count(*) AS n_minutes, count(DISTINCT device_id) AS n_dev\n"
            f"  FROM {DATABASE}.readings_1min\n  WHERE cell_id IS NOT NULL AND {predicate}\n"
            f"  GROUP BY cell_id\n)")


def build_source_select(period: str, sat_period: str, bldg_year: int,
                        stationary_ids: list[str] | None = None) -> str:
    """SELECT producing every cell_period column for both match sets of one period."""
    mp = parse_merge_period(period)
    sp = ss.parse_period(sat_period)
    if sp.period_type not in ("scene", "season"):
        raise ValueError(f"sat_period must be scene/season: {sat_period!r}")
    st = list(stationary_ids or [])

    ctes = [
        f"sat AS (\n  SELECT * FROM {DATABASE}.satellite_grid\n"
        f"  WHERE period_type = {_q(sp.period_type)} AND year = {sp.year} AND period = {_q(sat_period)}\n)",
        f"bld AS (\n  SELECT * FROM {DATABASE}.buildings_grid WHERE year = {int(bldg_year)}\n)",
    ]
    for m in MATCH_TYPES:
        ctes.append(_iot_cte(f"iot_{m}", iot_predicate(mp, m, st)))

    def select_for(m: str) -> str:
        n = "i.n_minutes"
        ok = f"{n} >= {MIN_IOT_MINUTES}"
        expr = {
            "cell_id": "s.cell_id",
            "period": _q(period),
            "match_type": _q(m),
            "period_type": _q(mp.period_type),
            "sat_period": "s.period",
            "sat_date_start": "s.date_start",
            "sat_date_end": "s.date_end",
            "iot_date_start": f"DATE {_lit_date(mp.iot_start)}",
            "iot_date_end": f"DATE {_lit_date(mp.iot_end)}",
            "in_cover": "coalesce(b.in_cover, false)",
            "n_iot_minutes": f"CAST(coalesce({n}, 0) AS integer)",
            "n_devices": "CAST(coalesce(i.n_dev, 0) AS integer)",
            "merged_at": "CAST(current_timestamp AS timestamp)",
        }
        for c in SAT_COLS:
            expr[c] = f"s.{c}"
        for c in BLDG_COLS:
            expr.setdefault(c, f"b.{c}")
        for dst, _ in IOT_AVG:
            expr[dst] = f"CASE WHEN {ok} THEN i.{dst} END"
        cols = ",\n  ".join(f"{expr[c]} AS {c}" for c in COLUMN_NAMES)
        return (f"SELECT\n  {cols}\nFROM sat s\nLEFT JOIN bld b ON b.cell_id = s.cell_id\n"
                f"LEFT JOIN iot_{m} i ON i.cell_id = s.cell_id")

    return "WITH " + ",\n".join(ctes) + "\n" + "\nUNION ALL\n".join(select_for(m) for m in MATCH_TYPES)


def build_create_table(bucket: str) -> str:
    cols = ",\n  ".join(f"{n} {t}" for n, t in CELL_PERIOD)
    return (f"CREATE TABLE IF NOT EXISTS {DATABASE}.{TABLE} (\n  {cols}\n)\n"
            f"PARTITIONED BY (period)\n"
            f"LOCATION 's3://{_check_sql_str(bucket)}/{TABLE_PREFIX}'\n"
            f"TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')")


def build_merge(period: str, sat_period: str, bldg_year: int,
                stationary_ids: list[str] | None = None) -> str:
    source = build_source_select(period, sat_period, bldg_year, stationary_ids)
    on = " AND ".join(f"t.{k} = s.{k}" for k in KEY_COLUMNS)
    sets = ",\n    ".join(f"{c} = s.{c}" for c in COLUMN_NAMES if c not in KEY_COLUMNS)
    names = ", ".join(COLUMN_NAMES)
    vals = ", ".join(f"s.{c}" for c in COLUMN_NAMES)
    return (f"MERGE INTO {DATABASE}.{TABLE} t\nUSING (\n{source}\n) s\nON {on}\n"
            f"WHEN MATCHED THEN UPDATE SET\n    {sets}\n"
            f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({vals})")


def build_verify_query(period: str) -> str:
    cov = ",\n  ".join(f"count({c}) AS {c}" for c in
                       ("lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct", "air_temp_mean"))
    return (f"SELECT match_type, count(*) AS n_rows, count(DISTINCT cell_id) AS n_cells,\n  {cov},\n"
            f"  sum(CASE WHEN n_iot_minutes > 0 THEN 1 ELSE 0 END) AS n_cells_with_iot\n"
            f"FROM {DATABASE}.{TABLE} WHERE period = {_q(period)} GROUP BY match_type")


def build_sat_cell_count(sat_period: str) -> str:
    sp = ss.parse_period(sat_period)
    return (f"SELECT count(*) AS n FROM {DATABASE}.satellite_grid WHERE period_type = {_q(sp.period_type)} "
            f"AND year = {sp.year} AND period = {_q(sat_period)}")


def verify_counts(rows: list[dict], expected_cells: int) -> list[str]:
    """Problems (empty = OK) for the verify query result (one dict per match_type)."""
    problems = []
    by = {r["match_type"]: r for r in rows}
    for m in MATCH_TYPES:
        r = by.get(m)
        if r is None:
            problems.append(f"{m}: no rows")
            continue
        if r["n_rows"] != expected_cells:
            problems.append(f"{m}: {r['n_rows']} rows, expected {expected_cells}")
        if r["n_cells"] != r["n_rows"]:
            problems.append(f"{m}: duplicate cell_id ({r['n_cells']} distinct of {r['n_rows']})")
    return problems


# =============================================================================
#  Athena runner
# =============================================================================
class AthenaError(RuntimeError):
    pass


RETRYABLE = ("ICEBERG_COMMIT_ERROR", "ICEBERG_FILESYSTEM_ERROR", "TOO_MANY_REQUESTS")


def run_query(athena, sql: str, workgroup: str, database: str = DATABASE, *,
              poll_s: float = 2.0, timeout_s: float = 780.0, retries: int = 3,
              sleep=time.sleep) -> str:
    """Run one statement and wait; returns the QueryExecutionId.

    Concurrent MERGEs into the same Iceberg table can fail to commit -> retried.
    """
    last = ""
    for attempt in range(retries + 1):
        qid = athena.start_query_execution(
            QueryString=sql, WorkGroup=workgroup,
            QueryExecutionContext={"Database": database})["QueryExecutionId"]
        waited = 0.0
        while True:
            st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            if st["State"] == "SUCCEEDED":
                return qid
            if st["State"] in ("FAILED", "CANCELLED"):
                last = st.get("StateChangeReason", st["State"])
                break
            if waited >= timeout_s:
                raise AthenaError(f"query {qid} still running after {timeout_s:.0f}s")
            sleep(poll_s)
            waited += poll_s
        if attempt < retries and any(c in last for c in RETRYABLE):
            sleep(5 * (attempt + 1))
            continue
        raise AthenaError(f"{qid}: {last}")
    raise AthenaError(last)


def fetch_rows(athena, qid: str) -> list[dict]:
    """Result rows as dicts (numbers converted); first row of the result is the header."""
    res = athena.get_query_results(QueryExecutionId=qid)
    rows = res["ResultSet"]["Rows"]
    if not rows:
        return []
    head = [c.get("VarCharValue") for c in rows[0]["Data"]]
    out = []
    for r in rows[1:]:
        rec = {}
        for h, c in zip(head, r["Data"]):
            v = c.get("VarCharValue")
            if v is not None and re.fullmatch(r"-?\d+", v):
                v = int(v)
            rec[h] = v
        out.append(rec)
    return out
