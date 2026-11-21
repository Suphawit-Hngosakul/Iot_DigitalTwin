import csv
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from common import schema as s

FIXTURES = Path(__file__).with_name("fixtures")
FIRMWARE_SD_LOGGER = Path(__file__).resolve().parents[3] / "iot/firmware/main/sd_logger.h"


def read_fixture(name):
    with open(FIXTURES / name, newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def good_raw(**overrides):
    _, rows = read_fixture("good.csv")
    raw = dict(rows[0])
    raw.update(overrides)
    return raw


# ── header ───────────────────────────────────────────────────────────────────

def test_header_ok():
    assert s.check_header(s.CSV_COLUMNS) == []


def test_header_matches_firmware_source():
    if not FIRMWARE_SD_LOGGER.exists():
        pytest.skip("firmware not in this checkout")
    src = FIRMWARE_SD_LOGGER.read_text()
    # #define CSV_HEADER "Device_ID,..." \  (string literals joined over lines)
    start = src.index("#define CSV_HEADER")
    end = src.index("\n\n", start)
    header = "".join(part for part in src[start:end].split('"')[1::2])
    assert tuple(header.split(",")) == s.CSV_COLUMNS


def test_header_missing_column():
    cols = [c for c in s.CSV_COLUMNS if c != "Lux"]
    assert s.check_header(cols) == ["missing columns: Lux"]


def test_header_extra_column():
    assert s.check_header([*s.CSV_COLUMNS, "Foo"]) == ["unexpected columns: Foo"]


def test_header_swapped():
    cols = list(s.CSV_COLUMNS)
    cols[5], cols[6] = cols[6], cols[5]
    assert s.check_header(cols) == ["columns out of order"]


def test_header_fixture():
    fieldnames, _ = read_fixture("good.csv")
    assert s.check_header(fieldnames) == []


# ── rows ─────────────────────────────────────────────────────────────────────

def test_good_row_has_no_flags():
    row = s.parse_row(good_raw())
    assert row["dB_SPL"] == 62.40 and row["GPS_Valid"] is True
    assert row["ts_local"] == datetime(2026, 11, 16, 8, 30, 0)
    assert s.check_row(row) == set()


def test_spl_out_of_range():
    row = s.parse_row(good_raw(dB_SPL="125.00"))
    assert "SPL_OOR" in s.check_row(row)


def test_blank_values_with_valid_zero_only_get_device_flags():
    _, rows = read_fixture("good.csv")
    row = s.parse_row(rows[2])
    assert row["dB_SPL"] is None and row["Temp_C"] is None
    assert s.check_row(row) == {"SPL_ERR", "DHT_ERR", "GPS_MISSING"}


def test_gps_poor_from_hdop():
    _, rows = read_fixture("good.csv")
    assert s.check_row(s.parse_row(rows[3])) == {"GPS_POOR"}


def test_no_rtc_numeric_and_untrusted():
    _, rows = read_fixture("no_rtc.csv")
    for raw in rows:
        flags = s.check_row(s.parse_row(raw))
        assert "NO_RTC" in flags and s.should_drop(flags)
        assert s.raw_partition(raw) == ("unknown", "unknown")


def test_old_year_with_time_valid_is_no_rtc():
    row = s.parse_row(good_raw(DateTime="2000-01-01 00:00:05"))
    assert "NO_RTC" in s.check_row(row)


def test_out_of_batch_hour():
    row = s.parse_row(good_raw())
    assert "OUT_OF_BATCH_HOUR" not in s.check_row(row, datetime(2026, 11, 16, 8))
    assert "OUT_OF_BATCH_HOUR" in s.check_row(row, datetime(2026, 11, 16, 9))


def test_bad_device_and_unknown_flag():
    row = s.parse_row(good_raw(Device_ID="walk 01", Device_Type="Car", Flags="WHAT"))
    flags = s.check_row(row)
    assert {"BAD_DEVICE_ID", "BAD_DEVICE_TYPE", "UNKNOWN_FLAG"} <= flags


@pytest.mark.parametrize("col,value", [("Session", "x"), ("dB_SPL", "abc"), ("GPS_Valid", "2"), ("Record_ID", "")])
def test_parse_error(col, value):
    with pytest.raises(s.RowError):
        s.parse_row(good_raw(**{col: value}))


# ── clean_row ────────────────────────────────────────────────────────────────

def test_clean_row_nulls_and_drops_flag_columns():
    _, rows = read_fixture("good.csv")
    row = s.parse_row(rows[2])
    out = s.clean_row(row, s.check_row(row))
    assert out["temp_c"] is None and out["humidity_pct"] is None
    assert out["db_spl"] is None and out["lat"] is None
    assert out["lux"] == 10241.0
    assert not any(k.endswith("_valid") or k == "flags" for k in out)
    assert list(out) == [name for name, _ in s.READINGS_1S]


def test_clean_row_gps_poor_nulls_position():
    _, rows = read_fixture("good.csv")
    row = s.parse_row(rows[3])
    out = s.clean_row(row, s.check_row(row))
    assert out["lat"] is None and out["lon"] is None and out["hdop"] == 6.5
    assert out["db_spl"] == 64.0


def test_clean_row_temp_oor_keeps_humidity():
    row = s.parse_row(good_raw(Temp_C="95.0"))
    out = s.clean_row(row, s.check_row(row))
    assert out["temp_c"] is None and out["humidity_pct"] == 65.4


def test_clean_row_utc():
    row = s.parse_row(good_raw())
    out = s.clean_row(row, s.check_row(row))
    assert out["ts_utc"] == datetime(2026, 11, 16, 1, 30, tzinfo=timezone.utc)


def test_clean_row_refuses_dropped_row():
    _, rows = read_fixture("no_rtc.csv")
    row = s.parse_row(rows[0])
    with pytest.raises(ValueError):
        s.clean_row(row, s.check_row(row))


# ── batch_id / S3 keys ───────────────────────────────────────────────────────

def test_batch_id_roundtrip():
    b = s.make_batch_id("IOTW-A001", "2026-11-16", 8)
    assert b == "IOTW-A001__2026-11-16__08"
    p = s.parse_batch_id(b)
    assert (p.device_id, p.date, p.hour) == ("IOTW-A001", date(2026, 11, 16), 8)
    assert str(p) == b and p.start_local == datetime(2026, 11, 16, 8)


@pytest.mark.parametrize("bad", [
    "IOTW-A001__2026-11-16__24",
    "IOTW-A001__2026-13-01__08",
    "IOTW-A001_2026-11-16_08",
    "IOTW A001__2026-11-16__08",
    "__2026-11-16__08",
])
def test_batch_id_bad(bad):
    with pytest.raises(ValueError):
        s.parse_batch_id(bad)


def test_device_id_cannot_hold_separator():
    with pytest.raises(ValueError):
        s.make_batch_id("IOTW__A001", "2026-11-16", 8)


def test_s3_keys():
    assert s.s3_key_raw("IOTW-A001", "2026-11-16", 8, "12-1-300") == (
        "raw/csv/device_id=IOTW-A001/date=2026-11-16/hour=08/part-12-1-300.csv")
    assert s.s3_key_raw("IOTW-A001", None, None, "12-1-300") == (
        "raw/csv/device_id=IOTW-A001/date=unknown/hour=unknown/part-12-1-300.csv")
    b = "IOTW-A001__2026-11-16__08"
    assert s.s3_key_table("readings_1s", b) == (
        "trusted/iot_readings_1s/device_id=IOTW-A001/date=2026-11-16/IOTW-A001__2026-11-16__08.parquet")
    assert s.s3_key_table("readings_1min", b) == (
        "curated/readings_1min/device_id=IOTW-A001/date=2026-11-16/IOTW-A001__2026-11-16__08.parquet")
    assert s.s3_key_table("quality", b).startswith("trusted/iot_quality/device_id=IOTW-A001/")
    assert s.s3_key_report(b) == f"validation/iot/{b}/report.json"
    assert s.s3_prefix_quarantine(b) == f"quarantine/batch/iot/{b}/"
    assert s.s3_key_quarantine_rows(b) == f"quarantine/rows/iot/{b}/rows.csv"
    assert s.s3_key_quarantine_duplicate(b) == f"quarantine/duplicate/iot/{b}/rows.csv"
    assert s.s3_key_manifest(b) == f"curated/_success/iot/{b}.json"
    assert s.MANIFEST_PREFIX == "curated/_success/iot/"
    with pytest.raises(ValueError):
        s.s3_key_raw("IOTW-A001", "2026-11-16", 8, "bad")
    with pytest.raises(ValueError):
        s.s3_key_table("nope", b)


def test_raw_partition_from_raw_dict():
    assert s.raw_partition(good_raw()) == ("2026-11-16", "08")


# ── Parquet tables ───────────────────────────────────────────────────────────

def test_tables_have_no_partition_keys_or_flag_columns():
    keys = {k for k, _ in s.PARTITION_KEYS}
    for cols in s.TABLES.values():
        names = {n for n, _ in cols}
        assert not names & keys
        assert not any(n.endswith("_valid") or n == "flags" for n in names)


def test_arrow_schema():
    pytest.importorskip("pyarrow")
    for t in s.TABLES:
        assert s.arrow_schema(t).names == [n for n, _ in s.TABLES[t]]


# ── devices.csv ──────────────────────────────────────────────────────────────

def test_devices_csv_loads():
    devices = s.load_devices()
    assert [d.device_id for d in devices] == ["IOTW-A001", "IOTW-A002"]
    assert all(d.device_type == "Mobile" for d in devices)


def write_registry(tmp_path, rows):
    p = tmp_path / "devices.csv"
    p.write_text(",".join(s.DEVICES_COLUMNS) + "\n" + "\n".join(rows) + "\n")
    return p


def test_lookup_device_moves_mid_day(tmp_path):
    p = write_registry(tmp_path, [
        "ST-01,Stationary,14.07,100.60,site A,2026-11-01,2026-11-20 12:00:00,",
        "ST-01,Stationary,14.08,100.61,site B,2026-11-20 12:00:00,,moved",
    ])
    devices = s.load_devices(p)
    assert s.lookup_device(devices, "ST-01", datetime(2026, 11, 20, 11, 59)).site_name == "site A"
    assert s.lookup_device(devices, "ST-01", datetime(2026, 11, 20, 12, 0)).site_name == "site B"
    assert s.lookup_device(devices, "ST-01", datetime(2026, 10, 31)) is None
    assert s.lookup_device(devices, "ST-99", datetime(2026, 11, 20)) is None


def test_station_without_coords_is_rejected(tmp_path):
    p = write_registry(tmp_path, ["ST-01,Stationary,,,site A,2026-11-01,,"])
    with pytest.raises(ValueError, match="line 2"):
        s.load_devices(p)
