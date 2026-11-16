# tests — cloud/pipeline

รัน (จากโฟลเดอร์ `cloud/pipeline`):

```
pip install pytest pyarrow
python -m pytest -q
```

ไม่ต้องต่อ AWS ทุก test อ่านแค่ไฟล์ในเครื่อง `conftest.py` เพิ่ม `cloud/pipeline` เข้า `sys.path` จึง `from common import schema` ได้

## Fixture

| ไฟล์ | มีอะไร |
|---|---|
| `fixtures/good.csv` | 4 แถวจาก IOTW-A001: แถว 1–2 ปกติ, แถว 3 ไมค์/DHT/GPS เสีย (`SPL_ERR;DHT_ERR;GPS_MISSING` ค่าว่าง), แถว 4 HDOP 6.5 (`GPS_POOR`) |
| `fixtures/no_rtc.csv` | 2 แถวจาก IOTW-A002 ที่เวลาเชื่อไม่ได้: DateTime เป็นเลข `12345` และ `2000-01-01 00:00:05` + `Time_Valid=0` |

## test_schema.py (37 ข้อ)

### Header CSV — `check_header`

| test | ตรวจอะไร |
|---|---|
| `test_header_ok` | header ตรง `CSV_COLUMNS` → ไม่มีปัญหา |
| `test_header_matches_firmware_source` | อ่าน header จาก `iot/firmware/main/sd_logger.cpp` โดยตรง ต้องตรงกับ `CSV_COLUMNS` ทุกตัวและทุกลำดับ (ถ้าไม่มีโฟลเดอร์ firmware จะข้าม) **firmware เปลี่ยนคอลัมน์แล้วลืมแก้ schema.py ข้อนี้จะแดง** |
| `test_header_missing_column` | ขาด `Lux` → `missing columns: Lux` |
| `test_header_extra_column` | มีคอลัมน์เกิน → `unexpected columns: Foo` |
| `test_header_swapped` | สลับ dBA กับ dB_SPL → `columns out of order` |
| `test_header_fixture` | header ของ `good.csv` ผ่าน |

### แถวข้อมูล — `parse_row` / `check_row`

| test | ตรวจอะไร |
|---|---|
| `test_good_row_has_no_flags` | แถวปกติแปลงชนิดถูก (dB_SPL เป็น float, GPS_Valid เป็น bool, เวลาเป็น datetime) และไม่มี flag |
| `test_spl_out_of_range` | dB_SPL = 125 (เกิน 120) → `SPL_OOR` |
| `test_blank_values_with_valid_zero_only_get_device_flags` | ค่าว่างที่ `*_Valid=0` อ่านเป็น None และได้แค่ flag ที่เครื่องเขียน ไม่ถูกนับว่าข้อมูลเสียเพิ่ม |
| `test_gps_poor_from_hdop` | HDOP 6.5 > 5 → `GPS_POOR` |
| `test_no_rtc_numeric_and_untrusted` | ทั้ง 2 แถวใน `no_rtc.csv` → `NO_RTC` ต้องตัดทิ้ง และ ingest จัดไป `date=unknown/hour=unknown` |
| `test_old_year_with_time_valid_is_no_rtc` | ปีต่ำกว่า 2025 แม้ `Time_Valid=1` → `NO_RTC` |
| `test_out_of_batch_hour` | แถวเวลา 08:30 อยู่ใน batch ชั่วโมง 08 ได้ แต่ batch 09 → `OUT_OF_BATCH_HOUR` |
| `test_bad_device_and_unknown_flag` | Device_ID มีช่องว่าง, Device_Type ไม่รู้จัก, Flags แปลก → `BAD_DEVICE_ID`, `BAD_DEVICE_TYPE`, `UNKNOWN_FLAG` |
| `test_parse_error` (4 กรณี) | Session ไม่ใช่ตัวเลข, dB_SPL เป็นตัวอักษร, GPS_Valid = 2, Record_ID ว่าง → `RowError` |

### Clean ก่อนเข้า Parquet — `clean_row`

| test | ตรวจอะไร |
|---|---|
| `test_clean_row_nulls_and_drops_flag_columns` | ค่าที่มี flag เป็น null, ค่าที่ดี (Lux) คงไว้, ผลลัพธ์ไม่มี `*_Valid`/`Flags` และคอลัมน์ตรง `READINGS_1S` |
| `test_clean_row_gps_poor_nulls_position` | `GPS_POOR` → lat/lon null แต่ hdop และค่าเสียงยังอยู่ |
| `test_clean_row_temp_oor_keeps_humidity` | Temp 95 °C → temp null แต่ humidity ยังอยู่ |
| `test_clean_row_utc` | 08:30 เวลาไทย → 01:30 UTC |
| `test_clean_row_refuses_dropped_row` | ส่งแถว `NO_RTC` เข้ามา → error (ต้องตัดก่อน) |

### batch_id และ S3 key

| test | ตรวจอะไร |
|---|---|
| `test_batch_id_roundtrip` | สร้าง `IOTW-A001__2026-11-16__08` แล้วแยกกลับได้ค่าเดิม |
| `test_batch_id_bad` (5 กรณี) | ชั่วโมง 24, เดือน 13, คั่นด้วย `_` เดียว, device_id มีช่องว่าง, ไม่มี device_id → error |
| `test_device_id_cannot_hold_separator` | device_id ที่มี `__` → error |
| `test_s3_keys` | path ของ raw (มี/ไม่มีเวลา), processed, report, quarantine, manifest ถูกรูป และ chunk_id/ชื่อตารางผิด → error |
| `test_raw_partition_from_raw_dict` | แถว CSV ดิบ → `("2026-11-16", "08")` |

### ตาราง Parquet

| test | ตรวจอะไร |
|---|---|
| `test_tables_have_no_partition_keys_or_flag_columns` | ทั้ง 3 ตารางไม่มีคอลัมน์ `device_id`/`date` (อยู่ใน path แทน) และไม่มี `*_Valid`/`Flags` |
| `test_arrow_schema` | สร้าง pyarrow schema ได้ทั้ง 3 ตาราง (ข้ามถ้าไม่ได้ติดตั้ง pyarrow) |

### devices.csv

| test | ตรวจอะไร |
|---|---|
| `test_devices_csv_loads` | ไฟล์จริงโหลดได้ มี IOTW-A001, IOTW-A002 เป็น Mobile |
| `test_lookup_device_moves_mid_day` | station ย้ายจุดเที่ยงวัน: 11:59 ได้จุด A, 12:00 ได้จุด B, ก่อนติดตั้งและเครื่องที่ไม่มีในทะเบียน → None |
| `test_station_without_coords_is_rejected` | Stationary ที่ไม่มีพิกัด → error บอกเลขบรรทัด |

## ยังไม่มี (ต้องทำก่อนปิด #2)

- fixture จาก CSV จริงของเครื่อง 1 ไฟล์ (~20 แถว)
