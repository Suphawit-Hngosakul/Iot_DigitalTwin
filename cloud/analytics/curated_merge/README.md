# cloud/analytics/curated_merge — curated-merge (#32, spec 60 §2.1, D23, D24)

Step Functions `iot-dt-curated-merge` รวม `satellite_grid` + `buildings_grid` + `readings_1min`
เป็นตาราง Iceberg `iot_walking.cell_period` (S3: `curated/cell_period/`) ด้วย Athena `MERGE INTO`

```
(EventBridge: curated/_success/satellite|buildings/*.json | Scheduler รายวัน 10:00 | รันมือ)
PlanMerge → AnythingToMerge? ─ ไม่มี → NothingToDo
                             └ Map ทีละ period: CheckSources → SourcesReady? ─ ไม่ครบ → WaitingForOtherSources
                                                                         └ ครบ → MergeCurated → VerifyCurated → Published
Catch ทุก Task → NotifyFailure (SNS) → PipelineFailed
```

## ตาราง `cell_period`

1 แถว = 1 เซลล์ × 1 period × 1 `match_type` · key `(cell_id, period, match_type)` · partition `period`
คอลัมน์ดู `common/curated.py` (`CELL_PERIOD`)

| period | ค่าดาวเทียมจาก (`sat_period`) | `window` (IoT) | `all` (IoT) |
|---|---|---|---|
| `scene_<date>` | ฉากนั้น | ±7 วัน 09:30–11:30 · station: วันที่ถ่าย 10:00–11:00 | ±7 วัน ทุกเวลา |
| `season_<y>_03-05` | composite นั้น | 1 มี.ค.–31 พ.ค. 09:30–11:30 | 1 มี.ค.–31 พ.ค. ทุกเวลา |
| `month_<YYYY-MM>` | season ล่าสุดที่ปี ≤ ปีของเดือน | ทั้งเดือน 09:30–11:30 | ทั้งเดือน ทุกเวลา |

- เซลล์ที่ IoT < `MIN_IOT_MINUTES` (5) → `air_temp_mean, humidity_mean, lux_mean, db_spl_mean` เป็น null (`n_iot_minutes` ยังเก็บค่าจริง)
- `in_cover = false` ยังอยู่ในตาราง (ตัดตอนวิเคราะห์ใน #33)
- ไม่มีข้อมูลอาคารของเซลล์ → คอลัมน์อาคารเป็น null, `in_cover = false`
- จำนวนแถวต่อ (period, match_type) = จำนวนเซลล์ของ satellite batch (~6,829)

## PlanMerge: เมื่อไหร่ period ต้อง merge ใหม่

ยังไม่มี `curated/_success/merge/<period>.json` · หรือ marker ของฉาก/season/อาคาร หรือ manifest IoT ในช่วงวันที่ของ period
ใหม่กว่า `started_at` ใน marker · หรือ `sat_period` เปลี่ยน · หรือสั่ง `force`

อินพุต: `{}` / `{"scheduled": true}` (ทุก period ที่ stale) · `{"s3_key": ".../season_2024_03-05.json"}` (เฉพาะ period ที่ใช้ batch นั้น;
อาคาร = ทุก period) · `{"periods": [...], "mode": "force"}`

## รันมือ

```bash
SM=$(terraform -chdir=cloud/infra output -raw curated_merge_state_machine_arn)
aws stepfunctions start-execution --state-machine-arn "$SM" \
  --input '{"periods": ["scene_2024-03-17"], "mode": "force"}'
```

## ทดสอบ

```bash
python -m pytest -q cloud/analytics/curated_merge/tests     # ไม่แตะ AWS (Athena MERGE ทดสอบบน Learner Lab ตามขั้นตอนใน PR)
```
