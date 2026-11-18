"""Lambda satellite_ingest (issue #27, spec 50 §3-4).

Events:
    {"mode": "ingest"}                                   # EventBridge รายวัน: 35 วันล่าสุด
    {"mode": "ingest", "start_date": "2024-01-01", "end_date": "2024-03-01"}   # backfill ทีละช่วง
    {"mode": "composite", "year": 2024}                  # สร้าง/อัปเดต composite หน้าร้อนปีนั้น
    {"mode": "catalog"}                                  # สร้าง curated/satellite/catalog.json ใหม่
    ทุกโหมดใส่ "dry_run": true / "force": true ได้; ingest ใส่ "max_cloud_pct", "min_valid_ratio" ได้

mode=ingest อัปเดต composite ของฤดูที่มีฉากในช่วงที่ดึงให้เอง (สร้างใหม่เฉพาะเมื่อมีฉากใหม่)
ถ้ามีฉากหรือ composite ที่ error จะ raise หลังอัปเดต catalog แล้ว เพื่อให้ invocation ขึ้นเป็น
failed (Lambda Errors metric / CloudWatch) ไม่ใช่ 200 แบบเงียบ ๆ
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from ingest import (
    DEFAULT_MAX_CLOUD_PCT,
    DEFAULT_MIN_VALID_RATIO,
    build_seasonal_composite,
    env_float,
    run_ingest,
    season_years,
    update_catalog,
)

log = logging.getLogger()
log.setLevel(logging.INFO)

DEFAULT_LOOKBACK_DAYS = 35  # L2 ของ Landsat ขึ้น GEE ช้าได้หลายวัน มองย้อนให้ครอบคลุม


class IngestError(RuntimeError):
    """มีฉากหรือ composite ที่ทำไม่สำเร็จ (ผลส่วนที่สำเร็จเขียนลง S3 แล้ว)"""


def _composite_errors(composites: List[Dict[str, Any]]) -> int:
    return sum(1 for c in composites if c.get("status") == "error")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    mode = event.get("mode", "ingest")
    dry_run = bool(event.get("dry_run", False))
    force = bool(event.get("force", False))

    if mode == "composite":
        year = int(event.get("year", datetime.now(timezone.utc).year))
        result = build_seasonal_composite(year=year, dry_run=dry_run, force=force)
        if not dry_run:
            result["catalog"] = update_catalog()

    elif mode == "catalog":
        result = update_catalog()

    elif mode == "ingest":
        now_utc = datetime.now(timezone.utc)
        start_date = event.get("start_date") or (now_utc - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        end_date = event.get("end_date") or (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")
        max_cloud_pct = float(event.get("max_cloud_pct", env_float("MAX_CLOUD_PCT", DEFAULT_MAX_CLOUD_PCT)))
        min_valid_ratio = float(event.get("min_valid_ratio", env_float("MIN_VALID_RATIO", DEFAULT_MIN_VALID_RATIO)))

        result = run_ingest(
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            force=force,
            max_cloud_pct=max_cloud_pct,
            min_valid_ratio=min_valid_ratio,
        )

        # composite ของฤดูที่มีวันที่ใหม่ในรอบนี้ (ไม่นับวันที่ทำไปแล้วในรอบก่อน)
        new_dates = [
            r["scene_date"] for r in result["processed_scenes"]
            if r["status"] not in ("already_exists", "already_skipped", "error")
        ]
        composites: List[Dict[str, Any]] = []
        for year in season_years(new_dates):
            try:
                composites.append(build_seasonal_composite(year=year, dry_run=dry_run))
            except Exception as exc:  # noqa: BLE001 - keep going, report below
                log.exception("composite %s failed", year)
                composites.append({"status": "error", "mode": "composite", "year": year, "error": str(exc)})
        result["composites"] = composites

        if not dry_run:
            result["catalog"] = update_catalog()

    else:
        raise ValueError(f"unknown mode: {mode!r} (ingest | composite | catalog)")

    log.info(json.dumps(result, ensure_ascii=False, default=str))

    n_errors = result.get("errors", 0) + _composite_errors(result.get("composites", []))
    if n_errors:
        failed = [r for r in result.get("processed_scenes", []) if r.get("status") == "error"]
        failed += [c for c in result.get("composites", []) if c.get("status") == "error"]
        raise IngestError(f"{n_errors} item(s) failed: {json.dumps(failed, ensure_ascii=False, default=str)[:2000]}")

    return result
