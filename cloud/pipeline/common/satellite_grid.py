"""Satellite pixels -> 90 m cells, and map layers as PNG (issue #31, spec 51 §4).

Input pixels come from raw/satellite/<period>/pixels.csv after the contract
(spec 32) has typed them: x, y = pixel centre in EPSG:32647, lst_c / ndvi /
ndbi (None = nodata), and cloud_mask (scene, 1 = clear) or n_valid (season,
number of clear images in the median).

satellite_ingest aligns every pixel to grid.ORIGIN (crsTransform), so each
90 m cell holds exactly 3 x 3 pixels and no resampling happens here (D11).

PNG is written with zlib only (no Pillow/rasterio in the Lambda layer).
"""

from __future__ import annotations

import math
import struct
import zlib
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from . import grid
from . import satellite_schema as ss

PX = grid.LANDSAT_PIXEL_M
PX_PER_CELL = (grid.GRID_SIZE_M // PX) ** 2          # 9


# =============================================================================
#  Pixels -> cells
# =============================================================================

def pixel_is_valid(p: Mapping, kind: str) -> bool:
    """A pixel counts when it has LST and is clear (scene) / had clear images (season)."""
    if p.get("lst_c") is None:
        return False
    if kind == "scene":
        return p.get("cloud_mask") == 1
    return (p.get("n_valid") or 0) > 0


def _is_center_pixel(cell: grid.Cell, x: float, y: float) -> bool:
    cx, cy = cell.center_xy
    return abs(x - cx) < PX / 2 and abs(y - cy) < PX / 2


def _mean(vals: Sequence[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _p90(vals: Sequence[float]) -> float | None:
    """Nearest-rank 90th percentile (with <= 10 values this is the max)."""
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]


def group_by_cell(pixels: Iterable[Mapping]) -> dict[str, list[Mapping]]:
    cells: dict[str, list[Mapping]] = defaultdict(list)
    for p in pixels:
        cell = grid.xy_to_cell(p["x"], p["y"])
        if cell is not None:
            cells[cell.cell_id].append(p)
    return cells


def in_aoi_cells(cells: Mapping[str, list[Mapping]]) -> list[str]:
    """Cells whose centre pixel is in pixels.csv (= centre inside the clipped AOI)."""
    out = []
    for cid, pxs in cells.items():
        cell = grid.parse_cell_id(cid)
        if any(_is_center_pixel(cell, p["x"], p["y"]) for p in pxs):
            out.append(cid)
    return sorted(out)


def aggregate_pixels(pixels: Iterable[Mapping], period: str) -> list[dict]:
    """satellite_grid rows (satellite_schema.SATELLITE_GRID) for one period, AOI cells only."""
    p = ss.parse_period(period)
    kind = p.period_type
    if kind not in ("scene", "season"):
        raise ValueError(f"cannot aggregate period type {kind!r}")
    cells = group_by_cell(pixels)
    rows = []
    for cid in in_aoi_cells(cells):
        pxs = cells[cid]
        valid = [q for q in pxs if pixel_is_valid(q, kind)]
        lst = [q["lst_c"] for q in valid]
        ndvi = [q["ndvi"] for q in valid if q.get("ndvi") is not None]
        ndbi = [q["ndbi"] for q in valid if q.get("ndbi") is not None]
        if kind == "scene":
            clear_obs = _mean([1.0 if q.get("cloud_mask") == 1 else 0.0 for q in pxs])
            n_scenes = 1 if valid else 0
        else:
            clear_obs = _mean([float(q.get("n_valid") or 0) for q in pxs])
            # images (not distinct dates) that the median used, at the best pixel
            n_scenes = max((int(q.get("n_valid") or 0) for q in pxs), default=0)
        row = {
            "cell_id": cid,
            "period": period,
            "date_start": p.date_start,
            "date_end": p.date_end,
            "n_scenes": n_scenes,
            "clear_obs_mean": clear_obs,
            "valid_pixel_ratio": len(valid) / PX_PER_CELL,
            "lst_mean": _mean(lst),
            "lst_p90": _p90(lst),
            "ndvi_mean": _mean(ndvi),
            "ndbi_mean": _mean(ndbi),
            "green_ratio": (sum(1 for v in ndvi if v > ss.GREEN_NDVI) / len(ndvi)) if ndvi else None,
        }
        rows.append(ss.clean_satellite_row(row))
    return rows


def buildings_rows(rows: Iterable[Mapping]) -> list[dict]:
    """buildings_grid rows (satellite_schema.BUILDINGS_GRID) from contract-typed CSV rows."""
    out = []
    for r in rows:
        out.append({name: r.get(name) for name, _ in ss.BUILDINGS_GRID})
    return sorted(out, key=lambda r: r["cell_id"])


# =============================================================================
#  PNG
# =============================================================================

def png_rgba(width: int, height: int, rgba_rows: Sequence[bytes]) -> bytes:
    """Encode RGBA8 rows (each width*4 bytes) as a PNG file."""
    if len(rgba_rows) != height or any(len(r) != width * 4 for r in rgba_rows):
        raise ValueError("rows do not match width/height")

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + r for r in rgba_rows)      # filter type 0 per row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# Fixed colour scales so every scene compares (spec 51 §4). Stops: (t 0..1, (r, g, b)).
COLORMAPS: dict[str, dict] = {
    "lst": {"column": "lst_c", "min": 25.0, "max": 50.0, "unit": "°C",
            "stops": [(0.0, (49, 54, 149)), (0.25, (116, 173, 209)), (0.5, (255, 255, 191)),
                      (0.75, (244, 109, 67)), (1.0, (165, 0, 38))]},
    "ndvi": {"column": "ndvi", "min": -0.2, "max": 0.8, "unit": "",
             "stops": [(0.0, (140, 81, 10)), (0.3, (246, 232, 195)), (0.6, (127, 188, 65)),
                       (1.0, (0, 104, 55))]},
    "ndbi": {"column": "ndbi", "min": -0.5, "max": 0.5, "unit": "",
             "stops": [(0.0, (1, 102, 94)), (0.5, (245, 245, 245)), (1.0, (140, 81, 10))]},
    "building_density": {"column": "builtup_pct", "min": 0.0, "max": 100.0, "unit": "%",
                         "stops": [(0.0, (255, 255, 204)), (0.5, (253, 141, 60)),
                                   (1.0, (128, 0, 38))]},
}
ALPHA = 210


def color(value: float | None, cmap: Mapping) -> tuple[int, int, int, int]:
    if value is None:
        return (0, 0, 0, 0)
    t = (value - cmap["min"]) / (cmap["max"] - cmap["min"])
    t = min(1.0, max(0.0, t))
    stops = cmap["stops"]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1)) + (ALPHA,)
    return tuple(stops[-1][1]) + (ALPHA,)


