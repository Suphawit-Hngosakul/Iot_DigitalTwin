"""Lambda api: IoT data for the web (issue #18, spec 40 §3).

API Gateway HTTP API (payload format 2.0), all GET, JSON out:

    /devices                                   registry + latest published hour
    /latest?device=                            last readings_1min row
    /readings?device=&from=&to=&interval=      readings_1s (<= 6 h) or readings_1min (<= 31 d)
    /quality?device=&from=&to=                 loss per hour (<= 31 d)
    /layers?period=                            satellite PNG layers (presigned URLs) + bounds + legend (#34)
    /grid?period=&vars=&match=                 GeoJSON 90 m grid with per-cell values (#34)
    /grid/{cell_id}?period=                    every value of one cell, window + all (#34)

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
import re
import time
from datetime import date, datetime, timedelta
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from common import curated, grid, schema
from common import satellite_schema as ss

log = logging.getLogger()
log.setLevel(logging.INFO)

ATHENA_TIMEOUT_S = float(os.environ.get("ATHENA_TIMEOUT_S", 25))
CACHE_TTL_S = float(os.environ.get("CACHE_TTL_S", 60))
LAYER_URL_TTL_S = int(os.environ.get("LAYER_URL_TTL_S", 3600))

INTERVALS = {
    # interval -> (table, time column, longest range)
    "1s": ("readings_1s", "ts_local", timedelta(hours=6)),
    "1min": ("readings_1min", "minute_local", timedelta(days=31)),
}
QUALITY_MAX_RANGE = timedelta(days=31)
MANIFEST_PREFIX = "processed/_manifests/"
SATELLITE_SUCCESS_PREFIX = "curated/_success/satellite/"
PIXEL_LAYERS = ("lst", "ndvi", "ndbi")
STATIC_LAYERS = ("building_density",)
STATIC_LAYERS_PREFIX = "curated/satellite/layers/static/"

# /grid: only these cell_period columns may be asked for (never copied into SQL
# unchecked). Footprints and district borders are not columns here, so they
# cannot leak (D17).
GRID_VARS = (
    "n_scenes", "clear_obs_mean", "valid_pixel_ratio", "lst_mean", "lst_p90", "ndvi_mean",
    "ndbi_mean", "green_ratio", "builtup_pct", "building_count", "floors_mean", "far",
    "in_cover", "bldg_source", "air_temp_mean", "humidity_mean", "lux_mean", "db_spl_mean",
    "n_iot_minutes", "n_devices",
)
GRID_DEFAULT_VARS = ("lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct")
MATCH_TYPES = ("window", "all")

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
        # SigV4: presigned URLs of a regional bucket need it
        _s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
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

# --- satellite: /layers ----------------------------------------------------

def list_satellite_periods(s3=None) -> list[dict]:
    """Published satellite periods (scene / season), newest first, from the
    curated/_success/satellite/ markers (spec 51 §4)."""
    s3 = s3 or _s3_client()
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=os.environ["DATA_BUCKET"],
                                   Prefix=SATELLITE_SUCCESS_PREFIX):
        for obj in page.get("Contents", []):
            name = obj["Key"][len(SATELLITE_SUCCESS_PREFIX):]
            if not name.endswith(".json"):
                continue
            try:
                p = ss.parse_period(name[:-len(".json")])
            except ValueError:
                continue
            if p.period_type in ("scene", "season"):
                out.append({"period": p.period, "period_type": p.period_type,
                            "date_start": p.date_start.isoformat(),
                            "date_end": p.date_end.isoformat()})
    out.sort(key=lambda x: (x["date_start"], x["period"]), reverse=True)
    return out


def _latest_period(periods: list[dict]) -> str | None:
    """Newest season composite; if there is none yet, the newest scene."""
    for kind in ("season", "scene"):
        for p in periods:
            if p["period_type"] == kind:
                return p["period"]
    return None


def _check_period(value: str | None, allowed: tuple[str, ...]) -> str:
    """Validate a period string against its pattern (it ends up in a key or SQL)."""
    v = (value or "").strip()
    try:
        if allowed == ("scene", "season"):
            ok = ss.parse_period(v).period_type in allowed
        else:
            ok = curated.parse_merge_period(v).period_type in allowed
    except ValueError:
        ok = False
    if not ok:
        raise ApiError(400, f"bad period: {value!r}")
    return v


def _read_json(s3, key: str) -> dict | None:
    try:
        obj = s3.get_object(Bucket=os.environ["DATA_BUCKET"], Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(obj["Body"].read())


def _layer_entries(s3, prefix: str, names: tuple[str, ...]) -> list[dict]:
    out = []
    for name in names:
        meta = _read_json(s3, f"{prefix}{name}.json")
        if meta is None:
            continue
        url = s3.generate_presigned_url(
            "get_object", ExpiresIn=LAYER_URL_TTL_S,
            Params={"Bucket": os.environ["DATA_BUCKET"], "Key": f"{prefix}{name}.png"})
        out.append({"layer": name, "url": url, "url_expires_in_s": LAYER_URL_TTL_S,
                    "coordinates": meta.get("coordinates"), "legend": meta.get("legend")})
    return out


def get_layers(params: dict) -> dict:
    s3 = _s3_client()
    periods = list_satellite_periods(s3)
    period = params.get("period")
    if period:
        period = _check_period(period, ("scene", "season"))
        if not any(p["period"] == period for p in periods):
            raise ApiError(404, f"no published layers for {period}")
    else:
        period = _latest_period(periods)
        if period is None:
            raise ApiError(404, "no published satellite layers yet")
    return {
        "period": period,
        "period_type": ss.parse_period(period).period_type,
        "periods": periods,
        "layers": _layer_entries(s3, f"curated/satellite/layers/{period}/", PIXEL_LAYERS),
        "static_layers": _layer_entries(s3, STATIC_LAYERS_PREFIX, STATIC_LAYERS),
    }


# --- satellite: /grid ------------------------------------------------------

@lru_cache(maxsize=None)
def _corner(col: int, row: int) -> tuple[float, float]:
    """[lon, lat] of the north-west corner of cell (col, row); neighbours share corners."""
    x = grid.ORIGIN_X + col * grid.GRID_SIZE_M
    y = grid.ORIGIN_Y - row * grid.GRID_SIZE_M
    lat, lon = grid.xy_to_latlon(x, y)
    return round(lon, 6), round(lat, 6)


def cell_polygon(cell_id: str) -> dict:
    c = grid.parse_cell_id(cell_id)
    nw, ne = _corner(c.col, c.row), _corner(c.col + 1, c.row)
    se, sw = _corner(c.col + 1, c.row + 1), _corner(c.col, c.row + 1)
    return {"type": "Polygon", "coordinates": [[list(nw), list(ne), list(se), list(sw), list(nw)]]}


def _grid_period(params: dict) -> str:
    period = params.get("period")
    if period:
        return _check_period(period, ("scene", "season", "month"))
    latest = _latest_period(list_satellite_periods())
    if latest is None:
        raise ApiError(404, "no published satellite data yet")
    return latest


def _parse_vars(value: str | None) -> list[str]:
    if not value or not value.strip():
        return list(GRID_DEFAULT_VARS)
    names = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in names if v not in GRID_VARS]
    if bad or not names:
        raise ApiError(400, f"unknown vars: {', '.join(bad)}; allowed: {', '.join(GRID_VARS)}")
    return list(dict.fromkeys(names))


def get_grid(params: dict) -> dict:
    match = params.get("match") or "all"
    if match not in MATCH_TYPES:
        raise ApiError(400, "match must be window or all")
    names = _parse_vars(params.get("vars"))
    period = _grid_period(params)
    sql = (f"SELECT cell_id, {', '.join(names)} FROM cell_period "
           f"WHERE period = '{period}' AND match_type = '{match}' ORDER BY cell_id")
    columns, rows = run_query(sql)
    if not rows:
        raise ApiError(404, f"no grid for {period} ({match})")
    features = []
    for r in rows:
        props = dict(zip(columns, r))
        features.append({"type": "Feature", "id": props["cell_id"],
                         "geometry": cell_polygon(props["cell_id"]), "properties": props})
    return {"type": "FeatureCollection", "period": period, "match": match,
            "vars": names, "features": features}


def get_grid_cell(params: dict) -> dict:
    cell_id = (params.get("cell_id") or "").strip()
    try:
        grid.parse_cell_id(cell_id)
    except ValueError:
        raise ApiError(400, f"bad cell_id: {cell_id!r}") from None
    period = _grid_period(params)
    cols = ", ".join(["match_type"] + [c for c in curated.COLUMN_NAMES[3:] if c != "merged_at"])
    sql = (f"SELECT {cols} FROM cell_period "
           f"WHERE cell_id = '{cell_id}' AND period = '{period}' ORDER BY match_type")
    columns, rows = run_query(sql)
    if not rows:
        raise ApiError(404, f"no data for {cell_id} in {period}")
    by_match = {}
    for r in rows:
        row = dict(zip(columns, r))
        by_match[row.pop("match_type")] = row
    return {"cell_id": cell_id, "period": period, "geometry": cell_polygon(cell_id),
            "match": by_match}


ROUTES = {
    "/devices": get_devices,
    "/latest": get_latest,
    "/readings": get_readings,
    "/quality": get_quality,
    "/layers": get_layers,
    "/grid": get_grid,
}
GRID_CELL_PREFIX = "/grid/"


def _cache_key(path: str, params: dict) -> str:
    return path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def handler(event, context=None):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = event.get("rawPath") or http.get("path") or "/"
    params = event.get("queryStringParameters") or {}

    path_key = path.rstrip("/") or "/"
    fn = ROUTES.get(path_key)
    if fn is None and path_key.startswith(GRID_CELL_PREFIX):
        fn = get_grid_cell
        params = {**params, "cell_id": path_key[len(GRID_CELL_PREFIX):]}
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
