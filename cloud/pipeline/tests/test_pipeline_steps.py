"""ValidateBatch (#13), QuarantineBatch (#14), TransformBatch (#15), VerifyBatch (#16)."""
import csv
import importlib.util
import io
import json
from datetime import datetime
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from common import batch_eval, grid, schema  # noqa: E402


def _load_function(name):
    path = Path(__file__).resolve().parents[1] / "functions" / name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"fn_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_fn = _load_function("check_batch")
validate_fn = _load_function("validate_batch")
quarantine_fn = _load_function("quarantine_batch")
transform_fn = _load_function("transform_batch")
verify_fn = _load_function("verify_batch")

BUCKET = "test-data"
BATCH = "IOTW-A001__2026-11-16__08"
FIXTURES = Path(__file__).with_name("fixtures")
NOW = datetime(2026, 11, 18, 2, 0)


def dev(device_id, device_type="Mobile", lat=None, lon=None, valid_from="2026-11-01", valid_to=None):
    return schema.Device(device_id, device_type, lat, lon, "", datetime.fromisoformat(valid_from),
                         datetime.fromisoformat(valid_to) if valid_to else None, "")


DEVICES = [dev("IOTW-A001"), dev("IOTW-A002")]


def good_rows():
    with open(FIXTURES / "good.csv", newline="") as f:
        return list(csv.DictReader(f))


def row(record_id, dt, **kw):
    r = dict(good_rows()[0])
    r.update(Record_ID=str(record_id), DateTime=dt, **kw)
    return r


def to_csv(rows, header=schema.CSV_COLUMNS) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(header), extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def put(s3, chunk_id, body, batch_id=BATCH):
    s3.put_object(Bucket=BUCKET, Key=schema.raw_prefix(batch_id) + f"part-{chunk_id}.csv", Body=body)


def checked(s3, batch_id=BATCH, mode="normal"):
    event = {"batch_id": batch_id, "mode": mode}
    return {**event, "check": check_fn.check(s3, BUCKET, batch_id, mode)}


def validated(s3, devices=DEVICES, batch_id=BATCH):
    ev = checked(s3, batch_id)
    return {**ev, "validation": validate_fn.validate(s3, BUCKET, ev, devices, now=NOW)}


def run_all(s3, devices=DEVICES, batch_id=BATCH):
    ev = validated(s3, devices, batch_id)
    assert ev["validation"]["status"] == "PASS", ev["validation"]["reason"]
    ev["transform"] = transform_fn.transform(s3, BUCKET, ev, devices)
    ev["verify"] = verify_fn.verify(s3, BUCKET, ev, now=NOW)
    return ev


def read_table(s3, table, batch_id=BATCH):
    body = s3.get_object(Bucket=BUCKET, Key=schema.s3_key_table(table, batch_id))["Body"].read()
    return pq.read_table(io.BytesIO(body)).to_pylist()


def report(s3, batch_id=BATCH):
    return json.loads(s3.get_object(Bucket=BUCKET, Key=schema.s3_key_report(batch_id))["Body"].read())


# ── ValidateBatch ────────────────────────────────────────────────────────────