def _corners(x_min: float, y_min: float, x_max: float, y_max: float) -> list[list[float]]:
    """[lon, lat] of top-left, top-right, bottom-right, bottom-left (MapLibre image source)."""
    pts = [(x_min, y_max), (x_max, y_max), (x_max, y_min), (x_min, y_min)]
    out = []
    for x, y in pts:
        lat, lon = grid.xy_to_latlon(x, y)
        out.append([round(lon, 7), round(lat, 7)])
    return out


def _legend(name: str, cmap: Mapping) -> dict:
    return {"layer": name, "column": cmap["column"], "min": cmap["min"], "max": cmap["max"],
            "unit": cmap["unit"], "stops": [[t, list(c)] for t, c in cmap["stops"]]}


def render_pixels(pixels: Sequence[Mapping], layer: str, kind: str | None,
                  step: float = PX) -> tuple[bytes, dict]:
    """30 m overlay of one layer: (png, bounds). Cloudy / nodata pixels are transparent.
    kind None = no clear-sky check (per-cell values)."""
    cmap = COLORMAPS[layer]
    if not pixels:
        raise ValueError("no pixels")
    xs = [p["x"] for p in pixels]
    ys = [p["y"] for p in pixels]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    width = int(round((x1 - x0) / step)) + 1
    height = int(round((y1 - y0) / step)) + 1
    img = [bytearray(width * 4) for _ in range(height)]
    for p in pixels:
        v = p.get(cmap["column"]) if kind is None or pixel_is_valid(p, kind) else None
        c = int(round((p["x"] - x0) / step))
        r = int(round((y1 - p["y"]) / step))
        img[r][c * 4:c * 4 + 4] = bytes(color(v, cmap))
    png = png_rgba(width, height, [bytes(r) for r in img])
    half = step / 2
    bounds = {
        "coordinates": _corners(x0 - half, y0 - half, x1 + half, y1 + half),
        "crs_source": grid.CRS, "pixel_m": step, "width": width, "height": height,
        "legend": _legend(layer, cmap),
    }
    return png, bounds


def render_cells(rows: Sequence[Mapping], layer: str) -> tuple[bytes, dict]:
    """90 m overlay of a per-cell value (building density): one PNG pixel per cell (D17)."""
    cmap = COLORMAPS[layer]
    pts = []
    for r in rows:
        cell = grid.parse_cell_id(r["cell_id"])
        cx, cy = cell.center_xy
        v = r.get(cmap["column"]) if r.get("in_cover", True) else None
        pts.append({"x": cx, "y": cy, cmap["column"]: v})
    return render_pixels(pts, layer, None, step=grid.GRID_SIZE_M)
