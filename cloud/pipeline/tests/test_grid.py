import math

import pytest

from common import grid as g


def test_cell_id_roundtrip():
    assert g.make_cell_id(12, 345) == "c0012_r0345"
    c = g.parse_cell_id("c0012_r0345")
    assert (c.col, c.row) == (12, 345) and c.cell_id == "c0012_r0345"


@pytest.mark.parametrize("bad", ["c12_r345", "c0012r0345", "x0012_r0345", "c0012_r-001"])
def test_bad_cell_id(bad):
    with pytest.raises(ValueError):
        g.parse_cell_id(bad)


def test_origin_on_landsat_lattice():
    # Landsat L2 UTM pixel edges sit at ...15 m; grid edges must too.
    assert (g.ORIGIN_X - 15) % g.LANDSAT_PIXEL_M == 0
    assert (g.ORIGIN_Y - 15) % g.LANDSAT_PIXEL_M == 0
    assert g.GRID_SIZE_M % g.LANDSAT_PIXEL_M == 0


def test_xy_to_cell_and_bounds():
    c = g.xy_to_cell(g.ORIGIN_X + 1, g.ORIGIN_Y - 1)
    assert (c.col, c.row) == (0, 0)
    assert c.bounds == (g.ORIGIN_X, g.ORIGIN_Y - 90, g.ORIGIN_X + 90, g.ORIGIN_Y)
    c = g.xy_to_cell(g.ORIGIN_X + 200, g.ORIGIN_Y - 100)
    assert (c.col, c.row) == (2, 1)


def test_point_on_edge_goes_east_and_south():
    c = g.xy_to_cell(g.ORIGIN_X + 90, g.ORIGIN_Y - 90)
    assert (c.col, c.row) == (1, 1)


def test_outside_grid_is_none():
    assert g.xy_to_cell(g.ORIGIN_X - 1, g.ORIGIN_Y - 1) is None
    assert g.xy_to_cell(g.ORIGIN_X + 1, g.ORIGIN_Y + 1) is None
    assert g.latlon_to_cell(None, 100.5) is None


def test_every_cell_center_maps_back():
    for col, row in [(0, 0), (57, 123), (200, 190)]:
        cell = g.Cell(col, row)
        assert g.xy_to_cell(*cell.center_xy) == cell


def test_latlon_roundtrip_and_aoi_inside_grid():
    pytest.importorskip("pyproj")
    # Lak Si + Don Mueang communities (south-west and north-east extremes)
    for lat, lon in [(13.8594, 100.5475), (13.944, 100.613), (13.9493, 100.5711)]:
        cid = g.latlon_to_cell(lat, lon)
        assert cid is not None
        x, y = g.latlon_to_xy(lat, lon)
        x0, y0, x1, y1 = g.cell_bounds(cid)
        assert x0 <= x < x1 and y0 < y <= y1
        lat2, lon2 = g.xy_to_latlon(x, y)
        assert abs(lat2 - lat) < 1e-7 and abs(lon2 - lon) < 1e-7


def test_cells_in_circle_area_matches_circle():
    x, y = g.Cell(100, 100).center_xy
    for r in (300, 500):
        cells = g.cells_in_circle(x, y, r)
        area = sum(w for _, w in cells) * g.GRID_SIZE_M ** 2
        assert abs(area - math.pi * r * r) / (math.pi * r * r) < 0.02
        assert all(0 < w <= 1 for _, w in cells)
    # cell holding the centre is fully inside
    assert dict(g.cells_in_circle(x, y, 300))[g.Cell(100, 100).cell_id] == 1.0


def test_cells_in_circle_rejects_bad_radius():
    with pytest.raises(ValueError):
        g.cells_in_circle(g.ORIGIN_X + 1000, g.ORIGIN_Y - 1000, 0)
    with pytest.raises(ValueError):
        g.cells_in_circle(g.ORIGIN_X + 10, g.ORIGIN_Y - 10, 300)


def test_pure_python_utm47n_matches_pyproj():
    pytest.importorskip("pyproj")
    for lat, lon in [(13.8594, 100.5475), (13.8850, 100.5800), (13.9493, 100.5711)]:
        px, py = g.latlon_to_xy(lat, lon)
        fx, fy = g._utm47n_forward(lon, lat)
        assert abs(px - fx) < 1e-4 and abs(py - fy) < 1e-4
        flon, flat = g._utm47n_inverse(fx, fy)
        assert abs(flat - lat) < 1e-8 and abs(flon - lon) < 1e-8
