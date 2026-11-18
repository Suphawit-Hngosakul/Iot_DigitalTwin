"""Static building density per 90 m cell (issue #28, spec 50 §2, §4, §7).

คำนวณความหนาแน่นสิ่งปลูกสร้างใน GEE บนกริดกลาง 90 x 90 m (EPSG:32647, ORIGIN 660015, 1545015)
- แหล่งหลัก: building.shp ของ กทม. (ได้ builtup_pct, building_count, floors_mean, far)
- แหล่งเสริม: Google Open Buildings v3 (confidence >= 0.7) เติมส่วนที่ BMA ไม่ครอบคลุม
- ดึงกลับเฉพาะตารางสรุปต่อเซลล์ลง raw/satellite/static=buildings/buildings_grid.csv (ห้ามเก็บ footprint)
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import boto3
import ee
import requests

from ingest import (
    CRS,
    ORIGIN_X,
    ORIGIN_Y,
    _s3_key_exists,
    env_float,
    init_ee,
)

GRID_SIZE_M = 90
VEC_SCALE_M = 5
# จัดแนวพิกเซลราสเตอร์ 5 m ให้ชิดกับ ORIGIN_X / ORIGIN_Y พอดี (18x18 = 324 พิกเซลย่อยต่อ 1 เซลล์ 90 m)
VEC_TRANSFORM = [VEC_SCALE_M, 0, ORIGIN_X, 0, -VEC_SCALE_M, ORIGIN_Y]
CELL_AREA_M2 = float(GRID_SIZE_M * GRID_SIZE_M)  # 8,100 ตร.ม.

DEFAULT_BLDG_ASSET = "projects/turnkey-tribute-511903-t6/assets/building"
DEFAULT_BLDG_CONF_MIN = 0.7
BUILDINGS_PREFIX = "raw/satellite/static=buildings"
BUILDINGS_COLUMNS = [
    "cell_id",
    "builtup_pct",
    "building_count",
    "floors_mean",
    "far",
    "in_cover",
    "bldg_source",
]


def make_cell_id(col: int, row: int) -> str:
    """สร้างรหัสเซลล์ c0000_r0000 ตรงกับ cloud/pipeline/common/grid.py"""
    if not (0 <= col <= 9999 and 0 <= row <= 9999):
        raise ValueError(f"cell out of grid: col={col} row={row}")
    return f"c{col:04d}_r{row:04d}"


def build_grid_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> List[Tuple[str, float, float, float, float]]:
    """สร้างรายการ (cell_id, x0, y0, x1, y1) ใน EPSG:32647 ที่ครอบคลุมกรอบพื้นที่"""
    col0 = math.floor((xmin - ORIGIN_X) / GRID_SIZE_M)
    row0 = math.floor((ORIGIN_Y - ymax) / GRID_SIZE_M)
    col1 = math.ceil((xmax - ORIGIN_X) / GRID_SIZE_M)
    row1 = math.ceil((ORIGIN_Y - ymin) / GRID_SIZE_M)

    cells: List[Tuple[str, float, float, float, float]] = []
    for c in range(col0, col1):
        for r in range(row0, row1):
            x0 = ORIGIN_X + c * GRID_SIZE_M
            y1 = ORIGIN_Y - r * GRID_SIZE_M
            x1 = x0 + GRID_SIZE_M
            y0 = y1 - GRID_SIZE_M
            cells.append((make_cell_id(c, r), x0, y0, x1, y1))
    return cells


def _paint_mask(fc: ee.FeatureCollection, band_name: str) -> ee.Image:
    return ee.Image().byte().paint(fc, 1).unmask(0).rename(band_name)


def run_buildings_ingest(
    dry_run: bool = False,
    force: bool = False,
    conf_min: Optional_float = None,
) -> Dict[str, Any]:
    """คำนวณค่าสรุปอาคารต่อเซลล์ 90 m ใน GEE และอัปโหลดลง S3"""
    bucket = os.environ.get("DATA_BUCKET")
    if not bucket and not dry_run:
        raise ValueError("Missing DATA_BUCKET environment variable")

    s3 = boto3.client("s3") if not dry_run else None
    complete_key = f"{BUILDINGS_PREFIX}/_COMPLETE"

    if not dry_run and not force and _s3_key_exists(s3, bucket, complete_key):
        return {
            "status": "success",
            "mode": "buildings",
            "result": "already_exists",
            "s3_prefix": f"{BUILDINGS_PREFIX}/",
        }

    init_ee()

    if conf_min is None:
        conf_min = env_float("BLDG_CONF_MIN", DEFAULT_BLDG_CONF_MIN)

    aoi_asset = os.environ["GEE_ASSETS"]
    bldg_asset = os.environ.get("GEE_BUILDING_ASSET", DEFAULT_BLDG_ASSET)

    roi = ee.FeatureCollection(aoi_asset).geometry()
    bma_fc = ee.FeatureCollection(bldg_asset).filterBounds(roi.buffer(200))
    ob_fc = (
        ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons")
        .filterBounds(roi.buffer(200))
        .filter(ee.Filter.gte("confidence", conf_min))
    )

    # 1. สร้างเซลล์กริด 90 m เฉพาะเซลล์ที่จุดกึ่งกลางอยู่ใน AOI (ตรงกับ grid.py และ laksi_cells_v3.js)
    bb = roi.bounds(1, CRS).coordinates().get(0).getInfo()
    xs = [p[0] for p in bb]
    ys = [p[1] for p in bb]
    raw_cells = build_grid_bounds(min(xs), min(ys), max(xs), max(ys))

    ee_feats = [
        ee.Feature(
            ee.Geometry.Rectangle([x0, y0, x1, y1], CRS, False),
            {"cell_id": cid},
        )
        for cid, x0, y0, x1, y1 in raw_cells
    ]

    cells_fc = (
        ee.FeatureCollection(ee_feats)
        .filterBounds(roi)
        .map(
            lambda f: f.set(
                "inside", ee.Algorithms.If(roi.contains(f.geometry().centroid(1), 1), 1, 0)
            )
        )
        .filter(ee.Filter.eq("inside", 1))
        .select(["cell_id"])
    )

    # 2. ชุดข้อมูลหลัก: building.shp ของ กทม. (bma_shp)
    bma_mask = _paint_mask(bma_fc, "bma_r")
    bma_cover = _paint_mask(bma_fc.map(lambda f: f.buffer(100, 5)), "bma_cov")

    def _bma_centroid(f: ee.Feature) -> ee.Feature:
        fl = ee.Number(f.get("BL_NSTOREY"))
        area = ee.Number(f.get("BL_AREA"))
        fl_ok = fl.gt(0)  # 0 = ไม่ทราบจำนวนชั้น (spec 50 §7)
        return ee.Feature(
            f.geometry().centroid(1),
            {
                "cnt": 1,
                "fl_ok": fl_ok,
                "fl_sum": fl.multiply(fl_ok),
                "gfa": area.multiply(fl.multiply(fl_ok)),
            },
        )

    bma_cents = bma_fc.map(_bma_centroid)
    bma_cnt_img = bma_cents.reduceToImage(["cnt"], ee.Reducer.sum()).unmask(0).rename("bma_cnt")
    bma_fl_ok_img = bma_cents.reduceToImage(["fl_ok"], ee.Reducer.sum()).unmask(0).rename("bma_fl_ok")
    bma_fl_sum_img = bma_cents.reduceToImage(["fl_sum"], ee.Reducer.sum()).unmask(0).rename("bma_fl_sum")
    bma_gfa_img = bma_cents.reduceToImage(["gfa"], ee.Reducer.sum()).unmask(0).rename("bma_gfa")

    # 3. ชุดข้อมูลเสริม: Google Open Buildings v3 เติมส่วนที่ BMA ไม่ครอบคลุม
    ob_mask = _paint_mask(ob_fc, "ob_r")
    ob_cover = _paint_mask(ob_fc.map(lambda f: f.buffer(100, 5)), "ob_cov")
    ob_cents = ob_fc.map(lambda f: ee.Feature(f.geometry().centroid(1), {"cnt": 1}))
    ob_cnt_img = ob_cents.reduceToImage(["cnt"], ee.Reducer.sum()).unmask(0).rename("ob_cnt")

    # 4. รวมค่าใน GEE บนกริด 5 m แล้วดาวน์โหลดเฉพาะตารางสรุปต่อเซลล์ (ไม่มี geometry)
    ratio_stack = ee.Image.cat([bma_mask, bma_cover, ob_mask, ob_cover])
    sum_stack = ee.Image.cat([
        bma_cnt_img, bma_fl_ok_img, bma_fl_sum_img, bma_gfa_img, ob_cnt_img
    ])

    step1 = ratio_stack.reduceRegions(
        collection=cells_fc,
        reducer=ee.Reducer.mean(),
        crs=CRS,
        crsTransform=VEC_TRANSFORM,
    )
    step2 = sum_stack.reduceRegions(
        collection=step1,
        reducer=ee.Reducer.sum(),
        crs=CRS,
        crsTransform=VEC_TRANSFORM,
    )

    stat_cols = [
        "cell_id",
        "bma_r",
        "bma_cov",
        "ob_r",
        "ob_cov",
        "bma_cnt",
        "bma_fl_ok",
        "bma_fl_sum",
        "bma_gfa",
        "ob_cnt",
    ]
    csv_url = step2.getDownloadURL(filetype="CSV", selectors=stat_cols)
    resp = requests.get(csv_url, timeout=180)
    resp.raise_for_status()

    raw_rows = list(csv.DictReader(io.StringIO(resp.text)))
    raw_rows.sort(key=lambda r: r["cell_id"])

    # 5. จัดรูปตารางตาม Schema BUILDINGS_GRID (cloud/pipeline/common/satellite_schema.py)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(BUILDINGS_COLUMNS)

    bma_cells = 0
    ob_cells = 0
    uncovered_cells = 0
    total_buildings = 0

    for p in raw_rows:
        cid = p["cell_id"]
        bma_cov = float(p.get("bma_cov") or 0.0)

        if bma_cov >= 0.5:
            bma_cells += 1
            bldg_source = "bma_shp"
            in_cover = True
            builtup_pct = round(min(100.0, max(0.0, float(p.get("bma_r") or 0.0) * 100.0)), 4)
            cnt = int(round(float(p.get("bma_cnt") or 0.0)))
            total_buildings += cnt

            fl_ok = float(p.get("bma_fl_ok") or 0.0)
            fl_sum = float(p.get("bma_fl_sum") or 0.0)
            gfa = float(p.get("bma_gfa") or 0.0)

            floors_mean: Any = round(fl_sum / fl_ok, 2) if fl_ok > 0 else ""
            if cnt == 0:
                far: Any = 0.0
            elif fl_ok > 0:
                far = round(gfa / CELL_AREA_M2, 4)
            else:
                far = ""
        else:
            bldg_source = "open_buildings"
            ob_cov = float(p.get("ob_cov") or 0.0)
            in_cover = ob_cov >= 0.5

            if in_cover:
                ob_cells += 1
                builtup_pct = round(min(100.0, max(0.0, float(p.get("ob_r") or 0.0) * 100.0)), 4)
                cnt = int(round(float(p.get("ob_cnt") or 0.0)))
                total_buildings += cnt
            else:
                uncovered_cells += 1
                builtup_pct = ""
                cnt = ""

            floors_mean = ""
            far = ""

        writer.writerow([
            cid,
            builtup_pct,
            cnt,
            floors_mean,
            far,
            in_cover,
            bldg_source,
        ])

    csv_text = output.getvalue()
    metadata = {
        "period": "static_buildings",
        "period_type": "static",
        "source_type": "static_buildings",
        "primary_asset": bldg_asset,
        "fallback_dataset": "GOOGLE/Research/open-buildings/v3/polygons",
        "open_buildings_conf_min": conf_min,
        "crs": CRS,
        "grid_size_m": GRID_SIZE_M,
        "vec_scale_m": VEC_SCALE_M,
        "origin_x": ORIGIN_X,
        "origin_y": ORIGIN_Y,
        "total_cells": len(raw_rows),
        "bma_shp_cells": bma_cells,
        "open_buildings_cells": ob_cells,
        "uncovered_cells": uncovered_cells,
        "total_buildings_counted": total_buildings,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    if not dry_run:
        s3.delete_object(Bucket=bucket, Key=complete_key)
        s3.put_object(
            Bucket=bucket,
            Key=f"{BUILDINGS_PREFIX}/buildings_grid.csv",
            Body=csv_text.encode("utf-8"),
            ContentType="text/csv",
        )
        s3.put_object(
            Bucket=bucket,
            Key=f"{BUILDINGS_PREFIX}/metadata.json",
            Body=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        s3.put_object(
            Bucket=bucket,
            Key=complete_key,
            Body=b"",
            ContentType="text/plain",
        )

    return {
        "status": "success",
        "mode": "buildings",
        "result": "dry_run_ready" if dry_run else "created",
        "s3_prefix": f"{BUILDINGS_PREFIX}/",
        "summary": metadata,
    }


if __name__ == "__main__":
    sample = build_grid_bounds(666143.0, 1525559.0, 672325.0, 1538812.0)
    print(f"Generated {len(sample)} bounding-box cells. First: {sample[0]}, Last: {sample[-1]}")