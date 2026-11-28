# web/

Vite + TypeScript + MapLibre GL JS + ECharts — build เป็นไฟล์ static แล้ว deploy ขึ้น S3 + CloudFront (spec 70, D14)

- แท็บแผนที่: layer LST, NDVI, NDBI, ความหนาแน่นสิ่งปลูกสร้าง, อาคาร 3D, กริด 90 m, hot spot, เส้นทาง IoT, ตำแหน่งปัจจุบัน, หมุด station
- แท็บสถิติ: สรุปปัจจัยที่ทำให้เกิดความร้อน (ผลจาก cloud/analytics) + หน้าเทียบงานวิจัย UHI
- แท็บ IoT: ค่าสด realtime และกราฟข้อมูลย้อนหลัง, คุณภาพข้อมูล

## คำสั่ง

```bash
npm ci               # ติดตั้ง (ครั้งแรก)
npm run dev          # เปิดที่ http://localhost:5173
npm run build        # tsc + vite build → web/dist
npm run lint         # ESLint
npm run format       # Prettier จัดรูปแบบทุกไฟล์ (format:check = ตรวจอย่างเดียว)
```

## ค่าตั้งตอน runtime (`public/config.json`)

```json
{
  "api_url": "https://xxxx.execute-api.ap-southeast-2.amazonaws.com",
  "hub_live_url": "ws://<eip>:8081/live",
  "deploy_id": "20261128T000000Z"
}
```

- อ่านครั้งเดียวตอนเปิดหน้าใน `src/config.ts` จึงเปลี่ยน URL ได้โดยไม่ต้อง build ใหม่ (`make-config.sh` / `cloud/infra/deploy.py` สร้างให้)
- `api_url` ว่าง → ไม่มีข้อมูลให้แสดง (ใช้ตอนพัฒนาในเครื่องเท่านั้น)
- `hub_live_url` ว่าง → แท็บ Realtime ถาม `/latest` ทุก 2 วินาทีแทน WebSocket

อาคาร 3D อ่านจาก `public/data/buildings.geojson` (แปลงจาก `building.shp` ด้วย `scripts/convert_buildings.py`, D26) — ไฟล์นี้ไม่ commit

## โครงโค้ด

```
src/
├── main.ts              # ประกอบทุกแท็บ, สลับแท็บ/มุมมองย่อย, ส่งต่อ period + โหมดเวลา
├── config.ts            # อ่าน config.json → CFG
├── api.ts               # api(path, params) ตัวเดียวของทั้งเว็บ
├── types.ts             # รูปร่าง JSON ตาม cloud/api/openapi.yaml
├── period-selector.ts   # ตัวเลือก period แบบ cascade (รูปแบบ → ปี → เดือน → วัน)
├── geo/                 # AOI + cell_id ของกริด, สี legend เริ่มต้น
├── map/                 # แท็บแผนที่: 1 ไฟล์ต่อ layer
│   ├── map-controller.ts   # สร้างแผนที่ + ลำดับโหลด layer
│   ├── context.ts          # สถานะที่ layer ใช้ร่วมกัน (period, grid, devices)
│   ├── drawer.ts           # แผง Dashboard ด้านขวา + ไฮไลต์
│   ├── satellite-layers.ts # PNG 30 m จาก /layers
│   ├── grid-layer.ts       # กริด 90 m จาก /grid + Dashboard เซลล์
│   ├── buildings-layer.ts  # อาคาร 3D + Dashboard อาคาร
│   ├── hotspot-layer.ts    # Gi* จาก /analytics/hotspot
│   ├── devices-layer.ts    # /devices → ตัวเลือกเครื่อง + หมุด station
│   ├── track-layer.ts      # เส้นทางเดินจาก /readings
│   ├── live-position.ts    # ตำแหน่งปัจจุบันจาก /latest
│   ├── heat-verdict.ts     # ข้อความประเมินความร้อน + คำอธิบายตัวแปร
│   └── stops.ts            # สีของกริด/เส้นทาง
├── stats/               # แท็บสถิติ (stats-view) + หน้าเทียบงานวิจัย (research-view)
├── iot/                 # แท็บ IoT: iot-live (+ live-charts), iot-history
├── utils/               # esc/byId, format ตัวเลข-วันที่, legend
└── styles/              # CSS แยกตามส่วน, index.css import ตามลำดับ cascade
```

หลักการ: ทุก request ผ่าน `api()` ใน `api.ts`, ทุกข้อความที่มาจากข้อมูลต้องผ่าน `esc()` ก่อนแทรกลง `innerHTML`, สีคงที่ใส่เป็น class ใน `styles/` (inline style เหลือเฉพาะค่าที่คำนวณตอน runtime เช่นแถบสี legend)

## สัญญากับ deploy (อย่าเปลี่ยนโดยไม่แก้ `cloud/infra/`)

- build ออกที่ `web/dist` และ `base: "./"`
- `config.json` อ่านตอน runtime ด้วย `fetch("./config.json")`
- `buildings.geojson` อยู่ที่ `public/data/` → `dist/data/`
- ไฟล์ใน `dist/assets/` มี hash ในชื่อ (CloudFront cache แบบ immutable) — ไลบรารีแยกเป็น chunk `maplibre-*.js`, `echarts-*.js`
