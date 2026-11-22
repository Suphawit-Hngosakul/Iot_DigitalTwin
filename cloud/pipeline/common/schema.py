"""Central IoT data schema (issue #2).

Single source of truth for the CSV the device writes to SD / uploads, the
value ranges, the quality flags, the Parquet tables the pipeline publishes,
the S3 key layout and the device registry. Ingest, pipeline and the
API import from here; nobody re-types a column list.

The CSV layout mirrors iot/firmware/main/sd_logger.cpp (createCSVLog /
appendCSVRow) and the ranges mirror iot/firmware/main/config.h. If the
firmware changes, change this file in the same commit.

Satellite schema lives in specs 50/51, not here.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

# =============================================================================
#  Device CSV (raw)
# =============================================================================

CSV_COLUMNS: tuple[str, ...] = (
    "Device_ID", "Device_Type", "Session", "Record_ID",
    "DateTime", "dBA", "dB_SPL", "Lux", "Temp_C", "Humidity_pct", "Latitude",
    "Longitude", "Altitude_m", "Speed_kmh", "Satellites", "HDOP",
    "GPS_Valid", "Lux_Valid", "DHT_Valid",
    "Time_Valid", "SPL_Valid", "Flags",
)

# Column -> parsed Python type. Empty string -> None for every column.
_INT_COLS = {"Session", "Record_ID", "Satellites"}
_FLOAT_COLS = {
    "dBA", "dB_SPL", "Lux", "Temp_C", "Humidity_pct", "Latitude", "Longitude",
    "Altitude_m", "Speed_kmh", "HDOP",
}
_BOOL_COLS = {"GPS_Valid", "Lux_Valid", "DHT_Valid", "Time_Valid", "SPL_Valid"}
# Never blank in a well-formed row.
REQUIRED_COLS = (
    "Device_ID", "Device_Type", "Session", "Record_ID", "DateTime",
    "Satellites", "HDOP", *sorted(_BOOL_COLS), "Flags",
)

DEVICE_TYPES = ("Mobile", "Stationary")   # deviceTypeName() in settings.cpp

# A-Z a-z 0-9 - _, up to 16 chars (SETTINGS_DEVID_LEN 17 incl. NUL).
# "__" is reserved as the batch_id separator.
DEVICE_ID_RE = re.compile(r"^(?!.*__)[A-Za-z0-9_-]{1,16}$")

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"        # Thai local time, no offset
TIME_MIN_YEAR = 2025                      # config.h: below this -> TIME_INVALID
LOCAL_TZ = timezone(timedelta(hours=7), "Asia/Bangkok")

# Physical sensor limits (config.h). Outside -> *_OOR and the value is nulled.
# Primary sound value is dB_SPL (D19); dBA is secondary.
RANGES: dict[str, tuple[float, float]] = {
    "dB_SPL": (30.0, 120.0),        # SPL_MIN_DB / SPL_MAX_DB (INMP441)
    "dBA": (30.0, 120.0),
    "Temp_C": (-40.0, 80.0),        # DHT22
    "Humidity_pct": (0.0, 100.0),
    "Latitude": (-90.0, 90.0),
    "Longitude": (-180.0, 180.0),
}
LUX_MIN = 0.0                       # upper limit depends on per-device calibration
GPS_MAX_HDOP = 5.0                  # above -> GPS_POOR

# Codes the firmware writes into Flags (';'-separated, or "OK").
DEVICE_FLAGS = frozenset({
    "TIME_INVALID", "SPL_ERR", "SPL_OOR", "LUX_ERR", "LUX_OOR", "DHT_ERR",
    "TEMP_OOR", "HUMID_OOR", "GPS_MISSING", "GPS_POOR",
})
# Codes only the pipeline adds.
PIPELINE_FLAGS = frozenset({
    "NO_RTC",             # DateTime not a trusted Thai time (numeric, or Time_Valid=0)
    "OUT_OF_BATCH_HOUR",  # row time outside the batch's hour
    "TIME_JUMP",          # time goes backwards / jumps within a session (ValidateBatch)
    "DUPLICATE",          # same (Device_ID, Session, Record_ID) seen before (ValidateBatch)
    "PARSE_ERROR",        # a value could not be parsed
    "BAD_DEVICE_ID",
    "BAD_DEVICE_TYPE",
    "UNKNOWN_FLAG",       # Flags holds a code not in DEVICE_FLAGS
})
# Rows carrying any of these are removed by TransformBatch (and counted in quality).
DROP_FLAGS = frozenset({
    "NO_RTC", "OUT_OF_BATCH_HOUR", "DUPLICATE", "PARSE_ERROR",
    "BAD_DEVICE_ID", "BAD_DEVICE_TYPE",
})


class RowError(ValueError):
    """A CSV row cannot be parsed into typed values."""


def check_header(cols: Iterable[str]) -> list[str]:
    """Problems with a CSV header; empty list = header is exactly CSV_COLUMNS."""
    cols = [c.strip() for c in cols]
    if cols == list(CSV_COLUMNS):
        return []
    problems: list[str] = []
    missing = [c for c in CSV_COLUMNS if c not in cols]
    extra = [c for c in cols if c not in CSV_COLUMNS]
    if missing:
        problems.append(f"missing columns: {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected columns: {', '.join(extra)}")
    if not missing and not extra:
        problems.append("columns out of order")
    return problems


def parse_datetime(value: str | None) -> datetime | None:
    """Thai-local naive datetime, or None when it is not a usable clock time.

    The firmware writes seconds-since-boot (a bare number) when there is no RTC.
    """
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return None
    try:
        ts = datetime.strptime(value, DATETIME_FMT)
    except ValueError:
        return None
    return ts if ts.year >= TIME_MIN_YEAR else None


def to_utc(ts_local: datetime) -> datetime:
    """Thai-local naive datetime -> timezone-aware UTC."""
    return ts_local.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)


def parse_row(raw: Mapping[str, str | None]) -> dict:
    """Typed row keyed by CSV column. Blank -> None. Raises RowError."""
    row: dict = {}
    for col in CSV_COLUMNS:
        v = raw.get(col)
        v = v.strip() if isinstance(v, str) else v
        if v in (None, ""):
            if col in REQUIRED_COLS:
                raise RowError(f"{col} is blank")
            row[col] = None
            continue
        try:
            if col in _INT_COLS:
                row[col] = int(v)
            elif col in _FLOAT_COLS:
                row[col] = float(v)
            elif col in _BOOL_COLS:
                if v not in ("0", "1"):
                    raise ValueError(v)
                row[col] = v == "1"
            else:
                row[col] = v
        except ValueError as e:
            raise RowError(f"{col}={v!r} is not valid") from e
    row["ts_local"] = parse_datetime(row["DateTime"]) if row["Time_Valid"] else None
    return row


def device_flags(flags_value: str | None) -> set[str]:
    """Codes in the Flags column ("OK" -> empty set)."""
    if not flags_value or flags_value.strip() == "OK":
        return set()
    return {f.strip() for f in flags_value.split(";") if f.strip()}


def _out_of_range(col: str, v: float | None) -> bool:
    lo, hi = RANGES[col]
    return v is not None and not (lo <= v <= hi)


def check_row(row: dict, batch_hour: datetime | None = None) -> set[str]:
    """All quality flags for a parsed row: the device's own plus the pipeline's.

    `row` comes from parse_row. `batch_hour` (Thai local, minute=0) adds
    OUT_OF_BATCH_HOUR. DUPLICATE and TIME_JUMP need the whole batch, so
    ValidateBatch adds them.
    """
    dev = device_flags(row["Flags"])
    flags = {f for f in dev if f in DEVICE_FLAGS}
    if dev - DEVICE_FLAGS:
        flags.add("UNKNOWN_FLAG")

    if not DEVICE_ID_RE.match(row["Device_ID"]):
        flags.add("BAD_DEVICE_ID")
    if row["Device_Type"] not in DEVICE_TYPES:
        flags.add("BAD_DEVICE_TYPE")

    ts = row.get("ts_local")
    if ts is None:
        flags.add("NO_RTC")
    elif batch_hour is not None and not (
        batch_hour <= ts < batch_hour + timedelta(hours=1)
    ):
        flags.add("OUT_OF_BATCH_HOUR")

    # Re-check what the device should already have flagged, so a row is judged
    # the same way even if firmware thresholds drift.
    if not row["SPL_Valid"]:
        flags.add("SPL_ERR")
    elif _out_of_range("dB_SPL", row["dB_SPL"]) or _out_of_range("dBA", row["dBA"]):
        flags.add("SPL_OOR")
    if not row["Lux_Valid"]:
        flags.add("LUX_ERR")
    elif row["Lux"] is not None and row["Lux"] < LUX_MIN:
        flags.add("LUX_OOR")
    if not row["DHT_Valid"]:
        flags.add("DHT_ERR")
    else:
        if _out_of_range("Temp_C", row["Temp_C"]):
            flags.add("TEMP_OOR")
        if _out_of_range("Humidity_pct", row["Humidity_pct"]):
            flags.add("HUMID_OOR")
    if not row["GPS_Valid"]:
        flags.add("GPS_MISSING")
    elif (
        row["HDOP"] > GPS_MAX_HDOP
        or row["Latitude"] is None or row["Longitude"] is None
        or _out_of_range("Latitude", row["Latitude"])
        or _out_of_range("Longitude", row["Longitude"])
    ):
        flags.add("GPS_POOR")
    if not row["Time_Valid"]:
        flags.add("TIME_INVALID")
    return flags


def should_drop(flags: Iterable[str]) -> bool:
    return not DROP_FLAGS.isdisjoint(flags)


def clean_row(row: dict, flags: Iterable[str]) -> dict:
    """One readings_1s record: values nulled per flag, no *_Valid / Flags.

    Partition keys (device_id, date) are not in the record; they live in the
    S3 path. Caller must drop rows where should_drop(flags) is true.
    """
    flags = set(flags)
    if should_drop(flags):
        raise ValueError(f"row must be dropped: {sorted(flags & DROP_FLAGS)}")
    spl_bad = bool(flags & {"SPL_ERR", "SPL_OOR"})
    lux_bad = bool(flags & {"LUX_ERR", "LUX_OOR"})
    dht_bad = "DHT_ERR" in flags
    gps_bad = bool(flags & {"GPS_MISSING", "GPS_POOR"})

    def keep(value, bad: bool):
        return None if bad else value

    ts_local = row["ts_local"]
    return {
        "device_type": row["Device_Type"],
        "session": row["Session"],
        "record_id": row["Record_ID"],
        "ts_local": ts_local,
        "ts_utc": to_utc(ts_local),
        "db_spl": keep(row["dB_SPL"], spl_bad),
        "dba": keep(row["dBA"], spl_bad),
        "lux": keep(row["Lux"], lux_bad),
        "temp_c": keep(row["Temp_C"], dht_bad or "TEMP_OOR" in flags),
        "humidity_pct": keep(row["Humidity_pct"], dht_bad or "HUMID_OOR" in flags),
        "lat": keep(row["Latitude"], gps_bad),
        "lon": keep(row["Longitude"], gps_bad),
        "alt_m": keep(row["Altitude_m"], gps_bad),
        "speed_kmh": keep(row["Speed_kmh"], gps_bad),
        "satellites": row["Satellites"],
        "hdop": row["HDOP"],
    }


# =============================================================================
#  Parquet tables (trusted / curated)
# =============================================================================
# Type names are Glue/Athena types. Partition keys are NOT stored in the
# files (Athena rejects a column that repeats a partition key).

PARTITION_KEYS = (("device_id", "string"), ("date", "string"))

READINGS_1S: tuple[tuple[str, str], ...] = (
    ("device_type", "string"),
    ("session", "bigint"),
    ("record_id", "bigint"),
    ("ts_local", "timestamp"),
    ("ts_utc", "timestamp"),
    ("db_spl", "double"),
    ("dba", "double"),
    ("lux", "double"),
    ("temp_c", "double"),
    ("humidity_pct", "double"),
    ("lat", "double"),
    ("lon", "double"),
    ("alt_m", "double"),
    ("speed_kmh", "double"),
    ("satellites", "int"),
    ("hdop", "double"),
)

# Sound per minute = plain mean/max/min of the 1 s values (decided: no Leq).
READINGS_1MIN: tuple[tuple[str, str], ...] = (
    ("minute_local", "timestamp"),
    ("minute_utc", "timestamp"),
    ("n_rows", "int"),
    ("db_spl_mean", "double"),
    ("db_spl_max", "double"),
    ("db_spl_min", "double"),
    ("dba_mean", "double"),
    ("temp_c_mean", "double"),
    ("humidity_pct_mean", "double"),
    ("lux_mean", "double"),
    ("lat", "double"),
    ("lon", "double"),
    ("cell_id", "string"),          # filled by #29 (grid.py)
)

QUALITY: tuple[tuple[str, str], ...] = (
    ("batch_id", "string"),
    ("session", "bigint"),
    ("expected_rows", "bigint"),
    ("received_rows", "bigint"),
    ("missing_rows", "bigint"),
    ("duplicate_rows", "bigint"),
    ("invalid_rows", "bigint"),
    ("loss_rate", "double"),
    ("flag_counts", "string"),      # JSON {"GPS_MISSING": 12, ...}
)

TABLES = {
    "readings_1s": READINGS_1S,
    "readings_1min": READINGS_1MIN,
    "quality": QUALITY,
}


def arrow_schema(table: str):
    """pyarrow.Schema for an IoT table (pyarrow imported lazily)."""
    import pyarrow as pa

    types = {
        "string": pa.string(),
        "bigint": pa.int64(),
        "int": pa.int32(),
        "double": pa.float64(),
        "timestamp": pa.timestamp("s"),
    }
    return pa.schema([(name, types[t]) for name, t in TABLES[table]])


# =============================================================================
#  batch_id and S3 keys
# =============================================================================
# One batch = one device x one Thai-local hour (how ingest splits files).

BATCH_ID_RE = re.compile(
    r"^(?P<device_id>.+)__(?P<date>\d{4}-\d{2}-\d{2})__(?P<hour>\d{2})$"
)
CHUNK_ID_RE = re.compile(r"^\d+-\d+-\d+$")   # <session>-<first_record>-<last_record>
UNKNOWN = "unknown"


@dataclass(frozen=True)
class BatchId:
    device_id: str
    date: date
    hour: int

    def __str__(self) -> str:
        return make_batch_id(self.device_id, self.date, self.hour)

    @property
    def start_local(self) -> datetime:
        return datetime(self.date.year, self.date.month, self.date.day, self.hour)


def _check_device_id(device_id: str) -> None:
    if not DEVICE_ID_RE.match(device_id):
        raise ValueError(f"bad device_id: {device_id!r}")


def _as_date(d: date | str) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def make_batch_id(device_id: str, d: date | str, hour: int) -> str:
    _check_device_id(device_id)
    if not 0 <= hour <= 23:
        raise ValueError(f"bad hour: {hour}")
    return f"{device_id}__{_as_date(d).isoformat()}__{hour:02d}"


def parse_batch_id(batch_id: str) -> BatchId:
    m = BATCH_ID_RE.match(batch_id)
    if not m:
        raise ValueError(f"bad batch_id: {batch_id!r}")
    device_id = m["device_id"]
    _check_device_id(device_id)
    try:
        d = date.fromisoformat(m["date"])
    except ValueError as e:
        raise ValueError(f"bad batch_id date: {batch_id!r}") from e
    hour = int(m["hour"])
    if hour > 23:
        raise ValueError(f"bad batch_id hour: {batch_id!r}")
    return BatchId(device_id, d, hour)


def raw_partition(row: Mapping) -> tuple[str, str]:
    """(date, hour) folder for a raw row; ('unknown', 'unknown') without a trusted time.

    Accepts a parsed row (has ts_local) or a raw CSV dict.
    """
    if "ts_local" in row:
        ts = row["ts_local"]
    else:
        ts = parse_datetime(row.get("DateTime")) if row.get("Time_Valid") == "1" else None
    if ts is None:
        return UNKNOWN, UNKNOWN
    return ts.date().isoformat(), f"{ts.hour:02d}"


def s3_key_raw(device_id: str, d: date | str | None, hour: int | str | None,
               chunk_id: str) -> str:
    _check_device_id(device_id)
    if not CHUNK_ID_RE.match(chunk_id):
        raise ValueError(f"bad chunk_id: {chunk_id!r}")
    if d in (None, UNKNOWN) or hour in (None, UNKNOWN):
        d_s = h_s = UNKNOWN
    else:
        d_s = _as_date(d).isoformat()
        h = int(hour)
        if not 0 <= h <= 23:
            raise ValueError(f"bad hour: {hour}")
        h_s = f"{h:02d}"
    return f"raw/csv/device_id={device_id}/date={d_s}/hour={h_s}/part-{chunk_id}.csv"


def raw_prefix(batch_id: str) -> str:
    """S3 prefix holding every raw chunk of one batch (ends with '/')."""
    b = parse_batch_id(batch_id)
    return (f"raw/csv/device_id={b.device_id}/date={b.date.isoformat()}/"
            f"hour={b.hour:02d}/")


# Zone of every Parquet table (spec 01 §2 / spec 30 §8, D23): readings_1s and quality
# are the validated copy of the raw rows (trusted); readings_1min is derived and
# ready to use (curated).
TABLE_ZONE = {
    "readings_1s": "trusted/iot_readings_1s",
    "readings_1min": "curated/readings_1min",
    "quality": "trusted/iot_quality",
}
assert set(TABLE_ZONE) == set(TABLES)

# Publish marker written by VerifyBatch (CheckBatch, the API and curated-merge read it).
MANIFEST_PREFIX = "curated/_success/iot/"


def table_prefix(table: str) -> str:
    """S3 prefix of a table, no trailing slash (Glue locations use this)."""
    if table not in TABLE_ZONE:
        raise ValueError(f"unknown table: {table!r}")
    return TABLE_ZONE[table]


def s3_key_table(table: str, batch_id: str) -> str:
    b = parse_batch_id(batch_id)
    return (f"{table_prefix(table)}/device_id={b.device_id}/"
            f"date={b.date.isoformat()}/{batch_id}.parquet")


def s3_key_report(batch_id: str) -> str:
    parse_batch_id(batch_id)
    return f"validation/iot/{batch_id}/report.json"


def s3_prefix_quarantine(batch_id: str) -> str:
    """Whole batch failed validation: raw copies + reason.json."""
    parse_batch_id(batch_id)
    return f"quarantine/batch/iot/{batch_id}/"


def s3_key_quarantine_rows(batch_id: str) -> str:
    """Rows rejected inside a batch that passed (INVALID)."""
    parse_batch_id(batch_id)
    return f"quarantine/rows/iot/{batch_id}/rows.csv"


def s3_key_quarantine_duplicate(batch_id: str) -> str:
    parse_batch_id(batch_id)
    return f"quarantine/duplicate/iot/{batch_id}/rows.csv"


def s3_key_manifest(batch_id: str) -> str:
    parse_batch_id(batch_id)
    return f"{MANIFEST_PREFIX}{batch_id}.json"


# =============================================================================
#  Device registry (devices.csv)
# =============================================================================
# One row per device per placement. Moving a station = set valid_to on the old
# row and add a new one (never edit history). valid_from is inclusive,
# valid_to is exclusive; blank valid_to = still active. Dates or
# "YYYY-MM-DD HH:MM:SS" (Thai local) are both accepted.

DEVICES_CSV = Path(__file__).with_name("devices.csv")
DEVICES_COLUMNS = (
    "device_id", "device_type", "fixed_lat", "fixed_lon", "site_name",
    "valid_from", "valid_to", "note",
)


@dataclass(frozen=True)
class Device:
    device_id: str
    device_type: str
    fixed_lat: float | None
    fixed_lon: float | None
    site_name: str
    valid_from: datetime
    valid_to: datetime | None
    note: str

    def active_at(self, ts_local: datetime) -> bool:
        return self.valid_from <= ts_local and (
            self.valid_to is None or ts_local < self.valid_to
        )


def _registry_time(v: str) -> datetime:
    v = v.strip()
    return datetime.strptime(v, DATETIME_FMT) if " " in v else datetime.fromisoformat(v)


def load_devices(path: str | Path = DEVICES_CSV) -> list[Device]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != DEVICES_COLUMNS:
            raise ValueError(f"devices.csv header must be {','.join(DEVICES_COLUMNS)}")
        devices = []
        for i, r in enumerate(reader, start=2):
            try:
                _check_device_id(r["device_id"])
                if r["device_type"] not in DEVICE_TYPES:
                    raise ValueError(f"bad device_type {r['device_type']!r}")
                lat = float(r["fixed_lat"]) if r["fixed_lat"] else None
                lon = float(r["fixed_lon"]) if r["fixed_lon"] else None
                if r["device_type"] == "Stationary" and (lat is None or lon is None):
                    raise ValueError("Stationary needs fixed_lat/fixed_lon")
                devices.append(Device(
                    device_id=r["device_id"],
                    device_type=r["device_type"],
                    fixed_lat=lat,
                    fixed_lon=lon,
                    site_name=r["site_name"],
                    valid_from=_registry_time(r["valid_from"]),
                    valid_to=_registry_time(r["valid_to"]) if r["valid_to"] else None,
                    note=r["note"],
                ))
            except (ValueError, KeyError) as e:
                raise ValueError(f"devices.csv line {i}: {e}") from e
    return devices


def lookup_device(devices: Iterable[Device], device_id: str,
                  ts_local: datetime) -> Device | None:
    """Registry row for this device at this Thai-local time, or None."""
    for d in devices:
        if d.device_id == device_id and d.active_at(ts_local):
            return d
    return None
