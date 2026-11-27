"""legacy_log.py: convert legacy spl_log CSV, upload in chunks (no network)."""
import sys
from pathlib import Path

import pytest

import legacy_log

LEGACY_HEADER = ("DateTime,dB_SPL,Lux,UV_Index,Temp_C,Humidity_pct,Latitude,Longitude,Altitude_m,"
                 "Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,UV_Valid,DHT_Valid")


def legacy_file(tmp_path: Path) -> Path:
    p = tmp_path / "spl_log_20260301_085200.csv"
    p.write_text("\n".join([
        LEGACY_HEADER,
        "2026-03-01 08:52:00,0.00,120,0,30.1,60,13.88,100.57,10,0,0,99.99,0,1,1,1",
        "2026-03-01 08:52:01,55.20,121,0,30.1,60,13.88,100.57,10,1,7,1.20,1,1,1,0",
        "15,55.30,121,0,30.1,60,13.88,100.57,10,1,7,1.20,1,1,1,1",
    ]) + "\n", encoding="utf-8")
    return p


def test_convert_maps_columns_and_flags(tmp_path):
    rows, st = legacy_log.convert_file(legacy_file(tmp_path), "IOTW-A001", "Mobile")
    assert st["session"] == 3010852 and st["rows"] == 3
    assert [r["Record_ID"] for r in rows] == [1, 2, 3]
    assert rows[0]["SPL_Valid"] == 0 and "SPL_ERR" in rows[0]["Flags"] and "GPS_MISSING" in rows[0]["Flags"]
    assert rows[1]["Flags"] == "DHT_ERR"
    assert rows[2]["Time_Valid"] == 0 and "TIME_INVALID" in rows[2]["Flags"]
    assert tuple(rows[0]) == legacy_log.CSV_COLUMNS


def test_upload_retries_throttle_and_5xx(tmp_path, monkeypatch):
    rows, _ = legacy_log.convert_file(legacy_file(tmp_path), "IOTW-A001", "Mobile")
    out = tmp_path / "IOTW-A001_converted.csv"
    legacy_log.write_csv(rows, out)
    answers = iter([(429, "throttled"), (503, "busy"), (200, "{}")])
    calls = []

    def fake_post(url, token, device, chunk_id, body, timeout):
        calls.append(chunk_id)
        return next(answers)

    monkeypatch.setattr(legacy_log, "post", fake_post)
    monkeypatch.setattr(legacy_log.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "upload", str(out), "--url", "http://x", "--token", "t"])
    with pytest.raises(SystemExit) as e:
        legacy_log.main()
    assert e.value.code == 0
    assert calls == ["3010852-1-3"] * 3


def test_upload_does_not_retry_400(tmp_path, monkeypatch):
    rows, _ = legacy_log.convert_file(legacy_file(tmp_path), "IOTW-A001", "Mobile")
    out = tmp_path / "IOTW-A001_converted.csv"
    legacy_log.write_csv(rows, out)
    calls = []
    monkeypatch.setattr(legacy_log, "post", lambda *a: calls.append(a) or (400, "bad"))
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "upload", str(out), "--url", "http://x", "--token", "t"])
    with pytest.raises(SystemExit) as e:
        legacy_log.main()
    assert e.value.code == 1 and len(calls) == 1


V1_HEADER = ("Device_ID,Device_Type,Session,Record_ID,DateTime,dBA,dB_SPL,Lux,Temp_C,Humidity_pct,"
             "Latitude,Longitude,Altitude_m,Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,DHT_Valid")


def v1_file(tmp_path: Path) -> Path:
    p = tmp_path / "spl_log_20261113_164903.csv"
    p.write_text("\n".join([
        V1_HEADER,
        "IOTW-A001,Mobile,19,1,2026-11-13 16:49:05,60.02,81.49,590.77,29.6,69.5,13.7273855,100.7769257,-9.9,0.24,4,4.59,1,1,1",
        "IOTW-A001,Mobile,19,2,2026-11-13 16:49:06,0.00,0.00,590.77,29.6,69.5,0.0000000,0.0000000,0.0,0.00,0,99.99,0,1,0",
    ]) + "\n", encoding="utf-8")
    return p


def test_convert_v1_keeps_ids_and_shifts_spl(tmp_path):
    rows, st = legacy_log.convert_file(v1_file(tmp_path), None, "Mobile", 8.2)
    assert st["format"] == "v1" and st["session"] == 19 and st["device"] == "IOTW-A001"
    assert [r["Record_ID"] for r in rows] == [1, 2]
    assert rows[0]["dBA"] == "68.22" and rows[0]["dB_SPL"] == "89.69" and rows[0]["Flags"] == "OK"
    assert rows[1]["dB_SPL"] == "0.00" and rows[1]["SPL_Valid"] == 0 and "SPL_ERR" in rows[1]["Flags"]
    assert tuple(rows[0]) == legacy_log.CSV_COLUMNS


def test_convert_cli_v1_without_device(tmp_path, monkeypatch, capsys):
    src = v1_file(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "convert", str(src), "--out", str(out),
                                      "--old-mic-offset", "115.0", "--new-mic-offset", "123.2"])
    legacy_log.main()
    assert (out / "IOTW-A001_spl_log_20261113_164903.csv").is_file()
    assert "IOTW-A001__2026-11-13__16" in capsys.readouterr().out


def test_convert_legacy_requires_device(tmp_path):
    with pytest.raises(SystemExit):
        legacy_log.convert_file(legacy_file(tmp_path), None, "Mobile")


def test_convert_session_offset_and_duplicate_guard(tmp_path, monkeypatch):
    src = v1_file(tmp_path)
    rows, st = legacy_log.convert_file(src, "IOTW-A002", "Mobile", 0.0, 100)
    assert st["session"] == 119 and {r["Session"] for r in rows} == {119}
    assert {r["Device_ID"] for r in rows} == {"IOTW-A002"}
    out = tmp_path / "out"
    other = tmp_path / "other" / "spl_log_20261113_150938.csv"
    other.parent.mkdir(); other.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "convert", str(src), "--out", str(out)])
    legacy_log.main()
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "convert", str(other), "--out", str(out)])
    with pytest.raises(SystemExit, match="Session 19"):
        legacy_log.main()
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "convert", str(other), "--out", str(out),
                                      "--session-offset", "100"])
    legacy_log.main()
    monkeypatch.setattr(sys, "argv", ["legacy_log.py", "convert", str(src), "--out", str(out)])
    legacy_log.main()  # re-converting the same file overwrites itself, no error
