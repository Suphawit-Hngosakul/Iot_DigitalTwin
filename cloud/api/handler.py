"""Lambda api: IoT data for the web (issue #18, spec 40 §3).

API Gateway HTTP API (payload format 2.0), all GET, JSON out:

    /devices                                   registry + latest published hour
    /latest?device=                            last readings_1min row
    /readings?device=&from=&to=&interval=      readings_1s (<= 6 h) or readings_1min (<= 31 d)
    /quality?device=&from=&to=                 loss per hour (<= 31 d)

`from` / `to` are ISO times. Without an offset they are Thai local time (the
same clock as the CSV DateTime); with one (`Z`, `+07:00`, ...) they are
converted to Thai time. The range is [from, to).

Rules (spec 40 §3):
- every Athena query filters device_id + date (partition projection), so the
  scan stays small; the device must be in devices.csv and dates are built
  here, never copied from the request -> no SQL injection.
- Athena gets at most ATHENA_TIMEOUT_S (25 s, API GW stops at 30 s) -> 504.
- identical requests are answered from memory for CACHE_TTL_S (60 s).
- CORS is done by API Gateway (cors_configuration, ALLOWED_ORIGINS).

The latest published hour comes from the publish markers
processed/_manifests/<batch_id>.json that VerifyBatch writes (spec 30 §4):
cheaper and faster than Athena, and /latest uses its date as the partition.

Only stdlib + boto3 (in the Lambda runtime): no layer.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common import schema

log = logging.getLogger()
log.setLevel(logging.INFO)

ATHENA_TIMEOUT_S = float(os.environ.get("ATHENA_TIMEOUT_S", 25))
CACHE_TTL_S = float(os.environ.get("CACHE_TTL_S", 60))

INTERVALS = {
    # interval -> (table, time column, longest range)
    "1s": ("readings_1s", "ts_local", timedelta(hours=6)),
    "1min": ("readings_1min", "minute_local", timedelta(days=31)),
}
QUALITY_MAX_RANGE = timedelta(days=31)
MANIFEST_PREFIX = "processed/_manifests/"

_s3 = None
_athena = None
_cache: dict[str, tuple[float, dict]] = {}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --- clients ---------------------------------------------------------------

def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _athena_client():
    global _athena
    if _athena is None:
        _athena = boto3.client("athena")
    return _athena


# --- helpers ---------------------------------------------------------------

def _resp(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    }


def _devices() -> dict[str, schema.Device]:
    """device_id -> newest registry row (a station that moved has several)."""
    out: dict[str, schema.Device] = {}
    for d in schema.load_devices():
        cur = out.get(d.device_id)
        if cur is None or d.valid_from >= cur.valid_from:
            out[d.device_id] = d
    return out


def _require_device(params: dict) -> str:
    device = (params.get("device") or "").strip()
    if not device:
        raise ApiError(400, "device is required")
    if device not in _devices():
        raise ApiError(404, f"unknown device: {device}")
    return device


def parse_time(value: str | None, name: str) -> datetime:
    """ISO string -> naive Thai-local datetime. Date only = 00:00."""
    if not value:
        raise ApiError(400, f"{name} is required")
    v = value.strip()
    if v.endswith(("Z", "z")):
        v = v[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(v)
    except ValueError:
        raise ApiError(400, f"{name} is not an ISO time: {value!r}") from None
    if ts.tzinfo is not None:
        ts = ts.astimezone(schema.LOCAL_TZ).replace(tzinfo=None)
    return ts.replace(microsecond=0)


def parse_range(params: dict, max_range: timedelta) -> tuple[datetime, datetime]:
    start = parse_time(params.get("from"), "from")
    end = parse_time(params.get("to"), "to")
    if end <= start:
        raise ApiError(400, "to must be after from")
    if end - start > max_range:
        raise ApiError(400, f"range too long: max {_fmt_td(max_range)}")
    return start, end


def _fmt_td(td: timedelta) -> str:
    return f"{td.days} days" if td.days else f"{td.seconds // 3600} hours"


def partition_dates(start: datetime, end: datetime) -> list[str]:
    """Thai dates touched by [start, end)."""
    last = (end - timedelta(microseconds=1)).date()
    d, out = start.date(), []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _sql_ts(ts: datetime) -> str:
    return f"TIMESTAMP '{ts.strftime('%Y-%m-%d %H:%M:%S')}'"


def _sql_in(values: list[str]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


# --- Athena ----------------------------------------------------------------

def _convert(value: str | None, typ: str):
    if value is None:
        return None
    t = typ.lower()
    if t in ("integer", "bigint", "smallint", "tinyint", "int"):
        return int(value)
    if t in ("double", "float", "real", "decimal"):
        return float(value)
    if t == "boolean":
        return value.lower() == "true"
    if t.startswith("timestamp"):
        # Athena: '2026-11-16 08:00:00.000' -> '2026-11-16T08:00:00'
        return value.replace(" ", "T", 1).split(".")[0]
    return value


def run_query(sql: str, athena=None, timeout_s: float | None = None,
              sleep=time.sleep, clock=time.monotonic) -> tuple[list[str], list[list]]:
    """Run sql in the workgroup; (columns, rows) with typed values."""
    athena = athena or _athena_client()
    timeout_s = ATHENA_TIMEOUT_S if timeout_s is None else timeout_s
    kwargs = {
        "QueryString": sql,
        "QueryExecutionContext": {"Database": os.environ.get("ATHENA_DB", "iot_walking")},
        "WorkGroup": os.environ.get("ATHENA_WORKGROUP", "iot_walking"),
    }
    if os.environ.get("ATHENA_OUTPUT"):
        # Ignored when the workgroup enforces its own output (it does, #17).
        kwargs["ResultConfiguration"] = {"OutputLocation": os.environ["ATHENA_OUTPUT"]}
    started = clock()
    qid = athena.start_query_execution(**kwargs)["QueryExecutionId"]

    delay = 0.2
    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            log.error("athena %s %s: %s\n%s", qid, state,
                      status.get("StateChangeReason"), sql)
            raise ApiError(502, "query failed")
        if clock() - started >= timeout_s:
            try:
                athena.stop_query_execution(QueryExecutionId=qid)
            except (BotoCoreError, ClientError):
                log.warning("could not stop query %s", qid)
            raise ApiError(504, f"query took longer than {timeout_s:g} s")
        sleep(delay)
        delay = min(delay * 1.5, 1.0)

    columns: list[str] = []
    types: list[str] = []
    rows: list[list] = []
    first = True
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        rs = page["ResultSet"]
        if not columns:
            info = rs["ResultSetMetadata"]["ColumnInfo"]
            columns = [c["Name"] for c in info]
            types = [c["Type"] for c in info]
        data = rs["Rows"]
        if first:
            data = data[1:]       # first row of the first page = header
            first = False
        for r in data:
            cells = r.get("Data", [])
            rows.append([_convert(c.get("VarCharValue"), types[i])
                         for i, c in enumerate(cells)])
    return columns, rows


# --- publish markers -------------------------------------------------------

def latest_batch_id(device: str, s3=None) -> str | None:
    """Newest published batch_id of a device (keys sort by date, then hour)."""
    s3 = s3 or _s3_client()
    prefix = f"{MANIFEST_PREFIX}{device}__"
    last = None
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["DATA_BUCKET"], Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                last = obj["Key"]
    if last is None:
        return None
    return last[len(MANIFEST_PREFIX):-len(".json")]


def _read_manifest(batch_id: str, s3=None) -> dict:
    s3 = s3 or _s3_client()
    obj = s3.get_object(Bucket=os.environ["DATA_BUCKET"], Key=schema.s3_key_manifest(batch_id))
    return json.loads(obj["Body"].read())


# --- endpoints -------------------------------------------------------------

def get_devices(params: dict) -> dict:
    out = []
    for device_id, d in sorted(_devices().items()):
        item = {
            "device_id": device_id,
            "device_type": d.device_type,
            "site_name": d.site_name or None,
            "fixed_lat": d.fixed_lat,
            "fixed_lon": d.fixed_lon,
            "latest_batch_id": None,
            "latest_hour_local": None,
            "published_at": None,
        }
        batch_id = latest_batch_id(device_id)
        if batch_id:
            b = schema.parse_batch_id(batch_id)
            item["latest_batch_id"] = batch_id
            item["latest_hour_local"] = _iso(datetime.combine(b.date, datetime.min.time())
                                             + timedelta(hours=b.hour))
            item["published_at"] = _read_manifest(batch_id).get("published_at")
        out.append(item)
    return {"devices": out}


def get_latest(params: dict) -> dict:
    device = _require_device(params)
    batch_id = latest_batch_id(device)
    if batch_id is None:
        raise ApiError(404, f"no published data for {device}")
    day = schema.parse_batch_id(batch_id).date.isoformat()
    cols = ", ".join(name for name, _ in schema.READINGS_1MIN)
    sql = (f"SELECT {cols} FROM readings_1min "
           f"WHERE device_id = '{device}' AND date = '{day}' "
           f"ORDER BY minute_local DESC LIMIT 1")
    columns, rows = run_query(sql)
    if not rows:
        raise ApiError(404, f"no published data for {device}")
    return {"device": device, "row": dict(zip(columns, rows[0]))}


def get_readings(params: dict) -> dict:
    device = _require_device(params)
    interval = params.get("interval") or "1min"
    if interval not in INTERVALS:
        raise ApiError(400, "interval must be 1s or 1min")
    table, ts_col, max_range = INTERVALS[interval]
    start, end = parse_range(params, max_range)
    cols = ", ".join(name for name, _ in schema.TABLES[table])
    sql = (f"SELECT {cols} FROM {table} "
           f"WHERE device_id = '{device}' AND date IN {_sql_in(partition_dates(start, end))} "
           f"AND {ts_col} >= {_sql_ts(start)} AND {ts_col} < {_sql_ts(end)} "
           f"ORDER BY {ts_col}")
    columns, rows = run_query(sql)
    return {"device": device, "interval": interval, "from": _iso(start), "to": _iso(end),
            "columns": columns, "rows": rows}


def get_quality(params: dict) -> dict:
    device = _require_device(params)
    start, end = parse_range(params, QUALITY_MAX_RANGE)
    sql = (
        "SELECT batch_id, "
        "SUM(expected_rows) AS expected_rows, SUM(received_rows) AS received_rows, "
        "SUM(missing_rows) AS missing_rows, SUM(duplicate_rows) AS duplicate_rows, "
        "SUM(invalid_rows) AS invalid_rows, COUNT(*) AS sessions "
        f"FROM quality WHERE device_id = '{device}' "
        f"AND date IN {_sql_in(partition_dates(start, end))} "
        "GROUP BY batch_id ORDER BY batch_id")
    columns, rows = run_query(sql)
    hours = []
    for r in rows:
        row = dict(zip(columns, r))
        b = schema.parse_batch_id(row["batch_id"])
        hour_start = datetime.combine(b.date, datetime.min.time()) + timedelta(hours=b.hour)
        if not (start < hour_start + timedelta(hours=1) and hour_start < end):
            continue          # same date, but the hour is outside [from, to)
        expected = row["expected_rows"] or 0
        row["hour_local"] = _iso(hour_start)
        row["dropped_rows"] = (row["duplicate_rows"] or 0) + (row["invalid_rows"] or 0)
        # Same definition as batch_eval: missing / expected over the hour.
        row["loss_rate"] = (row["missing_rows"] or 0) / expected if expected else 0.0
        hours.append(row)
    return {"device": device, "from": _iso(start), "to": _iso(end), "hours": hours}


ROUTES = {
    "/devices": get_devices,
    "/latest": get_latest,
    "/readings": get_readings,
    "/quality": get_quality,
}


def _cache_key(path: str, params: dict) -> str:
    return path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def handler(event, context=None):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath") or http.get("path") or "/"
    params = event.get("queryStringParameters") or {}

    fn = ROUTES.get(path.rstrip("/") or "/")
    if fn is None:
        return _resp(404, {"error": f"no route {path}"})
    if method != "GET":
        return _resp(405, {"error": "only GET"})

    key = _cache_key(path, params)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return _resp(200, hit[1])

    try:
        body = fn(params)
    except ApiError as e:
        return _resp(e.status, {"error": e.message})
    except (BotoCoreError, ClientError):
        log.exception("aws error on %s", key)
        return _resp(502, {"error": "upstream error"})

    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (now + CACHE_TTL_S, body)
    return _resp(200, body)
