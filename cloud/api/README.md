# cloud/api (issue #18, #34)

API ให้เว็บดึงข้อมูล IoT ที่ผ่าน pipeline แล้ว (สเปค 40 §3) เว็บไม่ query Athena ตรง
Lambda `iot-dt-api` (Python 3.12, `LabRole`, stdlib + boto3 ไม่ใช้ layer) หลัง HTTP API ของตัวเอง แยกจาก API ingest
รายละเอียด request/response ดู `openapi.yaml`

| Method | Path | Query | อ่านจาก |
|---|---|---|---|
| GET | `/devices` | — | `devices.csv` + publish marker `processed/_manifests/<batch_id>.json` (ไม่ใช้ Athena) |
| GET | `/latest` | `device` | `readings_1min` เฉพาะวันที่ของชั่วโมงล่าสุดที่ publish |
| GET | `/readings` | `device`, `from`, `to`, `interval=1s\|1min` (default 1min) | `readings_1s` (≤ 6 ชม.) / `readings_1min` (≤ 31 วัน) |
| GET | `/quality` | `device`, `from`, `to` (≤ 31 วัน) | `quality` รวมทุก session ต่อชั่วโมง |

endpoint ดาวเทียม `/layers`, `/grid`, `/analytics/{name}` ทำใน #34 (ใช้ Lambda ตัวเดียวกัน เพิ่ม route)

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

`DATA_BUCKET`, `ATHENA_DB=iot_walking`, `ATHENA_WORKGROUP=iot_walking`, `ATHENA_OUTPUT` (workgroup บังคับ output เองอยู่แล้ว), `ATHENA_TIMEOUT_S=25`, `CACHE_TTL_S=60`, `ALLOWED_ORIGINS` (ไว้ดูเฉยๆ CORS ทำที่ API GW)

## ทดสอบ

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
