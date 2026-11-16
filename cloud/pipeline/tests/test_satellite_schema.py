from datetime import date
from pathlib import Path

import pytest

from common import grid as g
from common import satellite_schema as s

FIXTURES = Path(__file__).with_name("fixtures")


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


# ── communities ──────────────────────────────────────────────────────────────

def test_load_real_communities():
    cs = s.load_communities(FIXTURES / "communities.csv")
    assert len(cs) == 36
    assert {c.district for c in cs} == {"หลักสี่", "ดอนเมือง"}


def test_load_communities_rejects_duplicates(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(",".join(s.COMMUNITIES_COLUMNS) + "\n"
                 "C0001,a,หลักสี่,13.9,100.5,x\nC0001,b,หลักสี่,13.9,100.5,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        s.load_communities(p)


def test_buffer_cells_for_every_community():
    pytest.importorskip("pyproj")
    for c in s.load_communities(FIXTURES / "communities.csv"):
        for method, n_expected in (("buffer_300", 35), ("buffer_500", 97)):
            rows = s.community_cells_buffer(c, method)
            area = sum(r["weight"] for r in rows)
            assert abs(area - n_expected) / n_expected < 0.03, (c.community_id, method)
    with pytest.raises(ValueError):
        s.community_cells_buffer(c, "polygon")


# ── summary ──────────────────────────────────────────────────────────────────

def test_weighted_helpers():
    assert s.weighted_mean([(10, 1), (20, 3), (None, 5)]) == 17.5
    assert s.weighted_mean([(None, 1)]) is None
    assert s.weighted_quantile([(1, 1), (2, 1), (3, 1), (4, 1), (5, 6)], 0.9) == 5
    assert s.weighted_quantile([(1, 9), (100, 1)], 0.9) == 1


def test_summarize_community():
    cells = [
        {"community_id": "C0001", "method": "buffer_300", "cell_id": "c0001_r0001", "weight": 1.0},
        {"community_id": "C0001", "method": "buffer_300", "cell_id": "c0002_r0001", "weight": 0.5},
        {"community_id": "C0001", "method": "buffer_300", "cell_id": "c0003_r0001", "weight": 0.5},
    ]
    sat = {
        "c0001_r0001": sat_row(cell_id="c0001_r0001", lst_mean=40.0, n_scenes=11),
        "c0002_r0001": sat_row(cell_id="c0002_r0001", lst_mean=46.0, n_scenes=10),
        # cloudy cell: values nulled, must not pull the mean
        "c0003_r0001": s.clean_satellite_row(sat_row(cell_id="c0003_r0001", valid_pixel_ratio=0.2)),
    }
    bld = {
        "c0001_r0001": {"builtup_pct": 40.0, "building_count": 20, "in_cover": True},
        "c0002_r0001": {"builtup_pct": 10.0, "building_count": 4, "in_cover": True},
        "c0003_r0001": {"builtup_pct": 0.0, "building_count": 0, "in_cover": False},
    }
    out = s.summarize_community("C0001", "season_2026_03-05", cells, sat, bld)
    assert list(out) == [n for n, _ in s.COMMUNITY_SUMMARY]
    assert out["n_cells"] == 3 and out["area_m2"] == 2 * g.GRID_SIZE_M ** 2
    assert out["lst_mean"] == pytest.approx((40 * 1 + 46 * 0.5) / 1.5)
    assert out["lst_p90"] == 46.0
    assert out["n_scenes"] == 10
    assert out["valid_pixel_ratio"] == pytest.approx((1 * 1 + 1 * 0.5 + 0.2 * 0.5) / 2)
    assert out["in_cover_ratio"] == pytest.approx(0.75)
    assert out["builtup_pct"] == pytest.approx((40 + 10 * 0.5) / 1.5)
    assert out["building_count"] == 22


def test_summarize_without_buildings_and_missing_cells():
    cells = [{"community_id": "C0002", "method": "buffer_300", "cell_id": "c0009_r0009", "weight": 1.0}]
    out = s.summarize_community("C0002", "year_2026", cells, {}, None)
    assert out["lst_mean"] is None and out["valid_pixel_ratio"] == 0.0
    assert out["builtup_pct"] is None
    with pytest.raises(ValueError):
        s.summarize_community("C0002", "year_2026", [], {})


# ── S3 keys ──────────────────────────────────────────────────────────────────

def test_s3_keys():
    assert s.s3_prefix_raw_scene("2026-03-15") == "raw/satellite/scene_2026-03-15/"
    assert s.s3_prefix_raw_static("buildings") == "raw/satellite/static=buildings/"
    assert s.s3_key_satellite_grid("season_2026_03-05") == (
        "processed/satellite_grid/period_type=season/year=2026/season_2026_03-05.parquet")
    assert s.s3_key_buildings_grid(2026) == "processed/buildings_grid/year=2026/buildings_2026.parquet"
    assert s.s3_key_community_summary("buffer_300", "year_2026") == (
        "processed/community_summary/method=buffer_300/year=2026/year_2026.parquet")
    with pytest.raises(ValueError):
        s.s3_key_community_summary("buffer_1000", "year_2026")
    with pytest.raises(ValueError):
        s.s3_prefix_raw_static("../x")
