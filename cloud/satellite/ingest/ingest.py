"""Satellite Ingest Lambda & CLI: Google Earth Engine -> S3 (issue #27, spec 50).

Fetches Landsat 8 & 9 Collection 2 Level 2 scenes over Lak Si AOI.
For each scene with cloud coverage <= MAX_CLOUD_PCT (40%):
  - bands.tif      (GeoTIFF 30m in EPSG:32647 with bands: lst_c, ndvi, ndbi, cloud_mask)
  - metadata.json  (scene ID, acquisition time, cloud %, sensor, formulas)
  - _COMPLETE      (marker indicating upload completion)

Saved to S3 under raw/satellite/scene_<YYYY-MM-DD>/
Idempotent: skips scenes if _COMPLETE already exists in S3.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
import ee
from google.auth import aws

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("satellite_ingest")

# Constants & Defaults
DEFAULT_PROJECT = "turnkey-tribute-511903-t6"
DEFAULT_ASSET_ROI = "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"
DEFAULT_BUCKET = os.environ.get("DATA_BUCKET", "iot-dt-363245343149")
CRS = "EPSG:32647"
MAX_CLOUD_PCT = 40.0
FORMULA_VERSION = "v3"
CONFIG_FILENAME = "gee-wif-config.json"
LOCAL_TZ = timezone(timedelta(hours=7), "Asia/Bangkok")

_INITIALIZED = False
_s3_client = None


def get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def init_gee(config_dir: Path | None = None) -> None:
    """Initialize GEE client using WIF credentials or ambient environment."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    # If ee was already initialized (e.g. by handler.py), don't re-initialize
    try:
        if getattr(ee.data, "_credentials", None) is not None:
            _INITIALIZED = True
            return
    except Exception:
        pass

    project = os.environ.get("GEE_PROJECT", DEFAULT_PROJECT)

    # Search for WIF config in multiple locations
    search_dirs = [
        config_dir,
        Path(__file__).parent,
        Path(__file__).parent.parent / "test_lambda",
        Path.cwd(),
    ]
    config_file = None
    for d in search_dirs:
        if d and (d / CONFIG_FILENAME).exists():
            config_file = d / CONFIG_FILENAME
            break

    if config_file:
        log.info("Authenticating GEE using WIF config: %s", config_file)
        with open(config_file, encoding="utf-8") as f:
            info = json.load(f)
        credentials = aws.Credentials.from_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/earthengine",
                "https://www.googleapis.com/auth/cloud-platform",
            ],
        )
        ee.Initialize(credentials, project=project)
        _INITIALIZED = True
    else:
        log.info("No WIF config found; initializing GEE using ambient credentials")
        ee.Initialize(project=project)
        _INITIALIZED = True

    log.info("Google Earth Engine initialized successfully (project: %s)", project)


def prep_scene(img: ee.Image) -> ee.Image:
    """Calculate LST, NDVI, NDBI, and cloud mask from Landsat C2 L2."""
    qa = img.select("QA_PIXEL")
    # Bit 1-4: dilated cloud (2), cirrus (4), cloud (8), shadow (16)
    clear = qa.bitwiseAnd(30).eq(0).rename("cloud_mask")

    # LST in Celsius
    lst = (
        img.select("ST_B10")
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
        .rename("lst_c")
    )

    # Surface reflectance & indices
    sr = img.select(["SR_B4", "SR_B5", "SR_B6"]).multiply(0.0000275).add(-0.2)
    b4 = sr.select("SR_B4")
    b5 = sr.select("SR_B5")
    b6 = sr.select("SR_B6")

    ndvi = b5.subtract(b4).divide(b5.add(b4)).rename("ndvi")
    ndbi = b6.subtract(b5).divide(b6.add(b5)).rename("ndbi")

    return lst.addBands([ndvi, ndbi, clear]).copyProperties(img, ["system:time_start", "system:index"])


def check_scene_completed(bucket: str, prefix: str) -> bool:
    """Check if _COMPLETE marker already exists in S3."""
    s3 = get_s3()
    try:
        s3.head_object(Bucket=bucket, Key=f"{prefix}_COMPLETE")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "403"):
            return False
        return False


