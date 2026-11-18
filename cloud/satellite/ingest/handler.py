import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from ingest import run_ingest


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Entry point ของ AWS Lambda รองรับทั้งการเรียกอัตโนมัติและ Backfill กำหนดช่วงวันที่เอง"""
    event = event or {}
    now_utc = datetime.now(timezone.utc)

    # ค่าเริ่มต้น: ย้อนหลัง 35 วัน (ครอบคลุมรอบเดือน + เผื่อรอบวงโคจร 16 วัน)
    default_start = (now_utc - timedelta(days=35)).strftime("%Y-%m-%d")
    default_end = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")

    start_date = event.get("start_date", default_start)
    end_date = event.get("end_date", default_end)
    dry_run = bool(event.get("dry_run", False))
    force = bool(event.get("force", False))
    max_cloud_pct = float(event.get("max_cloud_pct", 75.0))
    min_valid_ratio = float(event.get("min_valid_ratio", 0.15))

    try:
        result = run_ingest(
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            force=force,
            max_cloud_pct=max_cloud_pct,
            min_valid_ratio=min_valid_ratio,
        )
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result, ensure_ascii=False),
        }
    except Exception as exc:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False),
        }