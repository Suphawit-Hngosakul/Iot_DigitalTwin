"""Lambda ingest: device CSV chunk -> S3 raw/csv/ (issue #9, spec 20).

POST /ingest (API Gateway HTTP API, payload format 2.0)
    Authorization: Bearer <device token>
    X-Device-Id:   <device_id>
    X-Chunk-Id:    <session>-<first_record>-<last_record>
    Body:          CSV with header, <= 300 rows (~36-40 KB), <= MAX_BODY_BYTES

The device sends one chunk at a time and retries on any non-200 (spike #4:
only one TLS connection while recording, ~36 KB per POST). So this function is
stateless and idempotent: the S3 key is derived only from device_id, the row's
Thai-local hour and chunk_id, and a retry overwrites the same objects with the
same bytes.

Rows are written verbatim (raw = what the device sent). Value checks, dedup and
parsing are the pipeline's job; here we only check who sent it and that the
header matches the schema.
"""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import json
import logging
import os
from collections import OrderedDict

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.schema import (
    CHUNK_ID_RE,
    CSV_COLUMNS,
    check_header,
    UNKNOWN,
    make_batch_id,
    raw_partition,
    s3_key_pending,
    s3_key_raw,
)

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", 1024 * 1024))

_COL = {name: i for i, name in enumerate(CSV_COLUMNS)}
_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def hash_token(token: str) -> str:
    """sha256 hex of a device token; DEVICE_TOKENS stores only these."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_map() -> dict[str, str]:
    raw = os.environ.get("DEVICE_TOKENS", "{}")
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        log.error("DEVICE_TOKENS is not valid JSON")
        return {}
    return {str(k).lower(): str(v) for k, v in m.items()}


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _err(status: int, msg: str, **extra) -> dict:
    log.info(json.dumps({"status": status, "error": msg, **extra}))
    return _resp(status, {"error": msg})


def _headers(event: dict) -> dict[str, str]:
    # HTTP API v2 lowercases header names already; do it anyway for tests / v1.
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}


def _body_bytes(event: dict) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body, validate=True)
    return body.encode("utf-8")


def _device_for(headers: dict[str, str]) -> str | None:
    auth = headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return _token_map().get(hash_token(token))


def _parse_line(line: str) -> list[str]:
    return next(csv.reader([line]), [])


def _field(fields: list[str], col: str) -> str | None:
    i = _COL[col]
    return fields[i].strip() if i < len(fields) else None


def handler(event, context=None):
    headers = _headers(event)

    # 1. token -> device (401)
    device_id = _device_for(headers)
    if device_id is None:
        return _err(401, "missing or unknown device token")

    # 2. X-Device-Id must be the token's device (403)
    hdr_device = headers.get("x-device-id", "").strip()
    if hdr_device != device_id:
        return _err(403, "X-Device-Id does not match token",
                    device_id=device_id, x_device_id=hdr_device)

    # 3. X-Chunk-Id format (400)
    chunk_id = headers.get("x-chunk-id", "").strip()
    if not CHUNK_ID_RE.match(chunk_id):
        return _err(400, "X-Chunk-Id must be <session>-<first_record>-<last_record>",
                    device_id=device_id, chunk_id=chunk_id)
    session, first, last = (int(x) for x in chunk_id.split("-"))
    if first > last:
        return _err(400, "X-Chunk-Id first_record > last_record",
                    device_id=device_id, chunk_id=chunk_id)

    # 4. body size (413) and decoding (400)
    try:
        body = _body_bytes(event)
    except (binascii.Error, ValueError):
        return _err(400, "body is not valid base64", device_id=device_id)
    if len(body) > MAX_BODY_BYTES:
        return _err(413, f"body larger than {MAX_BODY_BYTES} bytes",
                    device_id=device_id, chunk_id=chunk_id, bytes=len(body))
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _err(400, "body is not UTF-8", device_id=device_id, chunk_id=chunk_id)

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return _err(400, "empty body", device_id=device_id, chunk_id=chunk_id)

    # 5. header must match spec 01 exactly (400)
    header_line = lines[0]
    problems = check_header(_parse_line(header_line))
    if problems:
        return _err(400, "bad CSV header: " + "; ".join(problems),
                    device_id=device_id, chunk_id=chunk_id)
    rows = lines[1:]
    if not rows:
        return _err(400, "CSV has a header but no rows",
                    device_id=device_id, chunk_id=chunk_id)

    # 6. every row: Device_ID == token's device (403);
    #    Session / Record_ID inside chunk_id so a different chunk can never
    #    overwrite this one's key (400). Then group by Thai-local hour.
    groups: "OrderedDict[tuple[str, str], list[str]]" = OrderedDict()
    for n, line in enumerate(rows, start=2):
        fields = _parse_line(line)
        row_dev = _field(fields, "Device_ID")
        if row_dev != device_id:
            return _err(403, f"line {n}: Device_ID {row_dev!r} does not match token",
                        device_id=device_id, chunk_id=chunk_id)
        try:
            row_session = int(_field(fields, "Session") or "")
            row_record = int(_field(fields, "Record_ID") or "")
        except ValueError:
            return _err(400, f"line {n}: Session/Record_ID is not an integer",
                        device_id=device_id, chunk_id=chunk_id)
        if row_session != session or not first <= row_record <= last:
            return _err(400, f"line {n}: Session/Record_ID outside X-Chunk-Id",
                        device_id=device_id, chunk_id=chunk_id)
        part = raw_partition({
            "DateTime": _field(fields, "DateTime"),
            "Time_Valid": _field(fields, "Time_Valid"),
        })
        groups.setdefault(part, []).append(line)

    # 7. one object per hour, header in every file, rows verbatim
    bucket = os.environ["DATA_BUCKET"]
    written = []
    try:
        for (d, h), group in groups.items():
            key = s3_key_raw(device_id, d, h, chunk_id)
            data = "\n".join([header_line, *group]) + "\n"
            _client().put_object(
                Bucket=bucket,
                Key=key,
                Body=data.encode("utf-8"),
                ContentType="text/csv",
                Metadata={"chunk-id": chunk_id, "rows": str(len(group))},
            )
            written.append(key)
        # after the data: a marker that is newer than the files it announces
        for d, h in groups:
            if d != UNKNOWN and h != UNKNOWN:
                _client().put_object(Bucket=bucket, Body=b"",
                                     Key=s3_key_pending(make_batch_id(device_id, d, int(h))))
    except (ClientError, BotoCoreError) as e:
        log.exception("S3 write failed")
        return _err(503, "storage unavailable, retry later",
                    device_id=device_id, chunk_id=chunk_id, detail=str(e))

    log.info(json.dumps({"status": 200, "device_id": device_id,
                         "chunk_id": chunk_id, "rows": len(rows),
                         "written": written}))
    return _resp(200, {"written": written, "rows": len(rows)})

