"""Satellite + community schema (90 m grid -> community).

Layers (each its own table, a community never reads satellite pixels directly):
  1. Landsat 30 m pixels (GEE, raw GeoTIFF)          -> not a table
  2. grid_cells, satellite_grid, buildings_grid      -> per 90 m cell (grid.py)
  3. community_cells                                  -> which cells a community covers
  4. community_summary                                -> weighted mean of its cells

Sources: LST, NDVI, NDBI all from Landsat 8/9 C2 L2 (one sensor, one pixel grid).
Buildings: BMA building.shp first, Google Open Buildings (conf >= 0.7) to fill gaps;
only per-cell summaries leave GEE (footprints must not be published).
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Mapping

from . import grid

# =============================================================================
#  Periods
# =============================================================================
# scene  : one acquisition DATE (images of the same day from two WRS rows /
#          two satellites are merged, so a date is counted once)
# season : Mar-May composite of one year (hot season)
# year   : whole-year composite

SEASON_MONTHS = (3, 5)
PERIOD_TYPES = ("scene", "season", "year")
_PERIOD_RE = re.compile(
    r"^(?:scene_(?P<d>\d{4}-\d{2}-\d{2})"
    r"|season_(?P<sy>\d{4})_(?P<m0>\d{2})-(?P<m1>\d{2})"
    r"|year_(?P<y>\d{4}))$"
)


@dataclass(frozen=True)
class Period:
    period: str
    period_type: str
    date_start: date
    date_end: date      # inclusive

    @property
    def year(self) -> int:
        return self.date_start.year


def scene_period(d: date | str) -> str:
    d = d if isinstance(d, date) else date.fromisoformat(d)
    return f"scene_{d.isoformat()}"


def season_period(year: int) -> str:
    return f"season_{year}_{SEASON_MONTHS[0]:02d}-{SEASON_MONTHS[1]:02d}"


def year_period(year: int) -> str:
    return f"year_{year}"


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def parse_period(period: str) -> Period:
    m = _PERIOD_RE.match(period)
    if not m:
        raise ValueError(f"bad period: {period!r}")
    try:
        if m["d"]:
            d = date.fromisoformat(m["d"])
            return Period(period, "scene", d, d)
        if m["sy"]:
            y, m0, m1 = int(m["sy"]), int(m["m0"]), int(m["m1"])
            if not 1 <= m0 <= m1 <= 12:
                raise ValueError
            return Period(period, "season", date(y, m0, 1), _month_end(y, m1))
        y = int(m["y"])
        return Period(period, "year", date(y, 1, 1), date(y, 12, 31))
    except ValueError as e:
        raise ValueError(f"bad period: {period!r}") from e


# =============================================================================
#  Tables (Glue/Athena types). Partition keys are in the S3 path, not the file.
# =============================================================================

GRID_CELLS: tuple[tuple[str, str], ...] = (
    ("cell_id", "string"),
    ("col", "int"),
    ("row", "int"),
    ("x_min", "double"),          # EPSG:32647, NW corner
    ("y_max", "double"),
    ("lon", "double"),            # WGS84 cell centre (for the web)
    ("lat", "double"),
    ("in_aoi", "boolean"),
)

SATELLITE_GRID_PARTITIONS = (("period_type", "string"), ("year", "int"))
SATELLITE_GRID: tuple[tuple[str, str], ...] = (
    ("cell_id", "string"),
    ("period", "string"),
    ("date_start", "date"),
    ("date_end", "date"),
    ("n_scenes", "int"),           # distinct clear-sky dates (scene: 0/1)
    ("clear_obs_mean", "double"),  # mean cloud-free images per pixel
    ("valid_pixel_ratio", "double"),  # share of the 9 pixels with a value
    ("lst_mean", "double"),
    ("lst_p90", "double"),
    ("ndvi_mean", "double"),
    ("ndbi_mean", "double"),
    ("green_ratio", "double"),     # share of pixels with NDVI > GREEN_NDVI
)

BUILDINGS_GRID_PARTITIONS = (("year", "int"),)
BUILDINGS_GRID: tuple[tuple[str, str], ...] = (
    ("cell_id", "string"),
    ("builtup_pct", "double"),
    ("building_count", "int"),
    ("floors_mean", "double"),
    ("far", "double"),
    ("in_cover", "boolean"),       # False = no building data, NOT "no buildings"
    ("bldg_source", "string"),
)

COMMUNITIES_COLUMNS = ("community_id", "name", "district", "lat", "lon", "source")

COMMUNITY_CELLS: tuple[tuple[str, str], ...] = (
    ("community_id", "string"),
    ("method", "string"),
    ("cell_id", "string"),
    ("weight", "double"),          # share of the cell inside the community area
)

COMMUNITY_SUMMARY_PARTITIONS = (("method", "string"), ("year", "int"))
COMMUNITY_SUMMARY: tuple[tuple[str, str], ...] = (
    ("community_id", "string"),
    ("period", "string"),
    ("n_cells", "int"),
    ("area_m2", "double"),
    ("n_scenes", "int"),
    ("clear_obs_mean", "double"),
    ("valid_pixel_ratio", "double"),
    ("lst_mean", "double"),
    ("lst_p90", "double"),         # p90 of the cells' lst_mean (not of pixels)
    ("ndvi_mean", "double"),
    ("ndbi_mean", "double"),
    ("green_ratio", "double"),
    ("builtup_pct", "double"),
    ("building_count", "int"),
    ("in_cover_ratio", "double"),
)

TABLES = {
    "grid_cells": GRID_CELLS,
    "satellite_grid": SATELLITE_GRID,
    "buildings_grid": BUILDINGS_GRID,
    "community_cells": COMMUNITY_CELLS,
    "community_summary": COMMUNITY_SUMMARY,
}
PARTITIONS = {
    "grid_cells": (),
    "satellite_grid": SATELLITE_GRID_PARTITIONS,
    "buildings_grid": BUILDINGS_GRID_PARTITIONS,
    "community_cells": (),
    "community_summary": COMMUNITY_SUMMARY_PARTITIONS,
}


def arrow_schema(table: str):
    import pyarrow as pa

    types = {
        "string": pa.string(), "int": pa.int32(), "bigint": pa.int64(),
        "double": pa.float64(), "boolean": pa.bool_(), "date": pa.date32(),
    }
    return pa.schema([(n, types[t]) for n, t in TABLES[table]])


# =============================================================================
#  Ranges and checks
# =============================================================================

GREEN_NDVI = 0.3
MIN_VALID_PIXEL_RATIO = 0.5    # below -> a cell's satellite values are nulled

RANGES: dict[str, tuple[float, float]] = {
    "lst_mean": (0.0, 70.0),
    "lst_p90": (0.0, 70.0),
    "ndvi_mean": (-1.0, 1.0),
    "ndbi_mean": (-1.0, 1.0),
    "green_ratio": (0.0, 1.0),
    "valid_pixel_ratio": (0.0, 1.0),
    "clear_obs_mean": (0.0, math.inf),
    "builtup_pct": (0.0, 100.0),
    "building_count": (0, math.inf),
    "floors_mean": (1.0, 200.0),
    "far": (0.0, 50.0),
    "weight": (0.0, 1.0),
}
SATELLITE_VALUE_COLS = ("lst_mean", "lst_p90", "ndvi_mean", "ndbi_mean", "green_ratio")
BLDG_SOURCES = ("bma_shp", "open_buildings")
METHODS = {"buffer_300": 300.0, "buffer_500": 500.0, "polygon": None}
COMMUNITY_ID_RE = re.compile(r"^C\d{4}$")


def out_of_range(row: Mapping) -> list[str]:
    """Columns whose non-null value is outside RANGES."""
    bad = []
    for col, (lo, hi) in RANGES.items():
        v = row.get(col)
        if v is not None and not (lo <= v <= hi):
            bad.append(col)
    return bad


def check_satellite_row(row: Mapping) -> list[str]:
    """Problems with one satellite_grid row (empty = OK)."""
    problems = []
    try:
        grid.parse_cell_id(row["cell_id"])
    except (KeyError, ValueError):
        problems.append("bad cell_id")
    try:
        p = parse_period(row["period"])
        if (row.get("date_start"), row.get("date_end")) != (p.date_start, p.date_end):
            problems.append("dates do not match period")
        if p.period_type == "scene" and row.get("n_scenes") not in (0, 1):
            problems.append("scene row must have n_scenes 0 or 1")
    except (KeyError, ValueError):
        problems.append("bad period")
    problems += [f"{c} out of range" for c in out_of_range(row)]
    if (row.get("lst_mean") is not None and row.get("lst_p90") is not None
            and row["lst_p90"] < row["lst_mean"] - 1e-9):
        problems.append("lst_p90 < lst_mean")
    return problems


def clean_satellite_row(row: Mapping) -> dict:
    """Null all satellite values when too few of the cell's pixels are usable."""
    out = dict(row)
    vpr = row.get("valid_pixel_ratio")
    if vpr is None or vpr < MIN_VALID_PIXEL_RATIO:
        for c in SATELLITE_VALUE_COLS:
            out[c] = None
    return out


