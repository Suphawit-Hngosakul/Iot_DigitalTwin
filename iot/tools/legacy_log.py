#!/usr/bin/env python3
"""แปลง log รุ่นเก่า (spl_log_YYYYMMDD_HHMMSS.csv) เป็น CSV ตาม schema ปัจจุบัน แล้วส่งเข้า ingest

log รุ่นเก่า (ก่อน Special Project 2) มีคอลัมน์:
    DateTime,dB_SPL,Lux,UV_Index,Temp_C,Humidity_pct,Latitude,Longitude,Altitude_m,
    Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,UV_Valid,DHT_Valid
schema ปัจจุบัน (spec 01 §1, schema.CSV_COLUMNS):
    Device_ID,Device_Type,Session,Record_ID,DateTime,dBA,dB_SPL,Lux,Temp_C,Humidity_pct,
    Latitude,Longitude,Altitude_m,Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,DHT_Valid,
    Time_Valid,SPL_Valid,Flags

การแปลง:
- Device_ID / Device_Type จาก --device / --device-type
- Session = MMDDHHMM ของเวลาในชื่อไฟล์ (1 ไฟล์ = 1 ครั้งที่เปิดเครื่อง), Record_ID = 1, 2, 3, ... ตามลำดับแถว
- dBA ไม่มีในรุ่นเก่า -> ว่าง; UV_Index / UV_Valid ไม่อยู่ใน schema -> ตัดทิ้ง
- dB_SPL = 0.00 (แถวแรกหลังบูต mic ยังไม่พร้อม) -> SPL_Valid = 0
- DateTime ถือเป็นเวลาไทยจาก RTC (Time_Valid = 1 ถ้า parse ได้และปี >= 2025)
- Flags สร้างจาก *_Valid แบบเดียวกับ firmware (GPS_MISSING, GPS_POOR, DHT_ERR, LUX_ERR, SPL_ERR, TIME_INVALID) หรือ OK

รองรับ log อีกรุ่น (v1, firmware ช่วง พ.ย. 2569 ก่อนเพิ่ม Time_Valid/SPL_Valid/Flags) ที่มีคอลัมน์:
    Device_ID,Device_Type,Session,Record_ID,DateTime,dBA,dB_SPL,Lux,Temp_C,Humidity_pct,Latitude,
    Longitude,Altitude_m,Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,DHT_Valid
- ใช้ Device_ID / Device_Type / Session / Record_ID จากไฟล์ (--device ใช้ทับ Device_ID ได้)
- --session-offset N บวก N ให้ Session ใช้เมื่อเครื่องเดียวกันมี Session ซ้ำ (เช่น ไฟล์ที่ตั้ง Device_ID ผิด
  แล้วแก้ด้วย --device) เพราะ ingest/pipeline ระบุแถวด้วย (Device_ID, Session, Record_ID)
  convert จะหยุดถ้า (Device_ID, Session) ซ้ำกับไฟล์อื่นในรอบเดียวกันหรือใน --out
- เติม Time_Valid, SPL_Valid, Flags ตามกฎเดียวกับด้านบน

แก้ค่าเสียงที่บันทึกด้วย MIC_OFFSET_DB ค่าเก่า:
    --old-mic-offset 115.0 --new-mic-offset 123.2   (บวก new - old = +8.2 dB ให้ dBA และ dB_SPL)
    เพราะ SPL = 20*log10(rms) + MIC_OFFSET_DB ทั้ง dBA และ dB_SPL ค่าที่ถูกจึงต่างกันแค่ค่าคงที่
    แถวที่ dB_SPL = 0 (mic ไม่พร้อม) ไม่บวก

ใช้:
    python legacy_log.py convert spl_log_*.csv --device IOTW-A001 --out converted/
    python legacy_log.py convert raw/kmitl_20261113/*.csv --old-mic-offset 115.0 --new-mic-offset 123.2
    python legacy_log.py convert raw/x/a.csv --device IOTW-A001 --session-offset 100 --old-mic-offset 115.0 --new-mic-offset 123.2
    python legacy_log.py upload converted/*.csv --url "$INGEST_URL" --token "$DEVICE_TOKEN"   # --dry-run เพื่อดูก่อน

upload ส่งทีละก้อน <= 300 แถว (spec 20) header เดียวกับบอร์ด:
    Authorization: Bearer <token>, X-Device-Id, X-Chunk-Id = <session>-<first>-<last>
ส่งซ้ำได้ (idempotent ด้วย chunk_id) แล้วสั่ง pipeline ด้วย batch_ids ที่สคริปต์พิมพ์ให้
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CSV_COLUMNS = (
    "Device_ID", "Device_Type", "Session", "Record_ID",
    "DateTime", "dBA", "dB_SPL", "Lux", "Temp_C", "Humidity_pct", "Latitude",
    "Longitude", "Altitude_m", "Speed_kmh", "Satellites", "HDOP",
    "GPS_Valid", "Lux_Valid", "DHT_Valid",
    "Time_Valid", "SPL_Valid", "Flags",
)
V1_COLUMNS = CSV_COLUMNS[:19]
LEGACY_REQUIRED = ("DateTime", "dB_SPL", "Lux", "Temp_C", "Humidity_pct", "Latitude", "Longitude",
                   "Altitude_m", "Speed_kmh", "Satellites", "HDOP", "GPS_Valid", "Lux_Valid", "DHT_Valid")
NAME_RE = re.compile(r"(\d{8})_(\d{6})")
GPS_MAX_HDOP = 5.0
CHUNK_ROWS = 300


def session_from_name(path: Path) -> int:
    m = NAME_RE.search(path.name)
    if not m:
        raise SystemExit(f"{path.name}: ชื่อไฟล์ต้องมี YYYYMMDD_HHMMSS")
    return int(m.group(1)[4:] + m.group(2)[:4])  # MMDDHHMM เช่น 03010852


def is_valid_time(s: str) -> bool:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").year >= 2025
    except ValueError:
        return False


def shift_db(value: str, shift: float) -> str:
    """บวกค่าชดเชยเสียง; ค่าว่าง / ไม่ใช่ตัวเลข / <= 0 (mic ไม่พร้อม) คงเดิม"""
    try:
        v = float(value)
    except ValueError:
        return value.strip()
    if shift == 0 or v <= 0:
        return value.strip()
    return f"{v + shift:.2f}"


def convert_file(path: Path, device: str | None, device_type: str,
                 spl_shift: float = 0.0, session_offset: int = 0) -> tuple[list[dict], dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = tuple(reader.fieldnames or ())
        v1 = fields == V1_COLUMNS
        missing = [c for c in LEGACY_REQUIRED if c not in fields]
        if missing:
            raise SystemExit(f"{path.name}: ไม่ใช่ log รุ่นเก่า (ขาด {missing})")
        src = list(reader)
    if not v1 and not device:
        raise SystemExit(f"{path.name}: log รุ่นเก่าไม่มี Device_ID ต้องใส่ --device")
    session = (int(src[0]["Session"]) if v1 and src else session_from_name(path)) + session_offset
    out, stats = [], {"format": "v1" if v1 else "legacy", "rows": 0, "no_time": 0, "spl_err": 0,
                      "gps_missing": 0, "dht_err": 0, "spl_shift_db": spl_shift}
    for i, r in enumerate(src, start=1):
        gps = r["GPS_Valid"].strip() == "1"
        lux = r["Lux_Valid"].strip() == "1"
        dht = r["DHT_Valid"].strip() == "1"
        try:
            spl_val = float(r["dB_SPL"])
        except ValueError:
            spl_val = 0.0
        spl = spl_val > 0
        tvalid = is_valid_time(r["DateTime"])
        try:
            hdop = float(r["HDOP"])
        except ValueError:
            hdop = 99.9
        flags = []
        if not tvalid:
            flags.append("TIME_INVALID"); stats["no_time"] += 1
        if not spl:
            flags.append("SPL_ERR"); stats["spl_err"] += 1
        if not lux:
            flags.append("LUX_ERR")
        if not dht:
            flags.append("DHT_ERR"); stats["dht_err"] += 1
        if not gps:
            flags.append("GPS_MISSING"); stats["gps_missing"] += 1
        elif hdop > GPS_MAX_HDOP:
            flags.append("GPS_POOR")
        if v1:
            dev, dtype, sess, rec = (device or r["Device_ID"].strip(), r["Device_Type"].strip(),
                                     int(r["Session"]) + session_offset, int(r["Record_ID"]))
            dba = shift_db(r["dBA"], spl_shift) if spl else r["dBA"].strip()
        else:
            dev, dtype, sess, rec, dba = device, device_type, session, i, ""
        out.append({
            "Device_ID": dev, "Device_Type": dtype, "Session": sess, "Record_ID": rec,
            "DateTime": r["DateTime"].strip(), "dBA": dba, "dB_SPL": shift_db(r["dB_SPL"], spl_shift),
            "Lux": r["Lux"].strip(), "Temp_C": r["Temp_C"].strip(), "Humidity_pct": r["Humidity_pct"].strip(),
            "Latitude": r["Latitude"].strip(), "Longitude": r["Longitude"].strip(),
            "Altitude_m": r["Altitude_m"].strip(), "Speed_kmh": r["Speed_kmh"].strip(),
            "Satellites": r["Satellites"].strip(), "HDOP": r["HDOP"].strip(),
            "GPS_Valid": int(gps), "Lux_Valid": int(lux), "DHT_Valid": int(dht),
            "Time_Valid": int(tvalid), "SPL_Valid": int(spl), "Flags": ";".join(flags) or "OK",
        })
    stats["rows"] = len(out)
    stats["session"] = session
    stats["device"] = out[0]["Device_ID"] if out else device
    stats["first"] = out[0]["DateTime"] if out else None
    stats["last"] = out[-1]["DateTime"] if out else None
    return out, stats


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def expand(patterns: list[str]) -> list[Path]:
    """ขยาย * ? เอง เพราะ PowerShell / cmd ไม่ขยาย wildcard ให้ (bash ขยายมาแล้วก็ผ่านได้)"""
    out = []
    for pat in patterns:
        hits = sorted(glob.glob(pat)) if glob.has_magic(pat) else [pat]
        out.extend(Path(h) for h in (hits or [pat]))
    return out


def check_sessions(out_dir: Path, results: list) -> None:
    """หยุดถ้า (Device_ID, Session) ซ้ำ: ingest ใช้ chunk_id = session-first-last ทับไฟล์กัน และ pipeline ตัดแถวเป็น DUPLICATE"""
    owner = {}
    targets = {dst.resolve() for _, dst, _, _ in results}
    for f in sorted(out_dir.glob("*.csv")):
        if f.resolve() in targets:
            continue
        with f.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                owner.setdefault((r["Device_ID"], int(r["Session"])), f.name)
    for p, dst, rows, _ in results:
        for key in sorted({(r["Device_ID"], int(r["Session"])) for r in rows}):
            if key in owner and owner[key] != p.name:
                raise SystemExit(f"{p.name}: {key[0]} Session {key[1]} ซ้ำกับ {owner[key]} "
                                 f"ใช้ --session-offset หรือย้ายไฟล์เดิมออกจาก {out_dir}")
            owner[key] = p.name


def cmd_convert(a) -> None:
    out_dir = Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    hours = set()
    shift = 0.0
    if (a.old_mic_offset is None) != (a.new_mic_offset is None):
        raise SystemExit("ต้องใส่ --old-mic-offset และ --new-mic-offset คู่กัน")
    if a.old_mic_offset is not None:
        shift = round(a.new_mic_offset - a.old_mic_offset, 4)
    results = []
    for p in expand(a.files):
        rows, st = convert_file(p, a.device, a.device_type, shift, a.session_offset)
        name = p.stem.split("-", 1)[1] if re.match(r"^[0-9a-f]{8}-spl_log_", p.stem) else p.stem
        results.append((p, out_dir / f"{st['device']}_{name}.csv", rows, st))
    check_sessions(out_dir, results)
    for p, dst, rows, st in results:
        write_csv(rows, dst)
        for r in rows:
            if r["Time_Valid"]:
                hours.add((r["Device_ID"], r["DateTime"][:13]))
        print(f"{p.name} -> {dst.name}: {json.dumps(st, ensure_ascii=False)}")
    ids = sorted(f"{d}__{h[:10]}__{h[11:13]}" for d, h in hours)
    print("\nbatch_ids:", json.dumps(ids))


def post(url: str, token: str, device: str, chunk_id: str, body: bytes, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "X-Device-Id": device, "X-Chunk-Id": chunk_id,
        "Content-Type": "text/csv"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]


def cmd_upload(a) -> None:
    hours, failed = set(), 0
    files = expand(a.files) or sorted((Path(__file__).resolve().parent / "converted").glob("*.csv"))
    missing = [str(f) for f in files if not f.is_file()]
    if missing or not files:
        raise SystemExit(f"ไม่พบไฟล์: {missing or 'converted/*.csv'} (cwd = {Path.cwd()})")
    print("ไฟล์ที่จะส่ง:", ", ".join(f.name for f in files))
    for p in files:
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
                raise SystemExit(f"{p.name}: header ไม่ตรง schema (รัน convert ก่อน)")
            rows = list(reader)
        for k in range(0, len(rows), CHUNK_ROWS):
            chunk = rows[k:k + CHUNK_ROWS]
            device, session = chunk[0]["Device_ID"], chunk[0]["Session"]
            chunk_id = f"{session}-{chunk[0]['Record_ID']}-{chunk[-1]['Record_ID']}"
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n"); w.writeheader(); w.writerows(chunk)
            for r in chunk:
                if r["Time_Valid"] == "1":
                    hours.add((device, r["DateTime"][:13]))
            if a.dry_run:
                print(f"[dry-run] {p.name} {chunk_id} {len(chunk)} rows {len(buf.getvalue())} B"); continue
            for attempt in range(4):
                status, text = post(a.url, a.token, device, chunk_id, buf.getvalue().encode(), a.timeout)
                if status < 500 and status != 429:      # 429 = API Gateway throttle: retry
                    break
                time.sleep(2 ** attempt)
            ok = 200 <= status < 300
            failed += not ok
            print(f"{'OK ' if ok else 'ERR'} {p.name} {chunk_id} -> {status} {text}")
    ids = sorted(f"{d}__{h[:10]}__{h[11:13]}" for d, h in hours)
    print(f"\nเสร็จ (ล้มเหลว {failed} ก้อน) สั่ง pipeline:")
    print(f"aws stepfunctions start-execution --state-machine-arn \"$(terraform output -raw pipeline_state_machine_arn)\" "
          f"--input '{json.dumps({'batch_ids': ids, 'mode': 'force'})}'")
    sys.exit(1 if failed else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="log รุ่นเก่า -> CSV schema ปัจจุบัน")
    c.add_argument("files", nargs="+")
    c.add_argument("--device", help="Device_ID (จำเป็นสำหรับ log รุ่นเก่า; รุ่น v1 ใช้จากไฟล์ถ้าไม่ใส่)")
    c.add_argument("--old-mic-offset", type=float, help="MIC_OFFSET_DB ที่ใช้ตอนเก็บ เช่น 115.0")
    c.add_argument("--new-mic-offset", type=float, help="MIC_OFFSET_DB ที่ถูกต้อง เช่น 123.2")
    c.add_argument("--device-type", default="Mobile", choices=["Mobile", "Stationary"])
    c.add_argument("--session-offset", type=int, default=0, help="บวกให้ Session (กัน Session ซ้ำในเครื่องเดียวกัน)")
    c.add_argument("--out", default="converted")
    c.set_defaults(func=cmd_convert)
    u = sub.add_parser("upload", help="POST ไฟล์ที่แปลงแล้วเข้า ingest ทีละ 300 แถว")
    u.add_argument("files", nargs="*", help="ไม่ใส่ = ทุกไฟล์ใน converted/ ข้างสคริปต์"); u.add_argument("--url", help="terraform output -raw ingest_url")
    u.add_argument("--token", help="device token ของเครื่องนั้น (ค่าเดียวกับใน secrets.h)")
    u.add_argument("--timeout", type=float, default=30); u.add_argument("--dry-run", action="store_true")
    u.set_defaults(func=cmd_upload)
    a = ap.parse_args()
    if a.cmd == "upload" and not a.dry_run and not (a.url and a.token):
        ap.error("upload ต้องมี --url และ --token (หรือใช้ --dry-run)")
    a.func(a)


if __name__ == "__main__":
    main()
