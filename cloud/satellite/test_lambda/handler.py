"""Lambda handler for Google Earth Engine (GEE) verification (issue #25).

Uses Google Cloud Workload Identity Federation (WIF) with AWS STS credentials
to authenticate against Google Earth Engine without static private keys.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import ee
from google.auth import aws

log = logging.getLogger()
log.setLevel(logging.INFO)

CONFIG_FILENAME = "gee-wif-config.json"
DEFAULT_PROJECT = "turnkey-tribute-511903-t6"
DEFAULT_ASSETS = "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"

_INITIALIZED = False


def _init_gee() -> None:
    """Initialize GEE credentials and client during cold start."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    project = os.environ.get("GEE_PROJECT", DEFAULT_PROJECT)
    config_path = Path(__file__).parent / CONFIG_FILENAME

    if not config_path.exists():
        raise FileNotFoundError(f"WIF config not found at {config_path}")

    log.info("Loading WIF config from %s for project %s", config_path.name, project)
    with open(config_path, encoding="utf-8") as f:
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
    log.info("GEE successfully initialized for project: %s", project)


def lambda_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """Execute basic GEE calculation and verify asset accessibility."""
    try:
        _init_gee()

        # 1. Basic calculation test (1 + 1 = 2)
        num_test = ee.Number(1).add(1).getInfo()
        log.info("Test 1+1 = %s", num_test)

        # 2. Asset accessibility test
        assets_path = (event or {}).get("assets") or os.environ.get("GEE_ASSETS", DEFAULT_ASSETS)
        log.info("Inspecting asset: %s", assets_path)
        fc = ee.FeatureCollection(assets_path)
        fc_size = fc.size().getInfo()
        log.info("Asset size = %s", fc_size)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": "success",
                "calc_result": num_test,
                "laksi_boundary_size": fc_size,
                "asset_path": assets_path,
            }),
        }

    except Exception as exc:
        log.exception("Error executing GEE Lambda handler: %s", exc)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "status": "error",
                "message": str(exc),
            }),
        }
