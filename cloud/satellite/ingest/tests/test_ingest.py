import csv
import io
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

import handler
import ingest

BUCKET = "iot-dt-test"
NODATA = ingest.NODATA_VALUE


# ---------------------------------------------------------------- grid / pixels

def test_origin_matches_central_grid():
    """ingest.py copies ORIGIN_X/Y from cloud/pipeline/common/grid.py; they must agree (D11)."""
    grid_py = Path(__file__).resolve().parents[3] / "pipeline" / "common" / "grid.py"
    src = grid_py.read_text(encoding="utf-8")
    ns = {}
    for line in src.splitlines():
        if line.startswith(("ORIGIN_X", "ORIGIN_Y", "LANDSAT_PIXEL_M")):
            exec(line, ns)
    assert (ns["ORIGIN_X"], ns["ORIGIN_Y"]) == (ingest.ORIGIN_X, ingest.ORIGIN_Y)
    assert ns["LANDSAT_PIXEL_M"] == ingest.SCALE
    assert ingest.CRS_TRANSFORM == [30, 0, ingest.ORIGIN_X, 0, -30, ingest.ORIGIN_Y]


@pytest.mark.parametrize("x, y", [
    (660015.0 + 30 * 7, 1545015.0 - 30 * 4),          # upper-left corner of pixel (7, 4)
    (660015.0 + 30 * 7 + 15, 1545015.0 - 30 * 4 - 15),  # its centre
    (660015.0 + 30 * 7 - 1e-9, 1545015.0 - 30 * 4 + 1e-9),  # corner with float noise
])
def test_pixel_center_snaps_to_landsat_lattice(x, y):
    assert ingest.pixel_center(x, y) == (660015.0 + 30 * 7 + 15, 1545015.0 - 30 * 4 - 15)


def test_pixel_center_lands_on_15_m_offsets():
    # pixel edges sit at ...15/...45 m, so centres sit on multiples of 30 m
    cx, cy = ingest.pixel_center(668_123.4, 1_533_456.7)
    assert (cx, cy) == (668_130.0, 1_533_450.0)
    assert abs(cx - 668_123.4) <= 15 and abs(cy - 1_533_456.7) <= 15


def test_pixels_to_csv_columns_and_counts():
    reduced = {
        "x": [660015.0, 660045.0, NODATA],
        "y": [1545015.0, 1545015.0, NODATA],
        "longitude": [100.5, 100.5003, NODATA],
        "latitude": [13.9, 13.9, NODATA],
        "lst_c": [35.123456, NODATA, NODATA],
        "ndvi": [0.2, NODATA, NODATA],
        "ndbi": [-0.1, NODATA, NODATA],
        "cloud_mask": [1.0, 0.0, NODATA],
    }
    text, total, valid = ingest.pixels_to_csv(reduced, "cloud_mask")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0].keys()) == ["x", "y", "lon", "lat", "lst_c", "ndvi", "ndbi", "cloud_mask"]
    assert (total, valid) == (2, 1)                      # the NODATA-coordinate row is outside the AOI
    assert (float(rows[0]["x"]), float(rows[0]["y"])) == (660030.0, 1545000.0)  # pixel centre
    assert float(rows[0]["lst_c"]) == 35.1235
    assert float(rows[1]["lst_c"]) == NODATA


def test_pixels_to_csv_composite_counts_n_valid():
    reduced = {
        "x": [660015.0, 660045.0], "y": [1545015.0] * 2,
        "longitude": [100.5] * 2, "latitude": [13.9] * 2,
        "lst_c": [33.0, NODATA], "ndvi": [0.1, NODATA], "ndbi": [0.0, NODATA],
        "n_valid": [4.0, 0.0],
    }
    text, total, valid = ingest.pixels_to_csv(reduced, "n_valid")
    assert text.splitlines()[0].endswith(",n_valid")
    assert (total, valid) == (2, 1)


# ---------------------------------------------------------------- season helpers

def test_season_years_only_counts_march_to_may():
    assert ingest.season_years(["2024-02-28", "2024-03-01", "2024-05-31", "2025-04-10", "2025-06-01"]) == [2024, 2025]
    assert ingest.season_years([]) == []


def test_composite_is_current():
    assert not ingest.composite_is_current(None, ["2024-03-01"])
    meta = {"distinct_dates": ["2024-03-01", "2024-03-17"]}
    assert ingest.composite_is_current(meta, ["2024-03-17", "2024-03-01"])
    assert not ingest.composite_is_current(meta, ["2024-03-01", "2024-03-17", "2024-04-02"])


def test_season_period_matches_satellite_schema():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pipeline"))
    from common import satellite_schema
    assert ingest.season_period(2026) == satellite_schema.season_period(2026) == "season_2026_03-05"
    p = satellite_schema.parse_period(ingest.season_period(2024))
    assert p.date_end.isoformat() == "2024-05-31"


# ---------------------------------------------------------------- S3

class FakeS3:
    """In-memory stand-in for the few S3 calls ingest.py makes (no moto: its PyYAML
    dependency has no wheel for every Python version, e.g. 3.15 on Windows)."""

    def __init__(self):
        self.objects = {}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def put_object(self, Bucket, Key, Body=b"", **_):
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.encode()
        return {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, Bucket, Prefix=""):
                keys = sorted(k for b, k in fake.objects if b == Bucket and k.startswith(Prefix))
                yield {"Contents": [{"Key": k} for k in keys]}

        return Paginator()


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", BUCKET)
    client = FakeS3()
    monkeypatch.setattr(ingest.boto3, "client", lambda *a, **k: client)
    return client


