"""Satellite pipeline (#30, #31, spec 51): every step on a synthetic batch in moto S3."""
import csv
import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

pa = pytest.importorskip("pyarrow")
pytest.importorskip("yaml")
import pyarrow.parquet as pq  # noqa: E402

from common import grid  # noqa: E402
from common import satellite_grid as sg  # noqa: E402
from common import satellite_schema as ss  # noqa: E402

FUNCTIONS = Path(__file__).resolve().parents[1] / "functions"
ASL = Path(__file__).resolve().parents[1] / "statemachine" / "satellite-pipeline.asl.json"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"sat_{name}", FUNCTIONS / name / "handler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


check_fn, validate_fn, quarantine_fn = _load("check_batch"), _load("validate_batch"), _load("quarantine_batch")
write_fn, render_fn, verify_fn = _load("write_trusted"), _load("render_layers"), _load("verify_batch")

BUCKET = "test-data"
SCENE = "scene_2026-04-12"
SEASON = "season_2026_03-05"
COL0, ROW0, NCOL, NROW = 100, 200, 4, 3      # 12 cells x 9 pixels
COLS = ["x", "y", "lon", "lat", "lst_c", "ndvi", "ndbi"]


def pixels(extra="cloud_mask", cloudy_cells=(), lst=lambda c, r, k: 35.0 + (c - COL0) + 0.1 * k):
    """3x3 pixels per cell; cloudy_cells get cloud_mask 0 (scene) / n_valid 0 (season)."""
    out = []
    for r in range(ROW0, ROW0 + NROW):
        for c in range(COL0, COL0 + NCOL):
            x0, _, _, y1 = grid.Cell(c, r).bounds
            for k in range(9):
                x = x0 + 15 + 30 * (k % 3)
                y = y1 - 15 - 30 * (k // 3)
                lat, lon = grid.xy_to_latlon(x, y)
                cloudy = (c, r) in cloudy_cells
                out.append({"x": x, "y": y, "lon": round(lon, 6), "lat": round(lat, 6),
                            "lst_c": -9999 if cloudy else lst(c, r, k),
                            "ndvi": -9999 if cloudy else 0.1 * (k % 5),
                            "ndbi": -9999 if cloudy else 0.05,
                            extra: (0 if cloudy else (1 if extra == "cloud_mask" else 6))})
    return out


def csv_bytes(rows, cols):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def meta(period, n, **kw):
    m = {"period": period, "period_type": ss.parse_period(period).period_type, "total_pixels": n,
         "crs": "EPSG:32647", "crs_transform": [30, 0, 660015.0, 0, -30, 1545015.0],
         "valid_ratio": 0.9, "nodata": -9999.0}
    m.update(kw)
    return m


def put_batch(s3, period, rows=None, complete=True, **meta_kw):
    extra = "cloud_mask" if period.startswith("scene_") else "n_valid"
    rows = rows if rows is not None else pixels(extra)
    prefix = f"raw/satellite/{period}/"
    s3.put_object(Bucket=BUCKET, Key=prefix + "pixels.csv", Body=csv_bytes(rows, COLS + [extra]))
    s3.put_object(Bucket=BUCKET, Key=prefix + "metadata.json",
                  Body=json.dumps(meta(period, len(rows), **meta_kw)).encode())
    s3.put_object(Bucket=BUCKET, Key=prefix + "bands.tif", Body=b"II*\x00")
    if complete:
        s3.put_object(Bucket=BUCKET, Key=prefix + "_COMPLETE", Body=b"")


def put_buildings(s3):
    rows = []
    for r in range(ROW0, ROW0 + NROW):
        for c in range(COL0, COL0 + NCOL):
            cov = c != COL0
            rows.append({"cell_id": grid.make_cell_id(c, r), "builtup_pct": 10.0 * (c - COL0) if cov else "",
                         "building_count": 3 if cov else "", "floors_mean": 2.0 if cov else "",
                         "far": 0.5 if cov else "", "in_cover": cov,
                         "bldg_source": "bma_shp" if cov else "open_buildings"})
    prefix = "raw/satellite/static=buildings/"
    cols = ["cell_id", "builtup_pct", "building_count", "floors_mean", "far", "in_cover", "bldg_source"]
    s3.put_object(Bucket=BUCKET, Key=prefix + "buildings_grid.csv", Body=csv_bytes(rows, cols))
    s3.put_object(Bucket=BUCKET, Key=prefix + "metadata.json", Body=json.dumps({
        "crs": "EPSG:32647", "origin_x": 660015.0, "origin_y": 1545015.0, "total_cells": len(rows),
        "processed_at": "2026-11-19T10:00:00+00:00"}).encode())
    s3.put_object(Bucket=BUCKET, Key=prefix + "_COMPLETE", Body=b"")


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", BUCKET)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def run(s3, event):
    """CheckBatch -> ... -> VerifyBatch the way the state machine chains outputs."""
    ev = check_fn.check(s3, BUCKET, *_ids(event))
    ev = {**event, "batch_id": _ids(event)[0], "check": ev}
    if ev["check"]["status"] not in ("NEW", "CHANGED"):
        return ev
    ev["validation"] = validate_fn.validate(s3, BUCKET, ev)
    if ev["validation"]["status"] == "FAIL":
        ev["quarantine"] = quarantine_fn.quarantine(s3, BUCKET, ev)
        return ev
    ev["trusted"] = write_fn.write(s3, BUCKET, ev)
    ev["layers"] = render_fn.render(s3, BUCKET, ev)
    ev["verify"] = verify_fn.verify(s3, BUCKET, ev)
    return ev


def _ids(event):
    return check_fn.resolve_batch_id(event), event.get("mode", "normal")


def body(s3, key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


# ---------------------------------------------------------------- aggregation

def test_aggregate_scene_cells():
    rows = sg.aggregate_pixels(_typed(pixels(cloudy_cells={(COL0, ROW0)})), SCENE)
    assert len(rows) == NCOL * NROW
    first = next(r for r in rows if r["cell_id"] == grid.make_cell_id(COL0, ROW0))
    assert first["valid_pixel_ratio"] == 0 and first["lst_mean"] is None and first["n_scenes"] == 0
    c1 = next(r for r in rows if r["cell_id"] == grid.make_cell_id(COL0 + 1, ROW0))
    assert c1["valid_pixel_ratio"] == 1 and c1["n_scenes"] == 1
    assert c1["lst_mean"] == pytest.approx(36.0 + 0.4)       # 35 + col offset 1 + mean(0.1*k)
    assert c1["lst_p90"] == pytest.approx(36.8)
    assert c1["date_start"] == c1["date_end"] == date(2026, 4, 12)
    assert all(ss.check_satellite_row(r) == [] for r in rows)


def test_aggregate_drops_cells_without_centre_pixel():
    px = [p for p in _typed(pixels()) if not (grid.xy_to_cell(p["x"], p["y"]).col == COL0
                                              and abs(p["x"] - grid.Cell(COL0, 0).center_xy[0]) < 1)]
    rows = sg.aggregate_pixels(px, SCENE)
    assert len(rows) == NCOL * NROW - NROW      # the 3 cells of column COL0 lost their centre pixel


def test_png_is_valid():
    png, bounds = sg.render_pixels(_typed(pixels()), "lst", "scene")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (bounds["width"], bounds["height"]) == (NCOL * 3, NROW * 3)
    assert len(bounds["coordinates"]) == 4 and bounds["legend"]["min"] == 25.0


def _typed(rows):
    out = []
    for r in rows:
        t = dict(r)
        for k in ("lst_c", "ndvi", "ndbi"):
            t[k] = None if t[k] == -9999 else float(t[k])
        out.append(t)
    return out


# ---------------------------------------------------------------- pipeline

def test_scene_published_end_to_end(s3):
    put_batch(s3, SCENE)
    ev = run(s3, {"s3_key": f"raw/satellite/{SCENE}/_COMPLETE", "mode": "normal"})
    assert ev["check"]["status"] == "NEW" and ev["validation"]["status"] == "PASS"
    key = "trusted/satellite_grid/period_type=scene/year=2026/scene_2026-04-12.parquet"
    assert ev["trusted"] == {"table": "satellite_grid", "key": key, "rows": NCOL * NROW}
    t = pq.read_table(io.BytesIO(body(s3, key)))
    assert t.schema == ss.arrow_schema("satellite_grid") and t.num_rows == NCOL * NROW
    for f in ("lst.png", "ndvi.png", "ndbi.png", "lst.json"):
        assert body(s3, f"curated/satellite/layers/{SCENE}/{f}")
    marker = json.loads(body(s3, f"curated/_success/satellite/{SCENE}.json"))
    assert marker["manifest_hash"] == ev["check"]["manifest_hash"] and marker["trusted_rows"] == 12
    report = json.loads(body(s3, f"validation/satellite/{SCENE}/report.json"))
    assert report["status"] == "PASS" and report["passed"] == NCOL * NROW * 9
    # second run: nothing changed
    assert run(s3, {"batch_id": SCENE})["check"]["status"] == "ALREADY_PROCESSED"
    assert run(s3, {"batch_id": SCENE, "mode": "force"})["check"]["status"] == "CHANGED"


def test_season_published(s3):
    put_batch(s3, SEASON)
    ev = run(s3, {"batch_id": SEASON})
    assert ev["verify"]["trusted_rows"] == NCOL * NROW
    t = pq.read_table(io.BytesIO(body(s3, ev["trusted"]["key"]))).to_pylist()
    assert {r["n_scenes"] for r in t} == {6}
    assert ev["trusted"]["key"].startswith("trusted/satellite_grid/period_type=season/year=2026/")


def test_out_of_range_rows_go_to_quarantine_rows(s3):
    rows = pixels()
    rows[0]["lst_c"] = 95.0
    put_batch(s3, SCENE, rows=rows)
    ev = run(s3, {"batch_id": SCENE})
    assert ev["validation"]["quarantined_rows"] == 1 and ev["verify"]["quarantined_rows"] == 1
    rej = body(s3, f"quarantine/rows/satellite/{SCENE}/rows.csv").decode()
    assert "lst_c in [0, 70]" in rej


def test_cloudy_scene_is_quarantined(s3):
    put_batch(s3, SCENE, valid_ratio=0.05)
    ev = run(s3, {"batch_id": SCENE})
    assert ev["validation"]["status"] == "FAIL" and "valid_ratio" in ev["validation"]["reason"]
    reason = json.loads(body(s3, f"quarantine/batch/satellite/{SCENE}/reason.json"))
    assert reason["batch_id"] == SCENE and len(reason["source_files"]) == 4
    assert body(s3, f"quarantine/batch/satellite/{SCENE}/pixels.csv")
    assert body(s3, f"raw/satellite/{SCENE}/pixels.csv")      # raw untouched


def test_no_data_cases(s3):
    put_batch(s3, SCENE, complete=False)
    assert run(s3, {"batch_id": SCENE})["check"]["status"] == "NO_DATA"
    s3.put_object(Bucket=BUCKET, Key="raw/satellite/skipped/2026-04-13.json", Body=b"{}")
    ev = run(s3, {"batch_id": "scene_2026-04-13"})
    assert ev["check"]["status"] == "NO_DATA" and "skipped" in ev["check"]["reason"]


def test_invalid_input():
    for event in ({"batch_id": "foo"}, {"s3_key": "raw/csv/x/_COMPLETE"}, {"batch_id": SCENE, "mode": "x"}, {}):
        assert check_fn.handler(event)["check"]["status"] == "INVALID_INPUT"


def test_buildings_published(s3):
    put_buildings(s3)
    ev = run(s3, {"s3_key": "raw/satellite/static=buildings/_COMPLETE"})
    assert ev["batch_id"] == "static_buildings"
    assert ev["trusted"]["key"] == "trusted/buildings_grid/year=2026/buildings_2026.parquet"
    t = pq.read_table(io.BytesIO(body(s3, ev["trusted"]["key"]))).to_pylist()
    assert len(t) == NCOL * NROW and sum(1 for r in t if not r["in_cover"]) == NROW
    assert body(s3, "curated/satellite/layers/static/building_density.png")[:4] == b"\x89PNG"
    assert body(s3, "curated/_success/buildings/static_buildings.json")


def test_verify_detects_raw_change(s3):
    put_batch(s3, SCENE)
    ev = {"batch_id": SCENE, "mode": "normal"}
    ev["check"] = check_fn.check(s3, BUCKET, SCENE, "normal")
    ev["validation"] = validate_fn.validate(s3, BUCKET, ev)
    ev["trusted"] = write_fn.write(s3, BUCKET, ev)
    ev["layers"] = render_fn.render(s3, BUCKET, ev)
    s3.put_object(Bucket=BUCKET, Key=f"raw/satellite/{SCENE}/metadata.json",
                  Body=json.dumps(meta(SCENE, NCOL * NROW * 9, valid_ratio=0.8)).encode())
    with pytest.raises(verify_fn.VerifyError, match="changed"):
        verify_fn.verify(s3, BUCKET, ev)


# ---------------------------------------------------------------- state machine

def test_statemachine_paths():
    import re

    text = ASL.read_text()
    names = set(re.findall(r"\$\{(\w+)\}", text))
    assert names == {"check_batch_arn", "validate_batch_arn", "quarantine_batch_arn", "write_trusted_arn",
                     "render_layers_arn", "verify_batch_arn", "alert_topic_arn"}
    for n in names:
        text = text.replace("${%s}" % n, f"arn:aws:lambda:us-east-1:123:function:{n}")
    sm = json.loads(text)
    states = sm["States"]
    for name, st in states.items():
        nxt = [st.get("Next"), st.get("Default")] + [c["Next"] for c in st.get("Choices", []) + st.get("Catch", [])]
        assert all(n in states for n in nxt if n), name
    ends = {n for n, s in states.items() if s["Type"] in ("Succeed", "Fail")}
    assert ends == {"Published", "Quarantined", "StopAlreadyProcessed", "NoData", "InvalidInput", "PipelineFailed"}
