# tests — cloud/pipeline

รัน (จากโฟลเดอร์ `cloud/pipeline`):

```
pip install -r requirements-dev.txt
python -m pytest -q
```

ไม่ต้องต่อ AWS: test ของ Lambda ใช้ moto จำลอง S3 (test ของกริดใช้ `pyproj`: `pip install pyproj`) `conftest.py` เพิ่ม `cloud/pipeline` เข้า `sys.path` จึง `from common import schema` ได้

## Fixture

| ไฟล์ | มีอะไร |
|---|---|
| `fixtures/good.csv` | 4 แถวจาก IOTW-A001: แถว 1–2 ปกติ, แถว 3 ไมค์/DHT/GPS เสีย (`SPL_ERR;DHT_ERR;GPS_MISSING` ค่าว่าง), แถว 4 HDOP 6.5 (`GPS_POOR`) |
| `fixtures/communities.csv` | 36 ชุมชนจาก CS341-RONNAI `gee/communities.csv` (BMA open data) |
| `fixtures/no_rtc.csv` | 2 แถวจาก IOTW-A002 ที่เวลาเชื่อไม่ได้: DateTime เป็นเลข `12345` และ `2000-01-01 00:00:05` + `Time_Valid=0` |

## test_schema.py (37 ข้อ)

### Header CSV — `check_header`

| test | ตรวจอะไร |
|---|---|
| `test_header_ok` | header ตรง `CSV_COLUMNS` → ไม่มีปัญหา |
| `test_header_matches_firmware_source` | อ่าน `CSV_HEADER` จาก `iot/firmware/main/sd_logger.h` โดยตรง ต้องตรงกับ `CSV_COLUMNS` ทุกตัวและทุกลำดับ (ถ้าไม่มีโฟลเดอร์ firmware จะข้าม) **firmware เปลี่ยนคอลัมน์แล้วลืมแก้ schema.py ข้อนี้จะแดง** |
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
| `test_s3_keys` | path ของ raw (มี/ไม่มีเวลา), trusted/curated, report, quarantine (batch/rows/duplicate), manifest ถูกรูป และ chunk_id/ชื่อตารางผิด → error |
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

## test_grid.py — กริด 90 m (`common/grid.py`)

| test | ตรวจอะไร |
|---|---|
| `test_cell_id_roundtrip` / `test_bad_cell_id` | `c0012_r0345` สร้างและแยกกลับได้, รูปแบบผิด → error |
| `test_origin_on_landsat_lattice` | origin อยู่บนขอบพิกเซล Landsat (…15 m) และ 90 หาร 30 ลงตัว |
| `test_xy_to_cell_and_bounds` | พิกัด → เซลล์ถูก และขอบเซลล์ถูก |
| `test_point_on_edge_goes_east_and_south` | จุดบนเส้นแบ่งไปเซลล์ทางตะวันออก/ใต้ |
| `test_outside_grid_is_none` | จุดนอกกริดหรือไม่มีพิกัด → None |
| `test_every_cell_center_maps_back` | จุดกลางเซลล์แปลงกลับได้เซลล์เดิม |
| `test_latlon_roundtrip_and_aoi_inside_grid` | ชุมชนสุดขอบ (หลักสี่ใต้สุด, ดอนเมือง) อยู่ในกริด และแปลง lat/lon ไปกลับได้ |
| `test_cells_in_circle_area_matches_circle` | พื้นที่รวม (น้ำหนัก × เซลล์) ของ buffer 300/500 m ต่างจาก πr² ไม่เกิน 2% |
| `test_cells_in_circle_rejects_bad_radius` | รัศมี 0 หรือวงกลมเลยขอบกริด → error |
| `test_pure_python_utm47n_matches_pyproj` | สูตร UTM 47N ที่เขียนเอง (ใช้บน Lambda ที่ไม่มี pyproj) ได้ค่าตรงกับ pyproj |

## test_satellite_schema.py — ดาวเทียม + ชุมชน (`common/satellite_schema.py`)

