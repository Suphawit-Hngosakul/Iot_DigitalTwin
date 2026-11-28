"""GEE connectivity test Lambda (issue #25, spec 50).

Authenticates to Google Earth Engine with Workload Identity Federation: the
Lambda's own AWS credentials (role iot-dt-satellite-lambda) are exchanged for a short-lived Google
token, so no service-account key is stored in the repo or in SSM.

Env (set by cloud/infra/modules/satellite):
    GEE_WIF_CONFIG  external_account JSON (built from Terraform var gee_wif)
    GEE_PROJECT     Google Cloud project that owns the assets
    GEE_ASSETS      asset to read, e.g. projects/<project>/assets/laksi_boundary

Invoke with {} or {"assets": "<asset path>"}. Errors are raised, so a failed
check shows up as a failed invocation (Lambda Errors metric) instead of a 200.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import ee
from google.auth import aws

log = logging.getLogger()
log.setLevel(logging.INFO)

SCOPES = [
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
]

_initialized = False


def _init_gee() -> None:
    """Initialize GEE once per container (cold start)."""
    global _initialized
    if _initialized:
        return
    project = os.environ["GEE_PROJECT"]
    info = json.loads(os.environ["GEE_WIF_CONFIG"])
    # In Lambda, google-auth reads AWS_ACCESS_KEY_ID / AWS_SESSION_TOKEN /
    # AWS_REGION from the environment, so the metadata URLs are never called.
    credentials = aws.Credentials.from_info(info, scopes=SCOPES)
    ee.Initialize(credentials, project=project)
    _initialized = True
    log.info("GEE initialized for project %s", project)


def lambda_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    _init_gee()

    calc = ee.Number(1).add(1).getInfo()
    asset = (event or {}).get("assets") or os.environ["GEE_ASSETS"]
    size = ee.FeatureCollection(asset).size().getInfo()
    log.info("1+1=%s, %s has %s features", calc, asset, size)

    return {"status": "ok", "calc_result": calc, "asset_path": asset, "feature_count": size}
