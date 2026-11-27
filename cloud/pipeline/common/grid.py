"""Central 90 m grid (issue #29).

Every satellite / building / IoT value is summarised per 90 x 90 m cell
(= 3 x 3 Landsat pixels). The grid lives in EPSG:32647 (UTM 47N) and its edges
sit exactly on Landsat pixel edges, so a cell never splits a pixel.

cell_id = "c<col>_r<row>", counted from a FIXED origin (north-west corner),
col growing east and row growing south. Because the origin is fixed (not the
AOI bounding box), adding area to the AOI never renumbers existing cells.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

GRID_SIZE_M = 90
LANDSAT_PIXEL_M = 30
CRS = "EPSG:32647"

# North-west corner of the grid in EPSG:32647 metres.
# MUST stay on a Landsat pixel edge: (ORIGIN - tr) % 30 == 0, where tr is the
# ST_B10 transform (tr[2], tr[5]) recorded in spike #26. The values below are on
# the usual Landsat L2 UTM lattice (edges at ...15 m) and lie NW of Lak Si +
# Don Mueang. Re-check against #26 before publishing any data; changing the
# origin later renumbers every cell.
ORIGIN_X = 660_015.0
ORIGIN_Y = 1_545_015.0

CELL_ID_RE = re.compile(r"^c(?P<col>\d{4})_r(?P<row>\d{4})$")


@dataclass(frozen=True)
class Cell:
    col: int
    row: int

    @property
    def cell_id(self) -> str:
        return make_cell_id(self.col, self.row)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(x_min, y_min, x_max, y_max) in EPSG:32647."""
        x_min = ORIGIN_X + self.col * GRID_SIZE_M
        y_max = ORIGIN_Y - self.row * GRID_SIZE_M
        return x_min, y_max - GRID_SIZE_M, x_min + GRID_SIZE_M, y_max

    @property
    def center_xy(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds
        return (x0 + x1) / 2, (y0 + y1) / 2


def make_cell_id(col: int, row: int) -> str:
    if not (0 <= col <= 9999 and 0 <= row <= 9999):
        raise ValueError(f"cell out of grid: col={col} row={row}")
    return f"c{col:04d}_r{row:04d}"


def parse_cell_id(cell_id: str) -> Cell:
    m = CELL_ID_RE.match(cell_id)
    if not m:
        raise ValueError(f"bad cell_id: {cell_id!r}")
    return Cell(int(m["col"]), int(m["row"]))


def xy_to_cell(x: float, y: float) -> Cell | None:
    """Cell holding a point in EPSG:32647, or None if west/north of the origin.

    A point on a shared edge belongs to the cell east / south of it.
    """
    col = math.floor((x - ORIGIN_X) / GRID_SIZE_M)
    row = math.floor((ORIGIN_Y - y) / GRID_SIZE_M)
    if col < 0 or row < 0 or col > 9999 or row > 9999:
        return None
    return Cell(col, row)


def cell_bounds(cell_id: str) -> tuple[float, float, float, float]:
    return parse_cell_id(cell_id).bounds


# WGS84 <-> EPSG:32647 (UTM Zone 47N) constants for pure-Python Lambda fallback
_WGS84_A = 6_378_137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)
_WGS84_EP2 = _WGS84_E2 / (1.0 - _WGS84_E2)
_UTM47_LON0 = math.radians(99.0)
_UTM_K0 = 0.9996
_UTM_E0 = 500_000.0