| test | ตรวจอะไร |
|---|---|
| `test_periods` / `test_bad_period` | `scene_2026-03-15`, `season_2026_03-05`, `year_2026` ได้วันที่เริ่ม/จบถูก, รูปแบบผิด → error |
| `test_good_satellite_row` / `test_satellite_row_problems` | ค่านอกช่วง, cell_id ผิด, วันที่ไม่ตรง period, p90 < mean ถูกจับได้ |
| `test_scene_row_counts_one_date` | แถวรายฉากต้องมี n_scenes 0 หรือ 1 (ภาพวันเดียวกันจาก 2 row นับครั้งเดียว) |
| `test_clean_nulls_low_coverage` | พิกเซลใช้ได้ < 50% ของเซลล์ → ค่าดาวเทียมเป็น null |
| `test_tables_do_not_repeat_partition_keys` / `test_arrow_schema` | ตารางไม่ซ้ำคอลัมน์ partition และสร้าง pyarrow schema ได้ |
| `test_load_real_communities` | โหลด `fixtures/communities.csv` (36 ชุมชน หลักสี่ + ดอนเมือง) ได้ |
| `test_load_communities_rejects_duplicates` | community_id ซ้ำ → error |
| `test_buffer_cells_for_every_community` | ทุกชุมชนได้เซลล์ ≈ 35 (300 m) และ ≈ 97 (500 m) |
| `test_weighted_helpers` | ค่าเฉลี่ยถ่วงน้ำหนักและ quantile ถ่วงน้ำหนักถูก |
| `test_summarize_community` | สรุปชุมชน 3 เซลล์ (1 เซลล์เมฆบัง, 1 เซลล์ไม่มีข้อมูลอาคาร) ได้ lst_mean, p90, n_scenes, builtup_pct, building_count ตามคำนวณมือ |
| `test_summarize_without_buildings_and_missing_cells` | ไม่มีข้อมูลดาวเทียม/อาคาร → null, ไม่มีเซลล์ → error |
| `test_s3_keys` | path ของ raw/trusted/curated ถูกรูป และชื่อผิด → error |

## ยังไม่มี (ต้องทำก่อนปิด #2)

- fixture จาก CSV จริงของเครื่อง 1 ไฟล์ (~20 แถว)

## test_plan_batches.py (#11/#12, D21)

| test | ตรวจอะไร |
|---|---|
| `test_scheduled_time_is_thai_hour` | 18:05 UTC → ชั่วโมง H = 01:00 เวลาไทยวันถัดไป |
| `test_lookback_crosses_midnight` | ย้อนหลัง 3 ชม. จาก 01:00 ได้ 22, 23, 00 ของวันก่อน |
| `test_only_hours_with_data` | ส่งเฉพาะชั่วโมงที่มีไฟล์ใน raw เรียงเก่า → ใหม่ |
| `test_current_hour_and_outside_window_excluded` | ไม่เอาชั่วโมง H (ยังเขียนอยู่) และ H-25 |
| `test_unknown_partition_ignored` | `date=unknown` ไม่กลายเป็น batch |
| `test_inactive_device_skipped` | เครื่องที่ `valid_to` หมดแล้วไม่ถูกนับ |
| `test_handler_scheduled` / `test_handler_uses_real_devices_csv` | input จาก Scheduler → output ที่ Map ใช้ |
| `test_manual_batch_ids_pass_unfiltered` | รันมือ: `batch_ids` ผ่านตามที่ส่ง (Check ตัดสินเอง) |

## test_check_batch.py (#12)

| test | สถานะที่คาด |
|---|---|
| `test_invalid_input` (5 กรณี) | INVALID_INPUT: รูปแบบเก่า `/`, ชั่วโมง 24, เดือน 13, ไม่มี batch_id, mode `correction` |
| `test_no_data`, `test_neighbour_hour_and_unknown_are_not_this_batch` | NO_DATA (ไฟล์ชั่วโมงข้างๆ/เครื่องอื่น/unknown ไม่นับ) |
| `test_new_lists_files_sorted`, `test_force_on_unpublished_is_new` | NEW + manifest เรียงตาม key |
| `test_already_processed`, `test_resend_same_chunk_is_still_processed` | ALREADY_PROCESSED (ส่งก้อนเดิมซ้ำไม่ทำให้รันใหม่) |
| `test_backfill_chunk_makes_changed`, `test_replaced_content_makes_changed`, `test_force_on_published_is_changed` | CHANGED |
| `test_hash_ignores_order_and_size` | hash ไม่ขึ้นกับลำดับ |

