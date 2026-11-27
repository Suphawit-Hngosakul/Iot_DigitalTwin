"""Satellite schema (Landsat pixels -> 90 m grid).

Layers:
  1. Landsat 30 m pixels (GEE, raw GeoTIFF)          -> not a table
  2. grid_cells, satellite_grid, buildings_grid      -> per 90 m cell (grid.py)

Sources: LST, NDVI, NDBI all from Landsat 8/9 C2 L2 (one sensor, one pixel grid).
Buildings: BMA building.shp first, Google Open Buildings (conf >= 0.7) to fill gaps;
only per-cell summaries leave GEE (footprints must not be published).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping

from . import grid

# =============================================================================
#  Periods
# =============================================================================
# scene  : one acquisition DATE (images of the same day from two WRS rows /
#          two satellites are merged, so a date is counted once)
# season : Mar-May composite of one year (hot season)
# year   : whole-year composite

SEASON_MONTHS = (3, 5)
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

TABLES = {
    "grid_cells": GRID_CELLS,
    "satellite_grid": SATELLITE_GRID,
    "buildings_grid": BUILDINGS_GRID,
}
PARTITIONS = {
    "grid_cells": (),
    "satellite_grid": SATELLITE_GRID_PARTITIONS,
    "buildings_grid": BUILDINGS_GRID_PARTITIONS,
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
}
SATELLITE_VALUE_COLS = ("lst_mean", "lst_p90", "ndvi_mean", "ndbi_mean", "green_ratio")


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