# =============================================================================
#  Communities
# =============================================================================

@dataclass(frozen=True)
class Community:
    community_id: str
    name: str
    district: str
    lat: float
    lon: float
    source: str


def load_communities(path: str | Path) -> list[Community]:
    """communities.csv from CS341-RONNAI gee/ (UTF-8, BOM tolerated)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COMMUNITIES_COLUMNS:
            raise ValueError(f"communities.csv header must be {','.join(COMMUNITIES_COLUMNS)}")
        out, seen = [], set()
        for i, r in enumerate(reader, start=2):
            cid = r["community_id"]
            if not COMMUNITY_ID_RE.match(cid):
                raise ValueError(f"line {i}: bad community_id {cid!r}")
            if cid in seen:
                raise ValueError(f"line {i}: duplicate community_id {cid}")
            seen.add(cid)
            out.append(Community(cid, r["name"], r["district"],
                                 float(r["lat"]), float(r["lon"]), r["source"]))
    return out


def community_cells_buffer(c: Community, method: str) -> list[dict]:
    """community_cells rows for a point + buffer method."""
    radius = METHODS.get(method)
    if radius is None:
        raise ValueError(f"{method!r} is not a buffer method")
    x, y = grid.latlon_to_xy(c.lat, c.lon)
    return [
        {"community_id": c.community_id, "method": method, "cell_id": cid, "weight": w}
        for cid, w in grid.cells_in_circle(x, y, radius)
    ]


def weighted_mean(pairs: Iterable[tuple[float | None, float]]) -> float | None:
    num = den = 0.0
    for v, w in pairs:
        if v is None or w <= 0:
            continue
        num += v * w
        den += w
    return num / den if den else None


def weighted_quantile(pairs: Iterable[tuple[float | None, float]], q: float) -> float | None:
    """Weighted quantile (lower) of values; None-valued pairs are ignored."""
    pts = sorted((v, w) for v, w in pairs if v is not None and w > 0)
    if not pts:
        return None
    total = sum(w for _, w in pts)
    acc = 0.0
    for v, w in pts:
        acc += w
        if acc >= q * total - 1e-12:
            return v
    return pts[-1][0]


def summarize_community(
    community_id: str,
    period: str,
    cells: Iterable[Mapping],
    satellite: Mapping[str, Mapping],
    buildings: Mapping[str, Mapping] | None = None,
) -> dict:
    """One community_summary row.

    cells     : community_cells rows of ONE community and ONE method
    satellite : cell_id -> satellite_grid row of this period (already cleaned)
    buildings : cell_id -> buildings_grid row (optional)
    Satellite means are weighted by weight x valid_pixel_ratio; building values
    by weight only, and only over cells with in_cover.
    """
    cells = list(cells)
    if not cells:
        raise ValueError(f"{community_id}: no cells")
    cell_area = grid.GRID_SIZE_M ** 2
    out: dict = {
        "community_id": community_id,
        "period": period,
        "n_cells": len(cells),
        "area_m2": sum(c["weight"] for c in cells) * cell_area,
    }

    sat = [(satellite.get(c["cell_id"]), c["weight"]) for c in cells]
    sat_w = [(r, w * (r.get("valid_pixel_ratio") or 0.0)) for r, w in sat if r]
    for col in ("lst_mean", "ndvi_mean", "ndbi_mean", "green_ratio"):
        out[col] = weighted_mean((r.get(col), w) for r, w in sat_w)
    out["lst_p90"] = weighted_quantile(((r.get("lst_mean"), w) for r, w in sat_w), 0.9)
    out["valid_pixel_ratio"] = weighted_mean(
        ((r.get("valid_pixel_ratio") if r else 0.0), w) for r, w in sat)
    out["clear_obs_mean"] = weighted_mean((r.get("clear_obs_mean"), w) for r, w in sat if r)
    n = [r.get("n_scenes") for r, _ in sat if r and r.get("n_scenes") is not None]
    out["n_scenes"] = min(n) if n else None   # dates every cell has at least

    if buildings is not None:
        b = [(buildings.get(c["cell_id"]), c["weight"]) for c in cells]
        covered = [(r, w) for r, w in b if r and r.get("in_cover")]
        total_w = sum(w for _, w in b)
        out["in_cover_ratio"] = sum(w for _, w in covered) / total_w if total_w else None
        out["builtup_pct"] = weighted_mean((r.get("builtup_pct"), w) for r, w in covered)
        cnt = [(r.get("building_count"), w) for r, w in covered if r.get("building_count") is not None]
        out["building_count"] = round(sum(v * w for v, w in cnt)) if cnt else None
    else:
        out.update(in_cover_ratio=None, builtup_pct=None, building_count=None)
    return {name: out.get(name) for name, _ in COMMUNITY_SUMMARY}


# =============================================================================
#  S3 keys
# =============================================================================

def s3_prefix_raw_scene(d: date | str) -> str:
    return f"raw/satellite/{scene_period(d)}/"


def s3_prefix_raw_static(name: str) -> str:
    if not re.match(r"^[a-z_]+$", name):
        raise ValueError(f"bad static name: {name!r}")
    return f"raw/satellite/static={name}/"


# Zones follow D23 (spec 01 §2): raw -> trusted -> curated (+ quarantine).
STATIC_BUILDINGS = "static_buildings"


def batch_kind(batch_id: str) -> str:
    """scene | season | buildings for a satellite-pipeline batch_id.

    batch_id = the raw folder name: scene_<YYYY-MM-DD>, season_<YYYY>_03-05,
    or static_buildings (raw/satellite/static=buildings/).
    """
    if batch_id == STATIC_BUILDINGS:
        return "buildings"
    p = parse_period(batch_id)
    if p.period_type == "year":
        raise ValueError(f"year composites are not a satellite batch: {batch_id!r}")
    return p.period_type


def batch_id_from_key(key: str) -> str:
    """batch_id from an S3 key under raw/satellite/ (e.g. the _COMPLETE that fired the event)."""
    parts = key.split("/")
    if len(parts) < 4 or parts[:2] != ["raw", "satellite"]:
        raise ValueError(f"not a raw/satellite/ key: {key!r}")
    folder = parts[2]
    bid = STATIC_BUILDINGS if folder == "static=buildings" else folder
    batch_kind(bid)
    return bid


def zone_source(batch_id: str) -> str:
    """<source> used in quarantine/, validation/ and curated/_success/ paths."""
    return "buildings" if batch_kind(batch_id) == "buildings" else "satellite"


def contract_source(batch_id: str) -> str:
    """Name of cloud/pipeline/contracts/<name>.yaml for a batch."""
    return {"scene": "satellite_scene", "season": "satellite_season",
            "buildings": "buildings"}[batch_kind(batch_id)]


def s3_prefix_raw_batch(batch_id: str) -> str:
    if batch_kind(batch_id) == "buildings":
        return s3_prefix_raw_static("buildings")
    return f"raw/satellite/{batch_id}/"


def s3_key_raw_skipped(batch_id: str) -> str | None:
    """raw/satellite/skipped/<date>.json written by satellite_ingest for cloudy dates."""
    p = parse_period(batch_id) if batch_kind(batch_id) == "scene" else None
    return f"raw/satellite/skipped/{p.date_start.isoformat()}.json" if p else None


def s3_key_satellite_grid(period: str) -> str:
    p = parse_period(period)
    return (f"trusted/satellite_grid/period_type={p.period_type}/"
            f"year={p.year}/{period}.parquet")


def s3_key_buildings_grid(year: int) -> str:
    return f"trusted/buildings_grid/year={year}/buildings_{year}.parquet"


def s3_key_community_summary(method: str, period: str) -> str:
    if method not in METHODS:
        raise ValueError(f"unknown method: {method!r}")
    p = parse_period(period)
    return (f"curated/community_summary/method={method}/"
            f"year={p.year}/{period}.parquet")


def s3_key_validation(batch_id: str) -> str:
    return f"validation/{zone_source(batch_id)}/{batch_id}/report.json"


def s3_prefix_quarantine_batch(batch_id: str) -> str:
    return f"quarantine/batch/{zone_source(batch_id)}/{batch_id}/"


def s3_key_quarantine_rows(batch_id: str) -> str:
    return f"quarantine/rows/{zone_source(batch_id)}/{batch_id}/rows.csv"


def s3_key_quarantine_duplicate(batch_id: str) -> str:
    return f"quarantine/duplicate/{zone_source(batch_id)}/{batch_id}/rows.csv"


def s3_prefix_layers(batch_id: str) -> str:
    if batch_kind(batch_id) == "buildings":
        return "curated/satellite/layers/static/"
    return f"curated/satellite/layers/{batch_id}/"


def s3_key_success(batch_id: str) -> str:
    """Publish marker (holds the manifest hash CheckBatch compares with)."""
    return f"curated/_success/{zone_source(batch_id)}/{batch_id}.json"