## test_pipeline_steps.py (#13–#16, ต้องมี pyarrow)

ใช้ moto จำลอง S3 วาง CSV ใน `raw/csv/...` แล้วเรียก handler ตามลำดับเดียวกับ state machine

| test | ตรวจอะไร |
|---|---|
| `test_good_batch_passes_and_writes_report` | ข้อมูลดี → PASS และเขียน `validation/<batch_id>/report.json` |
| `test_bad_header_fails` | header ผิด → FAIL `BAD_HEADER` |
| `test_duplicates_counted_not_failed` | แถวซ้ำ (ส่งก้อนเดิมสองครั้ง) นับเป็น duplicate ไม่ทำให้ FAIL และไม่นับในสัดส่วน invalid |
| `test_missing_record_ids` | Record_ID ขาดช่วง → missing_rows, missing_ranges, loss_rate ถูก |
| `test_too_many_invalid_fails` | แถวเสีย > 20% → FAIL `TOO_MANY_INVALID` |
| `test_device_id_not_matching_path_fails` | Device_ID ในแถวไม่ตรง path → `DEVICE_ID_MISMATCH` |
| `test_unknown_device_fails` / `test_device_type_mismatch_fails` | ไม่มีใน devices.csv / Device_Type ไม่ตรงทะเบียน |
| `test_station_outside_registry_fails` | station มีแถวนอกช่วง valid_from–valid_to → `UNKNOWN_STATION_SITE` |
| `test_time_jump_flagged_not_dropped` | เวลากระโดด → flag `TIME_JUMP` แต่ยังเก็บแถว |
| `test_parse_error_row_is_invalid` | แถวแปลงไม่ได้ → invalid (ไม่ทำให้ Lambda พัง) |
| `test_quarantine_copies_files_and_reason` | คัดลอก raw ไป `quarantine/<batch_id>/` + `reason.json` โดย raw เดิมยังอยู่ |
| `test_transform_tables` | readings_1s / readings_1min (mean/max/min ไม่มี Leq, cell_id null เพราะจุด fixture อยู่นอกกริด) / quality ถูกต้อง |
| `test_one_parquet_per_table_per_hour` | raw หลาย part ในชั่วโมงเดียว → Parquet 1 ไฟล์ต่อตาราง |
| `test_speed_filter_nulls_jump` | จุด GPS ที่เร็วเกิน 30 km/h จากจุดก่อน → lat/lon เป็น null |
| `test_station_uses_registry_position` | Stationary ใช้พิกัดจาก devices.csv |
| `test_readings_1min_populates_cell_id` | readings_1min ได้ cell_id จาก GPS จุดแรกของนาที (grid.py, #29) |
| `test_readings_1min_station_cell_id_from_registry` | Stationary ได้ cell_id จากพิกัดใน devices.csv |
| `test_verify_publishes_then_check_says_already_processed` | Verify เขียน manifest แล้ว CheckBatch รอบถัดไปได้ ALREADY_PROCESSED, มี part ใหม่ → CHANGED |
| `test_verify_mismatch_raises` | จำนวนแถวไม่ตรง → `VerifyMismatch` |
| `test_count_data_lines_matches_parser` | ตัวนับบรรทัดของ Verify ตรงกับ parser (บรรทัดว่าง/BOM) |

## test_statemachine.py (#11)

แทนค่า `${...}` ใน `statemachine/pipeline.asl.json` แบบเดียวกับ Terraform แล้วตรวจโครง: ทุก state ไปถึงได้, ทุก `Next` มีจริง, ทุกเส้นทางใน Map จบที่ Published / Quarantined / StopAlreadyProcessed / NoData / InvalidInput / PipelineFailed และทุก Task มี Retry + Catch
