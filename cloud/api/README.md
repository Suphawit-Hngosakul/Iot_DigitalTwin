# cloud/api (issue #18, #34)

API ให้เว็บดึงข้อมูล IoT ที่ผ่าน pipeline แล้ว (สเปค 40 §3) เว็บไม่ query Athena ตรง
Lambda `iot-dt-api` (Python 3.12, `LabRole`, stdlib + boto3 ไม่ใช้ layer) หลัง HTTP API ของตัวเอง แยกจาก API ingest
รายละเอียด request/response ดู `openapi.yaml`

| Method | Path | Query | อ่านจาก |
|---|---|---|---|
| GET | `/devices` | — | `devices.csv` + publish marker `curated/_success/iot/<batch_id>.json` (ไม่ใช้ Athena) |
| GET | `/latest` | `device` | `readings_1min` เฉพาะวันที่ของชั่วโมงล่าสุดที่ publish |
| GET | `/readings` | `device`, `from`, `to`, `interval=1s\|1min` (default 1min) | `readings_1s` (≤ 6 ชม.) / `readings_1min` (≤ 31 วัน) |
| GET | `/quality` | `device`, `from`, `to` (≤ 31 วัน) | `quality` รวมทุก session ต่อชั่วโมง |
| GET | `/layers` | `period` (ไม่ใส่ = composite ฤดูล่าสุด) | marker `curated/_success/satellite/` + `curated/satellite/layers/<period>/` (ไม่ใช้ Athena) |
| GET | `/grid` | `period`, `vars`, `match=window\|all` (default all) | `cell_period` (Iceberg, #32) → GeoJSON กริด 90 m |
| GET | `/grid/{cell_id}` | `period` | `cell_period` ทั้ง `window` และ `all` ของเซลล์เดียว (popup) |

`/analytics/{name}` รอ #33 (ใช้ Lambda ตัวเดียวกัน เพิ่ม route ทีหลัง)

### satellite (#34)

- `/layers` ตอบรายการ period ที่ publish แล้ว + ต่อ layer: `url` (presigned GET อายุ `LAYER_URL_TTL_S` = 3600 วินาที เพราะ bucket บล็อก public), `coordinates` (4 มุม [lon, lat] สำหรับ MapLibre), `legend`; `static_layers` = `building_density` (ต่อเซลล์ 90 m ไม่ขึ้นกับ period) อายุ URL จริงไม่เกินอายุ credentials ของ LabRole ใน Lambda
- `/grid` และ `/grid/{cell_id}` ไม่ใส่ `period` = composite ฤดูล่าสุดที่ publish; รับ `scene_…`, `season_…`, `month_YYYY-MM`; `vars` ต้องอยู่ใน `GRID_VARS` (ค่าเริ่ม `lst_mean,ndvi_mean,ndbi_mean,builtup_pct`) ไม่ถูกต้อง → 400 ก่อนถึง Athena; ไม่มีข้อมูล → 404
- รูปเซลล์ (Polygon) คำนวณจาก `cell_id` ใน Lambda ด้วย `common/grid.py` ไม่ต้องใช้ pyproj; ไม่มี footprint อาคารหรือเส้นเขต (D17)
- ค่า IoT ใน `/grid` เป็น `null` จนกว่า IoT จะเข้ากริดหลักสี่

## กติกา

- `from` / `to` เป็น ISO 8601 ไม่มี offset = เวลาไทย (เหมือน `DateTime` ใน CSV) มี offset (`Z`, `+07:00`) จะแปลงเป็นเวลาไทยให้ ช่วงเป็น [from, to)
- ช่วงเกินกำหนด, `to` ≤ `from`, `interval` ผิด → 400; เครื่องไม่อยู่ใน `devices.csv` → 404
- ทุก query มี `device_id = ...` และ `date IN (...)` เสมอ (วันที่สร้างจากช่วงเวลา ไม่ได้เอาข้อความจาก request ไปต่อ SQL)
- รอ Athena ไม่เกิน 25 วินาที (`ATHENA_TIMEOUT_S`) เกิน → ยกเลิก query แล้วตอบ 504; query ล้ม → 502
- ผลของ request เดียวกันเก็บในหน่วยความจำ Lambda 60 วินาที (`CACHE_TTL_S`) ไม่ cache error
- `/readings` ตอบเป็นตาราง `columns` + `rows` (array ของแถว) แทน object ต่อแถว เพราะ 1s × 6 ชม. ≈ 21,600 แถว ถ้าเป็น object จะเกินเพดาน response 6 MB ของ Lambda
- CORS ตั้งที่ API Gateway (`api_allowed_origins` ใน Terraform, default `["*"]` จนกว่าเว็บ #35 จะมีโดเมน)
- ข้อมูลอาคารออกได้แค่ค่าต่อเซลล์ 90 m ห้ามมี endpoint ที่คืนอาคารรายหลังหรือเส้นขอบเขตเขต (D17)

## ค่าตั้งค่า (env ของ Lambda, Terraform ใส่ให้)

`DATA_BUCKET`, `LAYER_URL_TTL_S=3600`, `ATHENA_DB=iot_walking`, `ATHENA_WORKGROUP=iot_walking`, `ATHENA_OUTPUT` (workgroup บังคับ output เองอยู่แล้ว), `ATHENA_TIMEOUT_S=25`, `CACHE_TTL_S=60`, `ALLOWED_ORIGINS` (ไว้ดูเฉยๆ CORS ทำที่ API GW)

## ทดสอบ

หน้าทดสอบบนเบราว์เซอร์ (มีแผนที่ + โหมดข้อมูลตัวอย่าง): เปิด `tester/index.html` ดูวิธีใช้ที่ `tester/README.md`


```bash
cd cloud/api
pip install -r requirements-dev.txt
python -m pytest -q
```

Athena ในเทสต์เป็นตัวปลอม (`FakeAthena`), S3 ใช้ moto

หลัง `terraform apply` (CloudShell):

```bash
API=$(terraform -chdir=cloud/infra output -raw api_url)
curl -s "$API/devices"
curl -s "$API/latest?device=IOTW-A001"
time curl -s "$API/readings?device=IOTW-A001&from=2026-11-16T00:00&to=2026-11-17T00:00" | head -c 300
time curl -s "$API/readings?device=IOTW-A001&from=2026-11-16T00:00&to=2026-11-17T00:00" > /dev/null   # ครั้งที่ 2 มาจาก cache
curl -s -o /dev/null -w "%{http_code}\n" "$API/readings?device=IOTW-A001&interval=1s&from=2026-11-16T00:00&to=2026-11-16T07:00"   # 400
curl -s "$API/quality?device=IOTW-A001&from=2026-11-16&to=2026-11-17"
```
