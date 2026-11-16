# Data schema กลาง (IoT)

โค้ดอยู่ที่ `cloud/pipeline/common/schema.py` (ที่เดียว ทุกส่วน import จากตรงนี้) เอกสารนี้สรุปสำหรับคนอ่านและใช้ในเล่ม
ถ้า firmware เปลี่ยนคอลัมน์หรือช่วงค่า ต้องแก้ `schema.py` ใน commit เดียวกัน (test `test_header_matches_firmware_source` จะแดงถ้า header ไม่ตรง)

## 1. CSV จากเครื่อง (raw)

```
Device_ID,Device_Type,Session,Record_ID,DateTime,dBA,dB_SPL,Lux,Temp_C,Humidity_pct,Latitude,Longitude,Altitude_m,Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,DHT_Valid,Time_Valid,SPL_Valid,Flags
```

- `Device_ID` A-Z a-z 0-9 - _ ไม่เกิน 16 ตัว ห้ามมี `__` (ใช้คั่น batch_id) ตอนนี้มี `IOTW-A001`, `IOTW-A002`
- `Device_Type` = `Mobile` หรือ `Stationary`
- `DateTime` เวลาไทย `YYYY-MM-DD HH:MM:SS` ไม่มี offset ถ้าไม่มี RTC เป็นเลขวินาทีตั้งแต่บูต
- ค่าที่เซนเซอร์ invalid เป็นช่องว่าง, `*_Valid` เป็น 0/1, `Flags` = `OK` หรือรหัสคั่น `;`
- `dB_SPL` คือค่าหลัก, `dBA` ค่ารอง (D19)

ช่วงค่า (ตาม `config.h`): dB 30–120, Temp −40–80 °C, Humidity 0–100 %, HDOP > 5 = `GPS_POOR`

## 2. Flags

| ชุด | รหัส |
|---|---|
| firmware เขียน | `TIME_INVALID` `SPL_ERR` `SPL_OOR` `LUX_ERR` `LUX_OOR` `DHT_ERR` `TEMP_OOR` `HUMID_OOR` `GPS_MISSING` `GPS_POOR` |
| pipeline เติม | `NO_RTC` `OUT_OF_BATCH_HOUR` `TIME_JUMP` `DUPLICATE` `PARSE_ERROR` `BAD_DEVICE_ID` `BAD_DEVICE_TYPE` `UNKNOWN_FLAG` |
| ตัดทั้งแถว | `NO_RTC` `OUT_OF_BATCH_HOUR` `DUPLICATE` `PARSE_ERROR` `BAD_DEVICE_ID` `BAD_DEVICE_TYPE` |

`NO_RTC` = DateTime เป็นตัวเลข **หรือ** `Time_Valid=0` (เช่นเครื่องยังไม่ sync เขียนปี 2000) แถวพวกนี้ ingest เขียนลง `date=unknown/hour=unknown`

แถวที่ไม่ถูกตัด ค่าที่มี flag จะถูกตั้งเป็น null (`clean_row`) เช่น `DHT_ERR` → temp/humidity null, `GPS_MISSING`/`GPS_POOR` → lat/lon/alt/speed null

## 3. Parquet (processed)

Partition: `device_id`, `date` (อยู่ใน path ไม่อยู่ในไฟล์) ไม่มีคอลัมน์ `*_Valid` / `Flags` เพราะ clean แล้ว
ย้อนดูสาเหตุรายแถวได้จาก raw ด้วย `session` + `record_id` และดูจำนวนต่อ batch ได้ที่ `quality.flag_counts`

| ตาราง | 1 แถว = | คอลัมน์ |
|---|---|---|
| `readings_1s` | 1 แถว CSV ที่ผ่าน | device_type, session, record_id, ts_local, ts_utc, db_spl, dba, lux, temp_c, humidity_pct, lat, lon, alt_m, speed_kmh, satellites, hdop |
| `readings_1min` | 1 นาที × 1 เครื่อง | minute_local, minute_utc, n_rows, db_spl_mean/max/min, dba_mean, temp_c_mean, humidity_pct_mean, lux_mean, lat, lon, cell_id (#29) |
| `quality` | 1 session × 1 batch | batch_id, session, expected/received/missing/duplicate/invalid_rows, loss_rate, flag_counts (JSON) |

ค่าเสียงรายนาทีใช้ mean/max/min ไปก่อน ยังไม่ใช้ Leq

## 4. batch_id และ S3

batch = 1 เครื่อง × 1 ชั่วโมงเวลาไทย, `batch_id` = `<device_id>__<YYYY-MM-DD>__<HH>` เช่น `IOTW-A001__2026-11-16__08`

| | key |
|---|---|
| raw | `raw/csv/device_id=<id>/date=<YYYY-MM-DD>/hour=<HH>/part-<chunk_id>.csv` (ไม่มีเวลา: `date=unknown/hour=unknown`) |
| processed | `processed/<table>/device_id=<id>/date=<YYYY-MM-DD>/<batch_id>.parquet` |
| report | `validation/<batch_id>/report.json` |
| กักกัน | `quarantine/<batch_id>/` |
| manifest | `processed/_manifests/<batch_id>.json` |

`chunk_id` = `<session>-<first_record>-<last_record>`

## 5. devices.csv

`device_id,device_type,fixed_lat,fixed_lon,site_name,valid_from,valid_to,note`
`valid_from` รวม, `valid_to` ไม่รวม, ว่าง = ยังใช้งาน ย้ายจุด station = ใส่ `valid_to` แถวเดิมแล้วเพิ่มแถวใหม่ (ห้ามแก้ย้อนหลัง) station ต้องมี fixed_lat/lon