def test_good_batch_passes_and_writes_report(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    v = validated(s3)["validation"]
    assert v["status"] == "PASS" and v["reason"] is None
    assert v["raw_rows"] == 4 and v["kept_rows"] == 4
    rep = report(s3)
    assert rep["status"] == "PASS"
    assert rep["flag_counts"]["GPS_POOR"] == 1
    assert rep["sessions"][0]["loss_rate"] == 0.0


def test_bad_header_fails(s3):
    body = (FIXTURES / "good.csv").read_text().replace("dBA,dB_SPL", "dB_SPL,dBA", 1).encode()
    put(s3, "12-1-4", body)
    v = validated(s3)["validation"]
    assert v["status"] == "FAIL"
    assert "BAD_HEADER" in v["reason"]


def test_duplicates_counted_not_failed(s3):
    body = (FIXTURES / "good.csv").read_bytes()
    put(s3, "12-1-4", body)
    put(s3, "12-1-4b".replace("b", ""), body)  # same key -> overwritten, no dup
    put(s3, "12-1-3", to_csv(good_rows()[:3]))  # overlapping chunk -> 3 duplicates
    v = validated(s3)["validation"]
    assert v["status"] == "PASS"
    rep = report(s3)
    assert rep["duplicate_rows"] == 3 and rep["kept_rows"] == 4


def test_missing_record_ids(s3):
    rows = [row(1, "2026-11-16 08:00:01"), row(2, "2026-11-16 08:00:02"), row(5, "2026-11-16 08:00:05")]
    put(s3, "12-1-5", to_csv(rows))
    validated(s3)
    sess = report(s3)["sessions"][0]
    assert sess["expected_rows"] == 5 and sess["missing_rows"] == 2
    assert sess["missing_ranges"] == [[3, 4]]
    assert sess["loss_rate"] == pytest.approx(0.4)


def test_too_many_invalid_fails(s3):
    rows = [row(1, "2026-11-16 08:00:01"),
            row(2, "12345", Time_Valid="0"),
            row(3, "2026-11-16 09:00:00")]          # OUT_OF_BATCH_HOUR
    put(s3, "12-1-3", to_csv(rows))
    v = validated(s3)["validation"]
    assert v["status"] == "FAIL" and "TOO_MANY_INVALID" in v["reason"]
    assert report(s3)["flag_counts"]["NO_RTC"] == 1


def test_device_id_not_matching_path_fails(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes(), batch_id="IOTW-A002__2026-11-16__08")
    v = validated(s3, batch_id="IOTW-A002__2026-11-16__08")["validation"]
    assert "DEVICE_ID_MISMATCH" in v["reason"]


def test_unknown_device_fails(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    v = validated(s3, devices=[dev("IOTW-A002")])["validation"]
    assert "UNKNOWN_DEVICE" in v["reason"]


def test_device_type_mismatch_fails(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    v = validated(s3, devices=[dev("IOTW-A001", "Stationary", 14.0, 100.6)])["validation"]
    assert "DEVICE_TYPE_MISMATCH" in v["reason"]


def test_station_outside_registry_fails(s3):
    rows = [row(i, f"2026-11-16 08:30:0{i}", Device_Type="Stationary") for i in range(1, 5)]
    put(s3, "12-1-4", to_csv(rows))
    devices = [dev("IOTW-A001", "Stationary", 14.0, 100.6, valid_from="2026-11-16 08:30:03")]
    v = validated(s3, devices=devices)["validation"]
    assert "UNKNOWN_STATION_SITE" in v["reason"]


def test_time_jump_flagged_not_dropped(s3):
    rows = [row(1, "2026-11-16 08:00:01"), row(2, "2026-11-16 08:00:02"),
            row(3, "2026-11-16 08:00:30")]
    put(s3, "12-1-3", to_csv(rows))
    v = validated(s3)["validation"]
    assert v["status"] == "PASS" and v["kept_rows"] == 3
    assert report(s3)["flag_counts"]["TIME_JUMP"] == 1


def test_parse_error_row_is_invalid(s3):
    rows = [row(1, "2026-11-16 08:00:01"), row(2, "2026-11-16 08:00:02", Session="x")]
    rows += [row(i, f"2026-11-16 08:00:{i:02d}") for i in range(3, 10)]
    put(s3, "12-1-9", to_csv(rows))
    rep = (validated(s3), report(s3))[1]
    assert rep["invalid_rows"] == 1 and rep["bad_row_samples"][0]["flags"] == ["PARSE_ERROR"]


# ── QuarantineBatch ──────────────────────────────────────────────────────────

def test_quarantine_copies_files_and_reason(s3):
    body = (FIXTURES / "good.csv").read_text().replace("Flags", "Flagz").encode()
    put(s3, "12-1-4", body)
    ev = validated(s3)
    out = quarantine_fn.quarantine(s3, BUCKET, ev, now=NOW)
    prefix = schema.s3_prefix_quarantine(BATCH)
    assert out == {"prefix": prefix, "files": 1, "unpublished": []}
    assert s3.get_object(Bucket=BUCKET, Key=prefix + "part-12-1-4.csv")["Body"].read() == body
    reason = json.loads(s3.get_object(Bucket=BUCKET, Key=prefix + "reason.json")["Body"].read())
    assert "BAD_HEADER" in reason["reason"]
    # raw untouched
    assert s3.get_object(Bucket=BUCKET, Key=schema.raw_prefix(BATCH) + "part-12-1-4.csv")


def test_quarantined_batch_is_skipped_until_files_change(s3):
    body = (FIXTURES / "good.csv").read_text().replace("Flags", "Flagz").encode()
    put(s3, "12-1-4", body)
    quarantine_fn.quarantine(s3, BUCKET, validated(s3), now=NOW)
    assert checked(s3)["check"]["status"] == check_fn.ALREADY_QUARANTINED
    assert checked(s3, mode="force")["check"]["status"] == check_fn.NEW
    put(s3, "12-5-8", to_csv(good_rows()))                # new chunk -> look again
    assert checked(s3)["check"]["status"] == check_fn.NEW


def test_published_then_failing_batch_is_unpublished(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    run_all(s3)
    assert checked(s3)["check"]["status"] == check_fn.ALREADY_PROCESSED
    put(s3, "12-5-8", to_csv(good_rows()).replace(b"Flags", b"Flagz"))   # backfill with a bad header
    ev = validated(s3)
    assert ev["check"]["status"] == check_fn.CHANGED and ev["validation"]["status"] == "FAIL"
    out = quarantine_fn.quarantine(s3, BUCKET, ev, now=NOW)
    assert schema.s3_key_manifest(BATCH) in out["unpublished"]
    assert schema.s3_key_table("readings_1s", BATCH) in out["unpublished"]
    for prefix in ("trusted/", "curated/"):
        assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    assert checked(s3)["check"]["status"] == check_fn.ALREADY_QUARANTINED


# ── TransformBatch ───────────────────────────────────────────────────────────

def test_transform_tables(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    put(s3, "12-1-3", to_csv(good_rows()[:3]))         # duplicates
    ev = run_all(s3)
    t = ev["transform"]
    assert (t["raw_rows"], t["kept_rows"], t["duplicate_rows"], t["invalid_rows"]) == (7, 4, 3, 0)

    r1s = read_table(s3, "readings_1s")
    assert [r["record_id"] for r in r1s] == [1, 2, 3, 4]
    assert r1s[2]["db_spl"] is None and r1s[2]["temp_c"] is None      # SPL_ERR, DHT_ERR
    assert r1s[3]["lat"] is None                                      # GPS_POOR
    assert r1s[0]["ts_utc"] == datetime(2026, 11, 16, 1, 30, 0)

    (m,) = read_table(s3, "readings_1min")
    assert m["minute_local"] == datetime(2026, 11, 16, 8, 30)
    assert m["n_rows"] == 4
    assert m["db_spl_mean"] == pytest.approx((62.40 + 63.11 + 64.00) / 3)
    assert (m["db_spl_max"], m["db_spl_min"]) == (64.0, 62.4)
    assert m["lat"] == pytest.approx(14.0717281) and m["cell_id"] is None

    (q,) = read_table(s3, "quality")
    assert q["batch_id"] == BATCH and q["duplicate_rows"] == 3 and q["loss_rate"] == 0.0
    assert json.loads(q["flag_counts"])["DUPLICATE"] == 3


def _object(s3, key):
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()


def test_transform_writes_rejected_rows_to_quarantine(s3):
    import csv, io
    rows = [row(i, f"2026-11-16 08:00:{i:02d}") for i in range(1, 10)]
    rows.append(row(10, "2026-11-16 09:00:10"))            # OUT_OF_BATCH_HOUR -> invalid
    put(s3, "12-1-10", to_csv(rows))
    put(s3, "12-1-2", to_csv(rows[:2]))                    # overlapping chunk -> 2 duplicates
    ev = run_all(s3)
    t = ev["transform"]
    assert t["quarantine"] == {"invalid": schema.s3_key_quarantine_rows(BATCH),
                               "duplicate": schema.s3_key_quarantine_duplicate(BATCH)}
    bad = list(csv.DictReader(io.StringIO(_object(s3, schema.s3_key_quarantine_rows(BATCH)))))
    assert len(bad) == 1 and bad[0]["Record_ID"] == "10"
    assert "OUT_OF_BATCH_HOUR" in bad[0]["reason"] and bad[0]["source_file"].endswith("part-12-1-10.csv")
    dup = list(csv.DictReader(io.StringIO(_object(s3, schema.s3_key_quarantine_duplicate(BATCH)))))
    assert [d["Record_ID"] for d in dup] == ["1", "2"]
    # Verify: raw 12 = trusted 9 + quarantined 1 + duplicate 2
    assert ev["verify"]["rows"] == {"raw": 12, "trusted": 9, "quarantined": 1, "duplicate": 2}


def test_clean_batch_writes_no_quarantine_files_and_rerun_removes_stale(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    stale = schema.s3_key_quarantine_rows(BATCH)
    s3.put_object(Bucket=BUCKET, Key=stale, Body=b"old")
    ev = run_all(s3)
    assert ev["transform"]["quarantine"] == {}
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix="quarantine/rows/iot/")


def test_one_parquet_per_table_per_hour(s3):
    for i in range(3):
        rows = [row(r, f"2026-11-16 08:{r // 60:02d}:{r % 60:02d}") for r in range(i * 10 + 1, i * 10 + 11)]
        put(s3, f"12-{i * 10 + 1}-{i * 10 + 10}", to_csv(rows))
    run_all(s3)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="trusted/iot_readings_1s/")["Contents"]]
    assert keys == [schema.s3_key_table("readings_1s", BATCH)]
    assert len(read_table(s3, "readings_1s")) == 30


def test_speed_filter_nulls_jump(s3):
    rows = [row(1, "2026-11-16 08:00:01"), row(2, "2026-11-16 08:00:02", Latitude="14.0817281"),
            row(3, "2026-11-16 08:00:03")]                # row 2 jumps ~1.1 km in 1 s
    put(s3, "12-1-3", to_csv(rows))
    ev = run_all(s3)
    assert ev["transform"]["gps_speed_nulled"] == 1
    lats = [r["lat"] for r in read_table(s3, "readings_1s")]
    assert lats[1] is None and lats[2] is not None


def test_speed_filter_reanchors_after_bad_first_fix(s3):
    # the first fix is ~1.1 km off, every later point is right: only 3 are lost
    rows = [row(1, "2026-11-16 08:00:01", Latitude="14.0817281")]
    rows += [row(i, f"2026-11-16 08:00:{i:02d}") for i in range(2, 11)]
    put(s3, "12-1-10", to_csv(rows))
    ev = run_all(s3)
    assert ev["transform"]["gps_speed_nulled"] == 3
    lats = [r["lat"] for r in read_table(s3, "readings_1s")]
    assert lats[1:4] == [None, None, None] and all(x is not None for x in lats[4:])


def test_station_uses_registry_position(s3):
    rows = [row(i, f"2026-11-16 08:30:0{i}", Device_Type="Stationary") for i in range(1, 4)]
    put(s3, "12-1-3", to_csv(rows))
    devices = [dev("IOTW-A001", "Stationary", 13.88, 100.57)]
    run_all(s3, devices=devices)
    r = read_table(s3, "readings_1s")[0]
    assert (r["lat"], r["lon"], r["speed_kmh"]) == (13.88, 100.57, None)


# ── VerifyBatch + end to end with CheckBatch ─────────────────────────────────

def test_verify_publishes_then_check_says_already_processed(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    ev = run_all(s3)
    assert ev["verify"]["rows"] == {"raw": 4, "trusted": 4, "quarantined": 0, "duplicate": 0}
    man = json.loads(s3.get_object(Bucket=BUCKET, Key=schema.s3_key_manifest(BATCH))["Body"].read())
    assert man["manifest_hash"] == ev["check"]["manifest_hash"]
    assert check_fn.check(s3, BUCKET, BATCH, "normal")["status"] == "ALREADY_PROCESSED"
    put(s3, "12-5-8", to_csv([row(r, f"2026-11-16 08:31:0{r}") for r in range(5, 9)]))
    assert check_fn.check(s3, BUCKET, BATCH, "normal")["status"] == "CHANGED"


def test_verify_mismatch_raises(s3):
    put(s3, "12-1-4", (FIXTURES / "good.csv").read_bytes())
    ev = validated(s3)
    transform_fn.transform(s3, BUCKET, ev, DEVICES)
    s3.put_object(Bucket=BUCKET, Key=schema.s3_key_table("readings_1s", BATCH),
                  Body=transform_fn.to_parquet("readings_1s", []))
    with pytest.raises(verify_fn.VerifyMismatch):
        verify_fn.verify(s3, BUCKET, ev, now=NOW)
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET, Prefix=schema.MANIFEST_PREFIX)


def test_count_data_lines_matches_parser():
    body = (FIXTURES / "good.csv").read_bytes() + b"\n\n"
    assert batch_eval.count_data_lines(body) == len(batch_eval.parse_file("k", body).rows) == 4


def test_stray_quote_does_not_swallow_following_lines(s3):
    rows = [row(i, f"2026-11-16 08:00:{i:02d}") for i in range(1, 6)]
    body = to_csv(rows).decode().splitlines()
    body[2] = body[2].replace(",Mobile,", ',"Mobile,', 1)    # corrupt line 3 (record 2)
    body = ("\n".join(body) + "\n").encode()
    assert batch_eval.count_data_lines(body) == len(batch_eval.parse_file("k", body).rows) == 5
    put(s3, "12-1-5", body)
    ev = run_all(s3)                                          # VerifyBatch must not raise
    assert ev["verify"]["rows"] == {"raw": 5, "trusted": 4, "quarantined": 1, "duplicate": 0}


def test_time_jump_uses_device_interval(s3):
    # CSV_INTERVAL_MS = 10000: one row every 10 s is normal, a 2 min gap is a jump
    times = ["08:00:00", "08:00:10", "08:00:20", "08:00:30", "08:02:30"]
    rows = [row(i, f"2026-11-16 {t}") for i, t in enumerate(times, start=1)]
    put(s3, "12-1-5", to_csv(rows))
    validated(s3)
    assert report(s3)["flag_counts"]["TIME_JUMP"] == 1


def test_readings_1min_populates_cell_id(s3):
    r1 = row(1, "2026-11-16 08:30:00", Latitude="13.8850", Longitude="100.5800")
    r2 = row(2, "2026-11-16 08:30:01", Latitude="13.8851", Longitude="100.5801")
    put(s3, "12-1-2", to_csv([r1, r2]))
    run_all(s3)

    (m,) = read_table(s3, "readings_1min")
    expected_cell = grid.latlon_to_cell(13.8850, 100.5800)
    assert expected_cell is not None
    assert m["cell_id"] == expected_cell


def test_readings_1min_station_cell_id_from_registry(s3):
    rows = [row(i, f"2026-11-16 08:30:0{i}", Device_Type="Stationary") for i in range(1, 4)]
    put(s3, "12-1-3", to_csv(rows))
    devices = [dev("IOTW-A001", "Stationary", 13.88, 100.57)]
    run_all(s3, devices=devices)

    (m,) = read_table(s3, "readings_1min")
    expected_cell = grid.latlon_to_cell(13.88, 100.57)
    assert expected_cell is not None
    assert m["cell_id"] == expected_cell
