# web/mock — หน้าเว็บตัวอย่าง (ข้อมูลสมมติ)

หน้าเว็บไฟล์เดียวที่ทำให้ดูว่า **เว็บจริง (spec 70, #35–#38, #24) จะเอาข้อมูลจาก API มาแสดงอย่างไร** ทุกค่าบนจอเป็นข้อมูลสมมติจาก `mock-api.js` แต่ JSON ที่ได้มีรูปร่างเดียวกับ `cloud/api/openapi.yaml`

ไม่ใช่เว็บจริง: ไม่มี build, ไม่มี TypeScript, ไม่ deploy ใช้ดู/อธิบาย flow เท่านั้น เว็บจริงทำใน `web/` ตาม #35

## เปิดดู

ดับเบิลคลิก `index.html` (Chrome) ได้เลย ต้องต่อเน็ตเพราะโหลด MapLibre, ECharts และ basemap จาก CDN
หรือรันเซิร์ฟเวอร์เล็กๆ: `python -m http.server 8000` ในโฟลเดอร์นี้ แล้วเปิด http://localhost:8000

## มีอะไรบ้าง

| แท็บ | ส่วน | เรียก |
|---|---|---|
| ① ข้อมูลไหลอย่างไร | ไดอะแกรม pipeline + ตาราง "ส่วนบนจอ ← endpoint ← ตารางจริง" + ช่องลองเรียก API | ทุก endpoint |
| ② แผนที่ | layer LST/NDVI/NDBI (PNG 30 m), ความหนาแน่นอาคาร, กริด 90 m, hot spot, เส้นทางเดิน, หมุด station, จุด realtime, คลิกเซลล์ดู popup | `/layers`, `/grid`, `/grid/{cell_id}`, `/analytics/hotspot`, `/readings`, `/devices`, `/latest`, ws `/live` |
| ③ สถิติ | S1 การ์ด + ข้อความสรุป, S2 heatmap, scatter, S3 vs S5, S8, S7, S9 | `/analytics/{name}`, `/grid` |
| ④ IoT | กราฟ dB(SPL) (+dBA), อุณหภูมิ, ความชื้น, แสง, loss_rate รายชั่วโมง, ค่าสด + ปุ่มฟังเสียงสด | `/readings`, `/quality`, ws `/live` |

- ปุ่มสีฟ้า `GET /…` ข้างแต่ละส่วน → เปิด JSON ล่าสุดที่ส่วนนั้นได้รับ
- ปุ่ม **API log** มุมขวาบน → รายการ request ทุกตัวที่หน้านี้ยิง (สถานะ, เวลา, ขนาด)
- `period` / `match` ด้านบน → เปลี่ยน layer, กริด, hot spot และสถิติพร้อมกัน

## โครงไฟล์

```
web/mock/
├── index.html    # หน้าจอ + ค่าตั้ง APP_CONFIG (API_URL / LIVE_URL)
├── make-config.sh # terraform output → config.js (ไม่ commit)
├── mock-api.js   # API ปลอม: MockAPI.handle(path, params) + FakeLiveSocket
├── app.js        # api() ตัวเดียว → วาดด้วย MapLibre / ECharts
└── style.css
```

## ใช้ข้อมูลจริงจาก terraform

ถ้าไม่ได้ใส่ URL ใดๆ หน้าเว็บจะเป็นโหมด **MOCK** ทั้งหมด ถ้าใส่ `api_url` จะเป็นโหมด **REAL API** แทน มี 3 วิธีให้เลือก:

1. **กดปุ่ม "API จริง ⚙" บนหัวเว็บ** แล้ววางค่าจากคำสั่ง (รันใน `cloud/infra/` ที่มี state เช่น CloudShell)
   ```bash
   terraform output -raw api_url
   terraform output -raw hub_live_url
   ```
   ค่าจะเก็บไว้ใน localStorage ของเบราว์เซอร์ ถ้าอยากกลับไปใช้ mock ให้กด "กลับไปใช้ mock"
2. **ใส่ต่อท้าย URL** แบบ `index.html?api=https://xxxx.execute-api.us-east-1.amazonaws.com&live=ws://<eip>:8081/live`
3. **สร้าง `config.js`** ด้วย `cd cloud/infra && bash ../../web/mock/make-config.sh` แล้ววางไฟล์ไว้ข้าง `index.html` (ไฟล์นี้อยู่ใน `.gitignore` จะไม่ถูก commit)

ในโหมด REAL:

| ส่วน | มาจาก |
|---|---|
| `/devices`, `/latest`, `/readings`, `/quality` (#18) | API จริง |
| `/layers`, `/grid`, `/grid/{cell_id}` (#34) | API จริง ถ้ายังไม่มี period ที่ publish จะขึ้นข้อความ 404 สีแดงในแผงนั้น |
| `/analytics/*` | **ยังเป็น mock** (รอ #33) แท็บสถิติจะมีแถบเหลืองบอก ส่วน hot spot บนแผนที่จะปิดไว้ |
| ws `/live` | hub จริงจาก `hub_live_url` ถ้า EC2 ปิดอยู่จะขึ้นว่าต่อไม่ได้ |

- API log จะติดป้าย `real` หรือ `mock` ให้ทุก request
- legend อ่านจาก `legend` ของ `/layers` (stops แบบ `[t 0..1, [r,g,b]]` + min/max ตาม `satellite_grid._legend`)
- วันที่เริ่มต้นของกราฟและเส้นทางเดินจะใช้ `latest_hour_local` ของเครื่องนั้นจาก `/devices`
- CORS: API GW ตั้ง `api_allowed_origins = ["*"]` ไว้แล้ว จึงเปิดจาก `file://` ได้

`app.js` ดึงข้อมูลผ่านฟังก์ชัน `api(path, params)` ตัวเดียว โหมด mock เรียก `MockAPI.handle()` ส่วนโหมดจริงเรียก `fetch(API_URL + path)` โค้ดส่วนวาดจึงเป็นชุดเดียวกันทั้งสองโหมด

## ข้อมูลสมมติเป็นแบบไหน

- พื้นที่สี่เหลี่ยมแถวหลักสี่ 48 × 42 เซลล์ (~4.3 × 3.8 กม.) กริดสร้างบน lon/lat ขนาดประมาณ 90 m — ของจริงใช้ EPSG:32647 ตรงกับ pixel Landsat (D11) แต่ `cell_id` มีรูปแบบเดียวกัน (`c0020_r0021`)
- "โลก" มีสวน 4 จุด ย่านหนาแน่น 3 จุด และถนนใหญ่ 2 เส้น → LST สูงตรงที่หนาแน่น/ถนน ต่ำในสวน, dB(SPL) สูงใกล้ถนน
- period: `season_2025_03-05`, `season_2024_03-05`, ฉาก 3 ฉาก และ `month_2026-11` (เฉพาะ grid/สถิติ)
- เครื่อง: `IOTW-A001` เดิน 08–12 น., `IOTW-A002` เดิน 15–18 น. (ทุกวัน 14–21 พ.ย. 2026), station สมมติ `IOTW-S001` (สวน) และ `IOTW-S002` (ริมถนน) เก็บตลอด 24 ชม. — ของจริงยังไม่มี station ใน `devices.csv`
- ชั่วโมงล่าสุดที่ publish = `2026-11-21T11:00` (เวลาไทย)
- กฎ API ที่จำลอง: `interval=1s` เกิน 6 ชม. / `1min` เกิน 31 วัน → 400, เครื่องไม่รู้จัก → 404, `vars` นอก allowlist → 400, หน่วงเวลาเหมือน Athena
- `/layers`: PNG สร้างด้วย canvas แล้วส่งเป็น data URL (ของจริงเป็น presigned GET ของ S3)
- `/analytics/*`: ยังไม่มีใน `openapi.yaml` (รอ #33) รูปร่างใน `mock-api.js` เป็น**ข้อเสนอ** ตัวเลข S1–S3, S6–S9 คำนวณจริงจากกริด mock (OLS, Pearson, Gi*, Moran's I แบบหยาบ) ส่วน S5 (RF) แต่งจาก |std coef|
- ws `/live`: `FakeLiveSocket` ส่ง `status` + `reading` ทุก 1 วินาที (รูปแบบ spec 01 §3 + `received_at`) และ binary frame เสียง 804 ไบต์ 10 frame/วินาที ตาม header ใน spec 90 (เนื้อเสียงเป็นไบต์สุ่ม เสียงที่ได้ยินคือ noise ที่ดังตาม dB(SPL))

## สิ่งที่ mock ไม่ได้ทำแบบของจริง

- ไม่ถอด IMA-ADPCM / AudioWorklet / jitter buffer
- ไม่มีตัวเลือกฉาก Landsat แบบ timeline (มีแค่ dropdown period)
- ข้อมูลอาคารเป็นค่าต่อเซลล์ 90 m เท่านั้น (ตาม D17) ไม่มีรูปอาคารหรือเส้นขอบเขต
