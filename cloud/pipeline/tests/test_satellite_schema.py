from datetime import date

import pytest

from common import grid as g
from common import satellite_schema as s

# ── periods ──────────────────────────────────────────────────────────────────

def test_periods():
    p = s.parse_period(s.scene_period("2026-03-15"))
    assert (p.period_type, p.date_start, p.date_end) == ("scene", date(2026, 3, 15), date(2026, 3, 15))
    p = s.parse_period(s.season_period(2026))
    assert p.period == "season_2026_03-05"
    assert (p.date_start, p.date_end) == (date(2026, 3, 1), date(2026, 5, 31))
    p = s.parse_period(s.year_period(2024))
    assert (p.date_start, p.date_end, p.year) == (date(2024, 1, 1), date(2024, 12, 31), 2024)


@pytest.mark.parametrize("bad", ["scene_2026-02-30", "season_2026_05-03", "season_2026_03-13",
                                 "year_26", "LC09_L2SP_129050_20260315", ""])
def test_bad_period(bad):
    with pytest.raises(ValueError):
        s.parse_period(bad)


# ── satellite rows ───────────────────────────────────────────────────────────

def sat_row(**kw):
    row = {"cell_id": "c0100_r0100", "period": "season_2026_03-05",
           "date_start": date(2026, 3, 1), "date_end": date(2026, 5, 31),
           "n_scenes": 11, "clear_obs_mean": 7.5, "valid_pixel_ratio": 1.0,
           "lst_mean": 45.0, "lst_p90": 47.0, "ndvi_mean": 0.25, "ndbi_mean": 0.05,
           "green_ratio": 0.3}
    row.update(kw)
    return row


def test_good_satellite_row():
    assert s.check_satellite_row(sat_row()) == []


def test_satellite_row_problems():
    probs = s.check_satellite_row(sat_row(lst_mean=80.0, ndvi_mean=1.5, cell_id="x"))
    assert "lst_mean out of range" in probs and "ndvi_mean out of range" in probs
    assert "bad cell_id" in probs
    assert "dates do not match period" in s.check_satellite_row(sat_row(date_end=date(2026, 6, 30)))
    assert "lst_p90 < lst_mean" in s.check_satellite_row(sat_row(lst_p90=44.0))


def test_scene_row_counts_one_date():
    row = sat_row(period="scene_2026-03-15", date_start=date(2026, 3, 15),
                  date_end=date(2026, 3, 15), n_scenes=2)
    assert "scene row must have n_scenes 0 or 1" in s.check_satellite_row(row)


def test_clean_nulls_low_coverage():
    out = s.clean_satellite_row(sat_row(valid_pixel_ratio=0.4))
    assert all(out[c] is None for c in s.SATELLITE_VALUE_COLS)
    assert out["valid_pixel_ratio"] == 0.4
    assert s.clean_satellite_row(sat_row())["lst_mean"] == 45.0


# ── tables ───────────────────────────────────────────────────────────────────

def test_tables_do_not_repeat_partition_keys():
    for t, cols in s.TABLES.items():
        names = {n for n, _ in cols}
        assert not names & {k for k, _ in s.PARTITIONS[t]}, t


def test_arrow_schema():
    pytest.importorskip("pyarrow")
    for t in s.TABLES:
        assert s.arrow_schema(t).names == [n for n, _ in s.TABLES[t]]


# ── S3 keys ──────────────────────────────────────────────────────────────────

def test_s3_keys():
    assert s.s3_prefix_raw_scene("2026-03-15") == "raw/satellite/scene_2026-03-15/"
    assert s.s3_prefix_raw_static("buildings") == "raw/satellite/static=buildings/"
    assert s.s3_key_satellite_grid("season_2026_03-05") == (
        "trusted/satellite_grid/period_type=season/year=2026/season_2026_03-05.parquet")
    assert s.s3_key_buildings_grid(2026) == "trusted/buildings_grid/year=2026/buildings_2026.parquet"
    with pytest.raises(ValueError):
        s.s3_prefix_raw_static("../x")
