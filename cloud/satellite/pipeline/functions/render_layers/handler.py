"""RenderLayers — satellite pipeline (issue #31, spec 51 §4).

scene / season : lst.png, ndvi.png, ndbi.png (30 m) + <layer>.json bounds/legend
buildings      : building_density.png (one PNG pixel per 90 m cell, never footprints — D17)
-> curated/satellite/layers/<batch_id>/   (buildings: curated/satellite/layers/static/)

Fixed colour scales (satellite_grid.COLORMAPS) so scenes compare. bands.tif is
not read: it is clipped with laksi_boundary and must not leave the pipeline (D17).
Output: the input plus {"layers": {"prefix", "files"}}
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from common import satellite_batch as sb
from common import satellite_grid as sg
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

PIXEL_LAYERS = ("lst", "ndvi", "ndbi")
CELL_LAYERS = ("building_density",)

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def render(s3, bucket: str, event: dict) -> dict:
    batch_id = event["batch_id"]
    kind = ss.batch_kind(batch_id)
    res, _ = sb.evaluate(s3, bucket, batch_id, event["check"]["manifest"])
    if res.status != "PASS":
        raise RuntimeError(f"{batch_id}: contract no longer passes: {res.fail_reasons}")
    prefix = ss.s3_prefix_layers(batch_id)
    written = []
    for layer in (CELL_LAYERS if kind == "buildings" else PIXEL_LAYERS):
        if kind == "buildings":
            png, bounds = sg.render_cells(res.rows, layer)
        else:
            png, bounds = sg.render_pixels(res.rows, layer, kind)
        bounds["batch_id"] = batch_id
        s3.put_object(Bucket=bucket, Key=f"{prefix}{layer}.png", Body=png, ContentType="image/png")
        s3.put_object(Bucket=bucket, Key=f"{prefix}{layer}.json", ContentType="application/json",
                      Body=json.dumps(bounds, ensure_ascii=False, indent=1).encode())
        written += [f"{layer}.png", f"{layer}.json"]
    return {"prefix": prefix, "files": written}


def handler(event, context=None):
    result = render(_client(), os.environ["DATA_BUCKET"], event)
    log.info(json.dumps({"batch_id": event["batch_id"], **result}))
    return {**event, "layers": result}
