"""Data contracts (spec 32, D23): YAML loads, levels BLOCK / STANDARDIZE / WARN behave as specified."""
import csv
import io

import pytest

pytest.importorskip("yaml")

from common import contract as ct  # noqa: E402

META = {"total_pixels": 3, "crs": "EPSG:32647", "crs_transform": [30, 0, 660015, 0, -30, 1545015],
        "period_type": "scene", "valid_ratio": 0.9}
HEADER = ["x", "y", "lon", "lat", "lst_c", "ndvi", "ndbi", "cloud_mask"]
FILES = {"pixels.csv", "metadata.json", "_COMPLETE", "bands.tif"}


def rows(*vals):
    return [dict(zip(HEADER, v)) for v in vals]


GOOD = rows(
    ("663030", "1542030", "100.5", "13.9", "40.1", "0.2", "0.1", "1"),
    ("663060", "1542030", "100.5", "13.9", "-9999", "-9999", "-9999", "0"),
    ("663090", "1542030", "100.5", "13.9", "41.0", "0.3", "0.0", "1"),
)


@pytest.mark.parametrize("source", ["satellite_scene", "satellite_season", "buildings"])
def test_contracts_load(source):
    c = ct.load(source)
    assert c["source"] == source and c["columns"] and c["key"]


def test_good_scene_passes_and_nodata_becomes_null():
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, GOOD, FILES, META)
    assert r.status == "PASS", r.fail_reasons
    assert len(r.rows) == 3 and r.rows[1]["lst_c"] is None and r.rows[0]["cloud_mask"] == 1
    assert r.standardized["lst_c: nodata -> null"] == 1


def test_bom_header_is_stripped():
    hdr = ["﻿x"] + HEADER[1:]
    data = [{("﻿x" if k == "x" else k): v for k, v in r.items()} for r in GOOD]
    assert ct.evaluate(ct.load("satellite_scene"), hdr, data, FILES, META).status == "PASS"


MANY = rows(*[(str(663030 + 30 * i), "1541030", "100.5", "13.9", "40", "0.2", "0.1", "1") for i in range(9)])


def test_row_out_of_range_goes_to_rejected_not_fail():
    data = MANY + rows(("663120", "1542030", "100.5", "13.9", "95", "0.2", "0.1", "1"))
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, data, FILES, {**META, "total_pixels": 10})
    assert r.status == "PASS"
    assert len(r.rejected) == 1 and "lst_c in [0, 70]" in r.rejected[0]["_rule"]
    assert r.violations["lst_c in [0, 70]"]["rows"] == 1


def test_too_many_rejected_rows_fails_batch():
    bad = rows(*[("663120", str(1542030 + 30 * i), "100.5", "13.9", "95", "0", "0", "1") for i in range(3)])
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, GOOD + bad, FILES, {**META, "total_pixels": 6})
    assert r.status == "FAIL" and any("max_invalid_ratio" in f for f in r.fail_reasons)


@pytest.mark.parametrize("meta,files,why", [
    ({"crs_transform": [30, 0, 0, 0, -30, 0]}, FILES, "crs_transform"),
    ({"valid_ratio": 0.05}, FILES, "valid_ratio"),
    ({"total_pixels": 99}, FILES, "row_count_equals_metadata"),
    ({}, FILES - {"_COMPLETE"}, "required_files"),
])
def test_batch_checks_fail(meta, files, why):
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, GOOD, files, {**META, **meta})
    assert r.status == "FAIL" and any(why in f for f in r.fail_reasons), r.fail_reasons


def test_missing_column_fails_before_rows():
    hdr = HEADER[:-1]
    data = [{k: v for k, v in r.items() if k != "cloud_mask"} for r in GOOD]
    r = ct.evaluate(ct.load("satellite_scene"), hdr, data, FILES, META)
    assert r.status == "FAIL" and not r.rows


def test_duplicate_key_blocks_batch():
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, GOOD + GOOD[:1], FILES, {**META, "total_pixels": 4})
    assert r.status == "FAIL" and any("unique_key" in f for f in r.fail_reasons)


def test_bad_type_rejects_row():
    data = MANY + rows(("663150", "1542030", "100.5", "13.9", "hot", "0.2", "0.1", "1"))
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, data, FILES, {**META, "total_pixels": 10})
    assert r.status == "PASS" and r.rejected[0]["_rule"] == "lst_c is float"


def test_buildings_contract():
    text = ("cell_id,builtup_pct,building_count,floors_mean,far,in_cover,bldg_source\n"
            "c0100_r0100,35.5,12,2.5,0.9,True,bma_shp\n"
            "c0101_r0100,,,,,False,open_buildings\n"
            "bad,10,1,,,True,open_buildings\n")
    rd = csv.DictReader(io.StringIO(text))
    data = list(rd)
    meta = {"total_cells": 3, "crs": "EPSG:32647", "origin_x": 660015.0, "origin_y": 1545015.0}
    r = ct.evaluate(ct.load("buildings"), rd.fieldnames, data,
                    {"buildings_grid.csv", "metadata.json", "_COMPLETE"}, meta)
    # 1 of 3 rejected = 33% > 20% -> FAIL; but the row-level verdicts are still right
    assert [x["cell_id"] for x in r.rows] == ["c0100_r0100", "c0101_r0100"]
    assert r.rows[1]["in_cover"] is False and r.rows[1]["builtup_pct"] is None
    assert r.rejected[0]["_rule"] == "cell_id cell_id"
    assert r.status == "FAIL"


def test_report_shape():
    r = ct.evaluate(ct.load("satellite_scene"), HEADER, GOOD, FILES, META)
    rep = r.report("scene_2026-04-12", "2026-11-21T00:00:00+00:00")
    assert rep["status"] == "PASS" and rep["passed"] == 3 and rep["contract_version"] == 1


YAML11_BOOLS = {"y", "n", "yes", "no", "on", "off", "true", "false"}


@pytest.mark.parametrize("source", ["satellite_scene", "satellite_season", "buildings"])
def test_yaml11_bool_words_are_quoted(source):
    """Terraform yamldecode follows YAML 1.1: a bare `y` turns into true (seen on AWS)."""
    text = (ct._DIRS[0] / f"{source}.yaml").read_text(encoding="utf-8")
    c = ct.load(source)
    for name in ct.columns(c) + list(c["key"]):
        if name.lower() in YAML11_BOOLS:
            assert f'"{name}"' in text, f"{source}: quote column {name!r}"


def test_non_string_column_name_is_rejected():
    c = {"source": "s", "key": [True], "columns": [{"name": "x"}]}
    with pytest.raises(ValueError, match="must be strings"):
        ct._checked(c, "s")