def _utm47n_forward(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 (lon, lat) degrees -> EPSG:32647 (x, y) metres."""
    phi = math.radians(lat)
    lam = math.radians(lon)
    sin_p = math.sin(phi)
    cos_p = math.cos(phi)
    tan_p = math.tan(phi)

    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_p * sin_p)
    t = tan_p * tan_p
    c = _WGS84_EP2 * cos_p * cos_p
    a = (lam - _UTM47_LON0) * cos_p

    e2 = _WGS84_E2
    e4 = e2 * e2
    e6 = e4 * e2
    m = _WGS84_A * (
        (1.0 - e2 / 4.0 - 3.0 * e4 / 64.0 - 5.0 * e6 / 256.0) * phi
        - (3.0 * e2 / 8.0 + 3.0 * e4 / 32.0 + 45.0 * e6 / 1024.0) * math.sin(2.0 * phi)
        + (15.0 * e4 / 256.0 + 45.0 * e6 / 1024.0) * math.sin(4.0 * phi)
        - (35.0 * e6 / 3072.0) * math.sin(6.0 * phi)
    )

    x = _UTM_E0 + _UTM_K0 * n * (
        a
        + (1.0 - t + c) * (a ** 3) / 6.0
        + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * _WGS84_EP2) * (a ** 5) / 120.0
    )
    y = _UTM_K0 * (
        m
        + n
        * tan_p
        * (
            (a * a) / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * (a ** 4) / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * _WGS84_EP2) * (a ** 6) / 720.0
        )
    )
    return x, y


def _utm47n_inverse(x: float, y: float) -> tuple[float, float]:
    """EPSG:32647 (x, y) metres -> WGS84 (lon, lat) degrees."""
    e2 = _WGS84_E2
    e4 = e2 * e2
    e6 = e4 * e2
    e1 = (1.0 - math.sqrt(1.0 - e2)) / (1.0 + math.sqrt(1.0 - e2))

    m = y / _UTM_K0
    mu = m / (_WGS84_A * (1.0 - e2 / 4.0 - 3.0 * e4 / 64.0 - 5.0 * e6 / 256.0))
    phi1 = (
        mu
        + (3.0 * e1 / 2.0 - 27.0 * (e1 ** 3) / 32.0) * math.sin(2.0 * mu)
        + (21.0 * (e1 ** 2) / 16.0 - 55.0 * (e1 ** 4) / 32.0) * math.sin(4.0 * mu)
        + (151.0 * (e1 ** 3) / 96.0) * math.sin(6.0 * mu)
        + (1097.0 * (e1 ** 4) / 512.0) * math.sin(8.0 * mu)
    )

    sin_p1 = math.sin(phi1)
    cos_p1 = math.cos(phi1)
    tan_p1 = math.tan(phi1)

    c1 = _WGS84_EP2 * cos_p1 * cos_p1
    t1 = tan_p1 * tan_p1
    n1 = _WGS84_A / math.sqrt(1.0 - e2 * sin_p1 * sin_p1)
    r1 = _WGS84_A * (1.0 - e2) / ((1.0 - e2 * sin_p1 * sin_p1) ** 1.5)
    d = (x - _UTM_E0) / (n1 * _UTM_K0)

    phi = phi1 - (n1 * tan_p1 / r1) * (
        (d * d) / 2.0
        - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1 * c1 - 9.0 * _WGS84_EP2) * (d ** 4) / 24.0
        + (61.0 + 90.0 * t1 + 298.0 * c1 + 45.0 * t1 * t1 - 252.0 * _WGS84_EP2 - 3.0 * c1 * c1)
        * (d ** 6)
        / 720.0
    )
    lam = _UTM47_LON0 + (
        d
        - (1.0 + 2.0 * t1 + c1) * (d ** 3) / 6.0
        + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1 * c1 + 8.0 * _WGS84_EP2 + 24.0 * t1 * t1)
        * (d ** 5)
        / 120.0
    ) / cos_p1
    return math.degrees(lam), math.degrees(phi)


class _PureUtmForward:
    def transform(self, lon: float, lat: float) -> tuple[float, float]:
        return _utm47n_forward(lon, lat)


class _PureUtmInverse:
    def transform(self, x: float, y: float) -> tuple[float, float]:
        return _utm47n_inverse(x, y)


_to_utm = None
_to_wgs = None


def _transformers():
    global _to_utm, _to_wgs
    if _to_utm is None:
        try:
            from pyproj import Transformer  # imported lazily: heavy, only needed here

            _to_utm = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
            _to_wgs = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
        except ModuleNotFoundError:
            _to_utm = _PureUtmForward()
            _to_wgs = _PureUtmInverse()
    return _to_utm, _to_wgs


def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    return _transformers()[0].transform(lon, lat)


def xy_to_latlon(x: float, y: float) -> tuple[float, float]:
    lon, lat = _transformers()[1].transform(x, y)
    return lat, lon


def latlon_to_cell(lat: float | None, lon: float | None) -> str | None:
    """cell_id for a WGS84 point; None when the point is missing or off-grid."""
    if lat is None or lon is None:
        return None
    cell = xy_to_cell(*latlon_to_xy(lat, lon))
    return cell.cell_id if cell else None


def cells_in_circle(x: float, y: float, radius_m: float,
                    samples: int = 10) -> list[tuple[str, float]]:
    """(cell_id, weight) for every cell a circle touches.

    weight = share of the cell's area inside the circle (0-1], estimated on a
    samples x samples lattice of points per cell (10 -> 1% steps).
    """
    if radius_m <= 0:
        raise ValueError("radius_m must be > 0")
    lo = xy_to_cell(x - radius_m, y + radius_m)
    hi = xy_to_cell(x + radius_m, y - radius_m)
    if lo is None or hi is None:
        raise ValueError("circle reaches outside the grid; move ORIGIN")
    step = GRID_SIZE_M / samples
    r2 = radius_m * radius_m
    out = []
    for row in range(lo.row, hi.row + 1):
        for col in range(lo.col, hi.col + 1):
            x0, _, _, y1 = Cell(col, row).bounds
            inside = 0
            for i in range(samples):
                px = x0 + (i + 0.5) * step - x
                for j in range(samples):
                    py = y1 - (j + 0.5) * step - y
                    if px * px + py * py <= r2:
                        inside += 1
            if inside:
                out.append((make_cell_id(col, row), inside / (samples * samples)))
    return out