def ingest_scene(scene: ee.Image, roi: ee.Geometry, bucket: str, dry_run: bool = False) -> dict[str, Any]:
    """Process a single Landsat scene, download products, and upload to S3."""
    scene_id = scene.get("system:index").getInfo()
    time_ms = scene.get("system:time_start").getInfo()
    dt_utc = datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc)
    dt_local = dt_utc.astimezone(LOCAL_TZ)
    date_str = dt_local.date().isoformat()

    prefix = f"raw/satellite/scene_{date_str}/"
    log.info("Processing scene: %s (date: %s, prefix: %s)", scene_id, date_str, prefix)

    if not dry_run and check_scene_completed(bucket, prefix):
        log.info("Scene %s already ingested (_COMPLETE exists). Skipping.", scene_id)
        return {"scene_id": scene_id, "date": date_str, "status": "already_exists"}

    # 1. Check cloud percentage in AOI
    qa = scene.select("QA_PIXEL")
    cloud_mask = qa.bitwiseAnd(30).neq(0)
    cloud_mean = cloud_mask.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=roi,
        scale=30,
        crs=CRS,
        maxPixels=1e7,
    ).get("QA_PIXEL").getInfo()

    cloud_pct = float(cloud_mean or 0.0) * 100.0
    log.info("Scene %s cloud coverage in AOI: %.2f%%", scene_id, cloud_pct)

    if cloud_pct > MAX_CLOUD_PCT:
        log.warning("Scene %s exceeds MAX_CLOUD_PCT (%.2f%% > %.2f%%). Skipping.", scene_id, cloud_pct, MAX_CLOUD_PCT)
        return {"scene_id": scene_id, "date": date_str, "status": "skipped_cloud", "cloud_pct": cloud_pct}

    # 2. Compute 4 bands
    processed = prep_scene(scene).clip(roi)

    # 3. Create metadata
    metadata = {
        "scene_id": scene_id,
        "date": date_str,
        "acquisition_time_utc": dt_utc.isoformat(),
        "acquisition_time_local": dt_local.isoformat(),
        "cloud_pct_aoi": round(cloud_pct, 2),
        "crs": CRS,
        "resolution_m": 30,
        "formula_version": FORMULA_VERSION,
        "ingest_timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        log.info("[DRY RUN] Would upload %s to s3://%s/%s", scene_id, bucket, prefix)
        return {"scene_id": scene_id, "date": date_str, "status": "dry_run", "metadata": metadata}

    s3 = get_s3()

    # 4. Download and upload GeoTIFF
    log.info("Requesting GeoTIFF download URL from Earth Engine...")
    tif_url = processed.select(["lst_c", "ndvi", "ndbi", "cloud_mask"]).getDownloadURL({
        "name": f"bands_{date_str}",
        "scale": 30,
        "crs": CRS,
        "region": roi,
        "format": "GEO_TIFF",
    })
    log.info("Downloading GeoTIFF from GEE...")
    with urllib.request.urlopen(tif_url) as resp:
        tif_bytes = resp.read()
    log.info("Downloaded GeoTIFF (%d bytes). Uploading to s3://%s/%sbands.tif...", len(tif_bytes), bucket, prefix)
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}bands.tif",
        Body=tif_bytes,
        ContentType="image/tiff",
    )

    # 5. Upload metadata.json
    log.info("Uploading metadata.json...")
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

    # 6. Upload _COMPLETE marker
    log.info("Writing _COMPLETE marker...")
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}_COMPLETE",
        Body=b"",
    )

    log.info("Successfully ingested scene %s into s3://%s/%s", scene_id, bucket, prefix)
    return {"scene_id": scene_id, "date": date_str, "status": "ingested", "cloud_pct": cloud_pct}


def run_ingest(
    date_start: str,
    date_end: str,
    bucket: str = DEFAULT_BUCKET,
    asset_roi: str = DEFAULT_ASSET_ROI,
    summer_only: bool = True,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Find and ingest scenes within the date range."""
    init_gee()
    roi = ee.FeatureCollection(asset_roi).geometry()

    log.info("Searching Landsat 8/9 scenes from %s to %s (summer_only=%s)...", date_start, date_end, summer_only)
    col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
        .filterBounds(roi)
        .filterDate(date_start, date_end)
    )

    if summer_only:
        col = col.filter(ee.Filter.calendarRange(3, 5, "month"))

    scenes = col.sort("system:time_start").toList(100)
    count = scenes.size().getInfo()
    log.info("Found %d candidate scenes", count)

    results = []
    for i in range(count):
        scene = ee.Image(scenes.get(i))
        res = ingest_scene(scene, roi, bucket, dry_run=dry_run)
        results.append(res)

    return results


def lambda_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """AWS Lambda entry point for scheduled or manual backfill."""
    event = event or {}
    bucket = event.get("bucket", os.environ.get("DATA_BUCKET", DEFAULT_BUCKET))
    summer_only = event.get("summer_only", True)
    dry_run = event.get("dry_run", False)

    # If date range given in event, use it (backfill)
    # Otherwise, default to the last 16 days (daily check)
    if "date_start" in event and "date_end" in event:
        d_start = event["date_start"]
        d_end = event["date_end"]
    else:
        today = date.today()
        d_start = (today - timedelta(days=16)).isoformat()
        d_end = (today + timedelta(days=1)).isoformat()

    try:
        results = run_ingest(
            date_start=d_start,
            date_end=d_end,
            bucket=bucket,
            summer_only=summer_only,
            dry_run=dry_run,
        )
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "success", "processed_scenes": results}),
        }
    except Exception as exc:
        log.exception("Error in satellite ingest: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "message": str(exc)}),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Landsat scenes over Lak Si into S3")
    parser.add_argument("--start", default="2024-03-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-05-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Target S3 bucket")
    parser.add_argument("--all-months", action="store_true", help="Include all months, not just Mar-May")
    parser.add_argument("--dry-run", action="store_true", help="Check and print without uploading")
    args = parser.parse_args()

    res = run_ingest(
        date_start=args.start,
        date_end=args.end,
        bucket=args.bucket,
        summer_only=not args.all_months,
        dry_run=args.dry_run,
    )
    print(json.dumps(res, indent=2))