def test_s3_key_exists(s3):
    assert ingest._s3_key_exists(s3, BUCKET, "nope") is False
    s3.put_object(Bucket=BUCKET, Key="yes", Body=b"")
    assert ingest._s3_key_exists(s3, BUCKET, "yes") is True


def test_s3_key_exists_raises_on_access_denied():
    class Denied:
        def head_object(self, **_):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    with pytest.raises(ClientError):
        ingest._s3_key_exists(Denied(), BUCKET, "k")


def _put_json(s3, key, obj):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj).encode())


def test_update_catalog(s3):
    for d, cloud in (("2024-03-17", 12.5), ("2024-03-25", 3.0), ("2024-04-02", 30.0)):
        _put_json(s3, f"raw/satellite/scene_{d}/metadata.json", {
            "scene_date": d, "month": d[:7], "cloud_pct": cloud, "valid_ratio": 0.9,
            "valid_pixels": 100, "sensor": "LANDSAT_9",
        })
    _put_json(s3, "raw/satellite/season_2024_03-05/metadata.json", {
        "period": "season_2024_03-05", "year": 2024, "valid_ratio": 0.99,
        "distinct_dates": ["2024-03-17", "2024-03-25", "2024-04-02"],
    })
    _put_json(s3, "raw/satellite/skipped/2024-07-01.json", {
        "scene_date": "2024-07-01", "month": "2024-07", "details": {"cloud_pct": 88.0},
    })
    _put_json(s3, "raw/satellite/skipped/2024-03-09.json", {
        "scene_date": "2024-03-09", "month": "2024-03", "details": {"cloud_pct": 95.0},
    })

    out = ingest.update_catalog()
    assert out["total_valid_scenes"] == 3 and out["total_seasons"] == 1

    cat = json.loads(s3.get_object(Bucket=BUCKET, Key="curated/satellite/catalog.json")["Body"].read())
    assert cat["latest_valid_scene"]["scene_date"] == "2024-04-02"
    march = cat["months"]["2024-03"]
    assert march["status"] == "available"
    assert (march["scene_count"], march["best_scene_date"]) == (2, "2024-03-25")
    assert cat["months"]["2024-07"] == {
        "status": "no_valid_data", "reason": "all_scenes_cloudy",
        "skipped_count": 1, "min_cloud_pct_seen": 88.0,
    }
    assert cat["historical_seasons"][0]["s3_prefix"] == "raw/satellite/season_2024_03-05/"


# ---------------------------------------------------------------- handler

def _scene(d, status):
    return {"scene_date": d, "status": status}


@pytest.fixture
def stub_calls(monkeypatch):
    calls = {"composite": [], "catalog": 0}

    def fake_composite(year, dry_run=False, force=False):
        calls["composite"].append(year)
        return {"status": "success", "mode": "composite", "year": year}

    def fake_catalog():
        calls["catalog"] += 1
        return {"status": "success"}

    monkeypatch.setattr(handler, "build_seasonal_composite", fake_composite)
    monkeypatch.setattr(handler, "update_catalog", fake_catalog)
    return calls


def _fake_ingest(scenes, errors=0, seen=None):
    def run(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        return {"status": "error" if errors else "success", "errors": errors, "processed_scenes": scenes}
    return run


def test_handler_rebuilds_composite_only_for_new_in_season_dates(monkeypatch, stub_calls):
    monkeypatch.setattr(handler, "run_ingest", _fake_ingest([
        _scene("2024-03-17", "already_exists"),     # done before -> no rebuild for this alone
        _scene("2025-04-02", "ingested"),
        _scene("2025-04-18", "skipped_cloudy"),     # cloudy dates still feed the median
        _scene("2025-07-01", "ingested"),           # outside Mar-May
    ]))
    out = handler.lambda_handler({"mode": "ingest"}, None)
    assert stub_calls["composite"] == [2025]
    assert stub_calls["catalog"] == 1
    assert out["composites"][0]["year"] == 2025


def test_handler_uses_env_thresholds(monkeypatch, stub_calls):
    seen = {}
    monkeypatch.setenv("MAX_CLOUD_PCT", "55")
    monkeypatch.delenv("MIN_VALID_RATIO", raising=False)
    monkeypatch.setattr(handler, "run_ingest", _fake_ingest([], seen=seen))
    handler.lambda_handler({}, None)
    assert seen["max_cloud_pct"] == 55.0
    assert seen["min_valid_ratio"] == ingest.DEFAULT_MIN_VALID_RATIO == 0.15
    assert ingest.DEFAULT_MAX_CLOUD_PCT == 40.0


def test_handler_raises_when_a_scene_fails(monkeypatch, stub_calls):
    monkeypatch.setattr(handler, "run_ingest", _fake_ingest(
        [_scene("2025-08-01", "ingested"), {"scene_date": "2025-08-17", "status": "error", "error": "boom"}],
        errors=1,
    ))
    with pytest.raises(handler.IngestError, match="boom"):
        handler.lambda_handler({"mode": "ingest"}, None)
    assert stub_calls["catalog"] == 1  # catalog still written for what succeeded


def test_handler_raises_when_composite_fails(monkeypatch, stub_calls):
    monkeypatch.setattr(handler, "run_ingest", _fake_ingest([_scene("2025-03-05", "ingested")]))

    def boom(year, dry_run=False, force=False):
        raise RuntimeError("gee quota")

    monkeypatch.setattr(handler, "build_seasonal_composite", boom)
    with pytest.raises(handler.IngestError, match="gee quota"):
        handler.lambda_handler({"mode": "ingest"}, None)


def test_handler_unknown_mode():
    with pytest.raises(ValueError):
        handler.lambda_handler({"mode": "nope"}, None)
