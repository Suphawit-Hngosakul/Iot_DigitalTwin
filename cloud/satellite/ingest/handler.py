import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from ingest import build_seasonal_composite, run_ingest, update_catalog


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Entry point ของ AWS Lambda รองรับทั้งโหมด ingest, composite และ catalog"""
    event = event or {}
    mode = event.get("mode", "ingest")
    dry_run = bool(event.get("dry_run", False))
    force = bool(event.get("force", False))

    try:
        if mode == "composite":
            year = int(event.get("year", datetime.now(timezone.utc).year))
            result = build_seasonal_composite(year=year, dry_run=dry_run, force=force)
            if not dry_run:
                result["catalog"] = update_catalog()

        elif mode == "catalog":
            result = update_catalog()

        else:
            now_utc = datetime.now(timezone.utc)
            default_start = (now_utc - timedelta(days=35)).strftime("%Y-%m-%d")
            default_end = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")

            start_date = event.get("start_date", default_start)
            end_date = event.get("end_date", default_end)
            max_cloud_pct = float(event.get("max_cloud_pct", 75.0))
            min_valid_ratio = float(event.get("min_valid_ratio", 0.15))

            result = run_ingest(
                start_date=start_date,
                end_date=end_date,
                dry_run=dry_run,
                force=force,
                max_cloud_pct=max_cloud_pct,
                min_valid_ratio=min_valid_ratio,
            )
            if not dry_run:
                result["catalog"] = update_catalog()

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