import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3
import ee
import requests
from botocore.exceptions import ClientError
from google.auth import aws

# ค่าคงที่มาตรฐานอ้างอิงจาก spike26, grid.py และ satellite_schema.py
CRS = "EPSG:32647"
SCALE = 30
ORIGIN_X = 660015.0
ORIGIN_Y = 1545015.0
CRS_TRANSFORM = [SCALE, 0, ORIGIN_X, 0, -SCALE, ORIGIN_Y]
NODATA_VALUE = -9999.0
SEASON_MONTHS = (3, 5)

_EE_INITIALIZED = False


def init_ee() -> None:
    """เชื่อมต่อ GEE ผ่าน Workload Identity Federation (รันครั้งเดียวตอน Cold Start)"""
    global _EE_INITIALIZED
    if _EE_INITIALIZED:
        return

    config_path = os.path.join(os.path.dirname(__file__), "gee-wif-config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    credentials = aws.Credentials.from_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/earthengine",
            "https://www.googleapis.com/auth/cloud-platform",
        ],
    )
    project_id = os.environ.get("GEE_PROJECT", "turnkey-tribute-511903-t6")
    ee.Initialize(credentials, project=project_id)
    _EE_INITIALIZED = True


def _s3_key_exists(s3_client: Any, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def _prep_clear_indices(img: ee.Image) -> ee.Image:
    """คำนวณ LST, NDVI, NDBI เฉพาะพิกเซลที่ไม่มีเมฆ (QA_PIXEL bitwiseAnd(30) == 0)"""
    qa = img.select("QA_PIXEL")
    clear = qa.bitwiseAnd(30).eq(0)

    lst = (
        img.select("ST_B10")
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
        .updateMask(clear)
        .rename("lst_c")
    )

    sr = img.select(["SR_B4", "SR_B5", "SR_B6"]).multiply(0.0000275).add(-0.2)
    ndvi = (
        sr.select("SR_B5")
        .subtract(sr.select("SR_B4"))
        .divide(sr.select("SR_B5").add(sr.select("SR_B4")))
        .updateMask(clear)
        .rename("ndvi")
    )
    ndbi = (
        sr.select("SR_B6")
        .subtract(sr.select("SR_B5"))
        .divide(sr.select("SR_B6").add(sr.select("SR_B5")))
        .updateMask(clear)
        .rename("ndbi")
    )

    return lst.addBands([ndvi, ndbi])


def _build_stacked_image(mosaic_img: ee.Image, roi: ee.Geometry) -> ee.Image:
    """สร้างชั้นข้อมูล 4 แบนด์สำหรับรายฉาก (lst_c, ndvi, ndbi, cloud_mask)"""
    clear = mosaic_img.select("QA_PIXEL").bitwiseAnd(30).eq(0)
    indices = _prep_clear_indices(mosaic_img)
    cloud_mask = clear.toFloat().rename("cloud_mask")

    return (
        indices.addBands(cloud_mask)
        .setDefaultProjection(crs=CRS, crsTransform=CRS_TRANSFORM)
        .clip(roi)
        .unmask(NODATA_VALUE)
    )


def _generate_pixels_csv(
    stack: ee.Image, roi: ee.Geometry, extra_col: str = "cloud_mask"
) -> tuple[str, int, int]:
    """สกัดค่าพิกเซลทั้งหมดใน ROI ออกมาเป็น CSV พร้อมนับจำนวนพิกเซล"""
    coords = ee.Image.pixelCoordinates(CRS).clip(roi).unmask(NODATA_VALUE)
    combined = coords.addBands(stack)

    reduced = combined.reduceRegion(
        reducer=ee.Reducer.toList(),
        geometry=roi,
        scale=SCALE,
        crs=CRS,
        maxPixels=1e7,
    ).getInfo()

    xs = reduced.get("x", [])
    ys = reduced.get("y", [])
    lsts = reduced.get("lst_c", [])
    ndvis = reduced.get("ndvi", [])
    ndbis = reduced.get("ndbi", [])
    extras = reduced.get(extra_col, [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["x", "y", "lst_c", "ndvi", "ndbi", extra_col])

    total_pixels = 0
    valid_pixels = 0

    for x, y, lst_v, ndvi_v, ndbi_v, ext_v in zip(xs, ys, lsts, ndvis, ndbis, extras):
        if x == NODATA_VALUE or y == NODATA_VALUE:
            continue

        total_pixels += 1
        if extra_col == "cloud_mask":
            is_valid = ext_v == 1.0 and lst_v != NODATA_VALUE
        else:
            is_valid = ext_v > 0 and lst_v != NODATA_VALUE

        if is_valid:
            valid_pixels += 1

        writer.writerow([
            round(float(x), 2),
            round(float(y), 2),
            round(float(lst_v), 4) if lst_v != NODATA_VALUE else NODATA_VALUE,
            round(float(ndvi_v), 4) if ndvi_v != NODATA_VALUE else NODATA_VALUE,
            round(float(ndbi_v), 4) if ndbi_v != NODATA_VALUE else NODATA_VALUE,
            int(ext_v) if ext_v != NODATA_VALUE else int(NODATA_VALUE),
        ])

    return output.getvalue(), total_pixels, valid_pixels


def _upload_bundle(
    s3_client: Any,
    bucket: str,
    prefix: str,
    stack: ee.Image,
    roi: ee.Geometry,
    tif_name: str,
    csv_text: str,
    metadata: Dict[str, Any],
) -> None:
    """ดาวน์โหลด GeoTIFF จาก GEE และอัปโหลดครบชุด (bands.tif, pixels.csv, metadata.json, _COMPLETE)"""
    tif_url = stack.getDownloadURL({
        "name": tif_name,
        "scale": SCALE,
        "crs": CRS,
        "region": roi,
        "format": "GEO_TIFF",
    })
    resp = requests.get(tif_url, timeout=180)
    resp.raise_for_status()

    s3_client.put_object(
        Bucket=bucket,
        Key=f"{prefix}/bands.tif",
        Body=resp.content,
        ContentType="image/tiff",
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=f"{prefix}/pixels.csv",
        Body=csv_text.encode("utf-8"),
        ContentType="text/csv",
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=f"{prefix}/metadata.json",
        Body=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=f"{prefix}/_COMPLETE",
        Body=b"",
        ContentType="text/plain",
    )


def _write_skip_record(
    s3_client: Any,
    bucket: str,
    scene_date: str,
    reason: str,
    details: Dict[str, Any],
) -> None:
    payload = {
        "scene_date": scene_date,
        "month": scene_date[:7],
        "status": "skipped",
        "reason": reason,
        "details": details,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    s3_client.put_object(
        Bucket=bucket,
        Key=f"raw/satellite/skipped/{scene_date}.json",
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def run_ingest(
    start_date: str,
    end_date: str,
    dry_run: bool = False,
    force: bool = False,
    max_cloud_pct: float = 75.0,
    min_valid_ratio: float = 0.15,
) -> Dict[str, Any]:
    """ดึงข้อมูล Landsat 8/9 รายฉาก (รวมภาพวันเดียวกัน) และบันทึกลง S3"""
    init_ee()

    bucket = os.environ.get("DATA_BUCKET")
    if not bucket and not dry_run:
        raise ValueError("Missing DATA_BUCKET environment variable")

    asset_path = os.environ.get(
        "GEE_ASSETS", "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"
    )
    roi = ee.FeatureCollection(asset_path).geometry()
    s3 = boto3.client("s3") if not dry_run else None

    col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
        .filterBounds(roi)
        .filterDate(start_date, end_date)
    )

    def _extract_meta(img: ee.Image) -> ee.Feature:
        return ee.Feature(
            None,
            {
                "scene_id": img.get("system:index"),
                "date_str": ee.Date(img.get("system:time_start")).format("YYYY-MM-dd"),
                "spacecraft": img.get("SPACECRAFT_ID"),
            },
        )

    meta_list = col.map(_extract_meta).getInfo().get("features", [])

    scenes_by_date: Dict[str, List[Dict[str, str]]] = {}
    for feat in meta_list:
        props = feat["properties"]
        scenes_by_date.setdefault(props["date_str"], []).append(props)

    results: List[Dict[str, Any]] = []

    for scene_date in sorted(scenes_by_date.keys()):
        day_items = scenes_by_date[scene_date]
        scene_ids = [item["scene_id"] for item in day_items]
        sensors = sorted({item.get("spacecraft", "UNKNOWN") for item in day_items})
        sensor_label = "+".join(sensors)

        prefix = f"raw/satellite/scene_{scene_date}"
        complete_key = f"{prefix}/_COMPLETE"

        try:
            if not dry_run and not force and _s3_key_exists(s3, bucket, complete_key):
                results.append({
                    "scene_date": scene_date,
                    "status": "already_exists",
                    "scene_ids": scene_ids,
                })
                continue

            next_day = ee.Date(scene_date).advance(1, "day")
            mosaic_img = col.filterDate(scene_date, next_day).mosaic()

            cloud_img = mosaic_img.select("QA_PIXEL").bitwiseAnd(30).neq(0)
            cloud_stat = cloud_img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=roi,
                scale=SCALE,
                crs=CRS,
                maxPixels=1e7,
            ).getInfo()

            cloud_val = cloud_stat.get("QA_PIXEL")
            cloud_pct = round(float(cloud_val) * 100.0, 2) if cloud_val is not None else 100.0

            if cloud_pct > max_cloud_pct:
                reason = f"cloud_pct ({cloud_pct}%) exceeds max_cloud_pct ({max_cloud_pct}%)"
                if not dry_run:
                    _write_skip_record(
                        s3, bucket, scene_date, reason,
                        {"cloud_pct": cloud_pct, "scene_ids": scene_ids}
                    )
                results.append({
                    "scene_date": scene_date,
                    "status": "skipped_cloudy",
                    "cloud_pct": cloud_pct,
                    "scene_ids": scene_ids,
                })
                continue

            stack = _build_stacked_image(mosaic_img, roi)
            csv_text, total_pixels, valid_pixels = _generate_pixels_csv(
                stack, roi, extra_col="cloud_mask"
            )
            valid_ratio = round(valid_pixels / total_pixels, 4) if total_pixels > 0 else 0.0

            if valid_ratio < min_valid_ratio:
                reason = f"valid_ratio ({valid_ratio}) below min_valid_ratio ({min_valid_ratio})"
                if not dry_run:
                    _write_skip_record(
                        s3, bucket, scene_date, reason,
                        {"valid_ratio": valid_ratio, "cloud_pct": cloud_pct, "scene_ids": scene_ids}
                    )
                results.append({
                    "scene_date": scene_date,
                    "status": "skipped_low_valid_pixels",
                    "valid_ratio": valid_ratio,
                    "cloud_pct": cloud_pct,
                    "scene_ids": scene_ids,
                })
                continue

            metadata = {
                "period": f"scene_{scene_date}",
                "period_type": "scene",
                "scene_date": scene_date,
                "month": scene_date[:7],
                "source_type": "satellite_remote_sensing",
                "sensor": sensor_label,
                "scene_ids": scene_ids,
                "is_mosaic": len(scene_ids) > 1,
                "cloud_pct": cloud_pct,
                "valid_ratio": valid_ratio,
                "total_pixels": total_pixels,
                "valid_pixels": valid_pixels,
                "crs": CRS,
                "scale": SCALE,
                "nodata": NODATA_VALUE,
                "bands": ["lst_c", "ndvi", "ndbi", "cloud_mask"],
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            if dry_run:
                results.append({
                    "scene_date": scene_date,
                    "status": "dry_run_ready",
                    "metadata": metadata,
                })
                continue

            _upload_bundle(
                s3, bucket, prefix, stack, roi,
                f"scene_{scene_date}_bands", csv_text, metadata
            )

            results.append({
                "scene_date": scene_date,
                "status": "ingested",
                "cloud_pct": cloud_pct,
                "valid_ratio": valid_ratio,
                "valid_pixels": valid_pixels,
                "is_mosaic": len(scene_ids) > 1,
            })

        except Exception as exc:
            results.append({
                "scene_date": scene_date,
                "status": "error",
                "error": str(exc),
                "scene_ids": scene_ids,
            })

    return {
        "status": "success",
        "mode": "ingest",
        "start_date": start_date,
        "end_date": end_date,
        "dry_run": dry_run,
        "total_dates_found": len(scenes_by_date),
        "processed_scenes": results,
    }


def build_seasonal_composite(
    year: int, dry_run: bool = False, force: bool = False
) -> Dict[str, Any]:
    """สร้าง Seasonal Composite หน้าร้อน (เดือน 3-5) พร้อมนับจำนวนพิกเซลดีรายจุด (n_valid)"""
    init_ee()

    bucket = os.environ.get("DATA_BUCKET")
    if not bucket and not dry_run:
        raise ValueError("Missing DATA_BUCKET environment variable")

    asset_path = os.environ.get(
        "GEE_ASSETS", "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"
    )
    roi = ee.FeatureCollection(asset_path).geometry()
    s3 = boto3.client("s3") if not dry_run else None

    m0, m1 = SEASON_MONTHS
    period = f"season_{year}_{m0:02d}-{m1:02d}"
    prefix = f"raw/satellite/{period}"

    if not dry_run and not force and _s3_key_exists(s3, bucket, f"{prefix}/_COMPLETE"):
        return {"status": "success", "mode": "composite", "period": period, "result": "already_exists"}

    start_str = f"{year}-{m0:02d}-01"
    end_str = f"{year}-{m1 + 1:02d}-01"

    col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
        .filterBounds(roi)
        .filterDate(start_str, end_str)
    )

    scene_count = int(col.size().getInfo())
    if scene_count == 0:
        return {
            "status": "success",
            "mode": "composite",
            "period": period,
            "result": "no_scenes_found",
            "scene_count": 0,
        }

    # ดึงรายการวันที่และดาวเทียมทั้งหมดในช่วงหน้าร้อนของปีนั้น
    meta_feats = col.map(
        lambda img: ee.Feature(
            None,
            {
                "date_str": ee.Date(img.get("system:time_start")).format("YYYY-MM-dd"),
                "spacecraft": img.get("SPACECRAFT_ID"),
            },
        )
    ).getInfo().get("features", [])

    distinct_dates = sorted({f["properties"]["date_str"] for f in meta_feats})
    sensors = sorted({f["properties"].get("spacecraft", "UNKNOWN") for f in meta_feats})

    prepped = col.map(_prep_clear_indices)
    median_img = prepped.median()
    n_valid_img = prepped.select("lst_c").count().toFloat().rename("n_valid")

    stack = (
        median_img.addBands(n_valid_img)
        .setDefaultProjection(crs=CRS, crsTransform=CRS_TRANSFORM)
        .clip(roi)
        .unmask(NODATA_VALUE)
    )

    csv_text, total_pixels, valid_pixels = _generate_pixels_csv(
        stack, roi, extra_col="n_valid"
    )
    valid_ratio = round(valid_pixels / total_pixels, 4) if total_pixels > 0 else 0.0

    metadata = {
        "period": period,
        "period_type": "season",
        "year": year,
        "date_start": f"{year}-{m0:02d}-01",
        "date_end": f"{year}-{m1:02d}-31",
        "source_type": "satellite_seasonal_composite",
        "reducer": "median",
        "sensor": "+".join(sensors),
        "raw_scene_count": scene_count,
        "distinct_dates": distinct_dates,
        "valid_ratio": valid_ratio,
        "total_pixels": total_pixels,
        "valid_pixels": valid_pixels,
        "crs": CRS,
        "scale": SCALE,
        "nodata": NODATA_VALUE,
        "bands": ["lst_c", "ndvi", "ndbi", "n_valid"],
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        return {
            "status": "success",
            "mode": "composite",
            "period": period,
            "result": "dry_run_ready",
            "metadata": metadata,
        }

    _upload_bundle(s3, bucket, prefix, stack, roi, f"{period}_bands", csv_text, metadata)

    return {
        "status": "success",
        "mode": "composite",
        "period": period,
        "result": "created",
        "distinct_dates": len(distinct_dates),
        "valid_ratio": valid_ratio,
        "valid_pixels": valid_pixels,
    }


def update_catalog() -> Dict[str, Any]:
    """อัปเดตไฟล์สารบัญกลาง curated/satellite/catalog.json สำหรับหน้าเว็บและ API"""
    bucket = os.environ.get("DATA_BUCKET")
    if not bucket:
        raise ValueError("Missing DATA_BUCKET environment variable")

    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    scene_meta_keys: List[str] = []
    season_meta_keys: List[str] = []
    skipped_keys: List[str] = []

    for page in paginator.paginate(Bucket=bucket, Prefix="raw/satellite/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.startswith("raw/satellite/scene_") and key.endswith("/metadata.json"):
                scene_meta_keys.append(key)
            elif key.startswith("raw/satellite/season_") and key.endswith("/metadata.json"):
                season_meta_keys.append(key)
            elif key.startswith("raw/satellite/skipped/") and key.endswith(".json"):
                skipped_keys.append(key)

    scenes: List[Dict[str, Any]] = []
    for key in sorted(scene_meta_keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        meta = json.loads(body)
        if meta.get("valid_pixels", 0) > 0:
            scenes.append(meta)

    seasons: List[Dict[str, Any]] = []
    for key in sorted(season_meta_keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        meta = json.loads(body)
        seasons.append({
            "period": meta["period"],
            "year": meta["year"],
            "label": f"หน้าร้อน {meta['year']} (มี.ค.-พ.ค.)",
            "s3_prefix": f"raw/satellite/{meta['period']}/",
            "valid_ratio": meta.get("valid_ratio"),
            "distinct_dates": len(meta.get("distinct_dates", [])),
        })

    skipped_records: List[Dict[str, Any]] = []
    for key in sorted(skipped_keys):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        skipped_records.append(json.loads(body))

    # สรุปสถานะรายเดือน (YYYY-MM)
    months: Dict[str, Dict[str, Any]] = {}
    for s in scenes:
        m = s["month"]
        entry = months.setdefault(
            m,
            {
                "status": "available",
                "scene_count": 0,
                "best_scene_date": s["scene_date"],
                "best_cloud_pct": s["cloud_pct"],
                "latest_scene_date": s["scene_date"],
                "scenes": [],
            },
        )
        entry["scene_count"] += 1
        entry["latest_scene_date"] = max(entry["latest_scene_date"], s["scene_date"])
        if s["cloud_pct"] < entry["best_cloud_pct"]:
            entry["best_cloud_pct"] = s["cloud_pct"]
            entry["best_scene_date"] = s["scene_date"]
        entry["scenes"].append({
            "scene_date": s["scene_date"],
            "s3_prefix": f"raw/satellite/scene_{s['scene_date']}/",
            "cloud_pct": s["cloud_pct"],
            "valid_ratio": s["valid_ratio"],
            "sensor": s["sensor"],
        })

    for sk in skipped_records:
        m = sk["month"]
        if m not in months:
            months[m] = {
                "status": "no_valid_data",
                "reason": "all_scenes_cloudy",
                "skipped_count": 0,
                "min_cloud_pct_seen": 100.0,
            }
        if months[m]["status"] == "no_valid_data":
            months[m]["skipped_count"] += 1
            c_pct = sk.get("details", {}).get("cloud_pct", 100.0)
            months[m]["min_cloud_pct_seen"] = min(months[m]["min_cloud_pct_seen"], c_pct)

    latest_valid = None
    if scenes:
        last_s = max(scenes, key=lambda x: x["scene_date"])
        latest_valid = {
            "scene_date": last_s["scene_date"],
            "month": last_s["month"],
            "s3_prefix": f"raw/satellite/scene_{last_s['scene_date']}/",
            "cloud_pct": last_s["cloud_pct"],
            "valid_ratio": last_s["valid_ratio"],
            "sensor": last_s["sensor"],
        }

    catalog = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "latest_valid_scene": latest_valid,
        "historical_seasons": seasons,
        "months": dict(sorted(months.items())),
    }

    s3.put_object(
        Bucket=bucket,
        Key="curated/satellite/catalog.json",
        Body=json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    return {
        "status": "success",
        "mode": "catalog",
        "total_valid_scenes": len(scenes),
        "total_seasons": len(seasons),
        "total_months_indexed": len(months),
        "latest_valid_scene": latest_valid,
    }