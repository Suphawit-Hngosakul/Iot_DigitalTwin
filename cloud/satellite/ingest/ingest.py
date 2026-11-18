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

# ค่าคงที่มาตรฐานอ้างอิงจาก spike26 และ grid.py
CRS = "EPSG:32647"
SCALE = 30
ORIGIN_X = 660015.0
ORIGIN_Y = 1545015.0
CRS_TRANSFORM = [SCALE, 0, ORIGIN_X, 0, -SCALE, ORIGIN_Y]
NODATA_VALUE = -9999.0

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


def _build_stacked_image(mosaic_img: ee.Image, roi: ee.Geometry) -> ee.Image:
    """คำนวณ LST, NDVI, NDBI และ Cloud Mask ตามสูตรมาตรฐาน spike26"""
    qa = mosaic_img.select("QA_PIXEL")
    clear = qa.bitwiseAnd(30).eq(0)

    # 1. Land Surface Temperature (°C)
    lst = (
        mosaic_img.select("ST_B10")
        .multiply(0.00341802)
        .add(149.0)
        .subtract(273.15)
        .updateMask(clear)
        .rename("lst_c")
    )

    # 2. Surface Reflectance -> NDVI & NDBI
    sr = mosaic_img.select(["SR_B4", "SR_B5", "SR_B6"]).multiply(0.0000275).add(-0.2)
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

    # 3. Cloud Mask (1 = ท้องฟ้าเปิด, 0 = มีเมฆ/เงาเมฆ)
    cloud_mask = clear.toFloat().rename("cloud_mask")

    # รวมแบนด์, ล็อก Grid 30m, ตัดตามขอบเขตหลักสี่ และแทนค่าพิกเซลว่างด้วย -9999
    stack = (
        lst.addBands([ndvi, ndbi, cloud_mask])
        .setDefaultProjection(crs=CRS, crsTransform=CRS_TRANSFORM)
        .clip(roi)
        .unmask(NODATA_VALUE)
    )
    return stack


def _generate_pixels_csv(stack: ee.Image, roi: ee.Geometry) -> tuple[str, int, int]:
    """สกัดค่าพิกเซลทั้งหมดใน ROI ออกมาเป็นข้อความ CSV พร้อมนับจำนวนพิกเซล"""
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
    masks = reduced.get("cloud_mask", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["x", "y", "lst_c", "ndvi", "ndbi", "cloud_mask"])

    total_pixels = 0
    valid_pixels = 0

    for x, y, lst_v, ndvi_v, ndbi_v, mask_v in zip(xs, ys, lsts, ndvis, ndbis, masks):
        if x == NODATA_VALUE or y == NODATA_VALUE:
            continue

        total_pixels += 1
        is_valid = mask_v == 1.0 and lst_v != NODATA_VALUE
        if is_valid:
            valid_pixels += 1

        writer.writerow([
            round(float(x), 2),
            round(float(y), 2),
            round(float(lst_v), 4) if lst_v != NODATA_VALUE else NODATA_VALUE,
            round(float(ndvi_v), 4) if ndvi_v != NODATA_VALUE else NODATA_VALUE,
            round(float(ndbi_v), 4) if ndbi_v != NODATA_VALUE else NODATA_VALUE,
            int(mask_v) if mask_v != NODATA_VALUE else int(NODATA_VALUE),
        ])

    return output.getvalue(), total_pixels, valid_pixels


def _write_skip_record(
    s3_client: Any,
    bucket: str,
    scene_date: str,
    reason: str,
    details: Dict[str, Any],
) -> None:
    """บันทึกเหตุผลการข้ามฉากลง S3 เพื่อใช้ตรวจสอบย้อนหลัง"""
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

    # รวมคอลเลกชัน Landsat 8 และ 9 ทุกเดือนในช่วงวันที่ที่กำหนด
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

    # จัดกลุ่มฉากตามวันที่ (YYYY-MM-dd) เพื่อทำ Mosaic กรณีมีภาพวันเดียวกัน
    scenes_by_date: Dict[str, List[Dict[str, str]]] = {}
    for feat in meta_list:
        props = feat["properties"]
        d_str = props["date_str"]
        scenes_by_date.setdefault(d_str, []).append(props)

    results: List[Dict[str, Any]] = []

    for scene_date in sorted(scenes_by_date.keys()):
        day_items = scenes_by_date[scene_date]
        scene_ids = [item["scene_id"] for item in day_items]
        sensors = sorted({item.get("spacecraft", "UNKNOWN") for item in day_items})
        sensor_label = "+".join(sensors)

        prefix = f"raw/satellite/scene_{scene_date}"
        meta_key = f"{prefix}/metadata.json"

        try:
            if not dry_run and not force and _s3_key_exists(s3, bucket, meta_key):
                results.append({
                    "scene_date": scene_date,
                    "status": "already_exists",
                    "scene_ids": scene_ids,
                })
                continue

            # รวมภาพทั้งหมดในวันเดียวกันเป็นภาพเดียว (Mosaic)
            next_day = ee.Date(scene_date).advance(1, "day")
            day_col = col.filterDate(scene_date, next_day)
            mosaic_img = day_col.mosaic()

            # คำนวณเปอร์เซ็นต์เมฆในเขตหลักสี่ของภาพรวมวันนั้น
            qa = mosaic_img.select("QA_PIXEL")
            cloud_img = qa.bitwiseAnd(30).neq(0)
            cloud_stat = cloud_img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=roi,
                scale=SCALE,
                crs=CRS,
                maxPixels=1e7,
            ).getInfo()

            cloud_val = cloud_stat.get("QA_PIXEL")
            cloud_pct = round(float(cloud_val) * 100.0, 2) if cloud_val is not None else 100.0

            # ด่านคัดกรองที่ 1: เมฆเกินเกณฑ์
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

            # สร้างชั้นข้อมูล 4 แบนด์ + สกัดตารางพิกเซล
            stack = _build_stacked_image(mosaic_img, roi)
            csv_text, total_pixels, valid_pixels = _generate_pixels_csv(stack, roi)
            valid_ratio = round(valid_pixels / total_pixels, 4) if total_pixels > 0 else 0.0

            # ด่านคัดกรองที่ 2: สัดส่วนพิกเซลดีต่ำกว่าเกณฑ์ขั้นต่ำ
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

            # ดาวน์โหลด GeoTIFF จาก GEE และอัปโหลดขึ้น S3
            tif_url = stack.getDownloadURL({
                "name": f"scene_{scene_date}_bands",
                "scale": SCALE,
                "crs": CRS,
                "region": roi,
                "format": "GEO_TIFF",
            })
            resp = requests.get(tif_url, timeout=120)
            resp.raise_for_status()

            # บันทึกครบ 3 ไฟล์ตาม Data Contract (bands.tif -> pixels.csv -> metadata.json)
            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/bands.tif",
                Body=resp.content,
                ContentType="image/tiff",
            )
            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/pixels.csv",
                Body=csv_text.encode("utf-8"),
                ContentType="text/csv",
            )
            s3.put_object(
                Bucket=bucket,
                Key=meta_key,
                Body=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
                ContentType="application/json",
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
        "start_date": start_date,
        "end_date": end_date,
        "dry_run": dry_run,
        "total_dates_found": len(scenes_by_date),
        "processed_scenes": results,
    }