# Iot_DigitalTwin — Special Project 2

ระบบเดินเก็บข้อมูลสิ่งแวดล้อมเมือง (เสียง, อุณหภูมิ, ความชื้น, แสง, GPS) ด้วย ESP32 ทั้งแบบเดินเก็บและแบบตั้งประจำจุด (station) ร่วมกับข้อมูลดาวเทียม Landsat (LST, NDVI, NDBI) และความหนาแน่นสิ่งปลูกสร้าง ผ่าน Data Pipeline บน Step Functions เพื่อวิเคราะห์ปัจจัยที่ทำให้เกิดความร้อน และแสดงผลเป็น layer บนเว็บ

## โครงสร้าง

```
Iot_DigitalTwin/
├── iot/
│   ├── firmware/        # ESP32 Arduino sketch
│   ├── hardware/        # ตารางขา, ไฟล์ 3D
│   └── simulator/       # จำลองอุปกรณ์สำหรับทดสอบ
├── cloud/
│   ├── ingest/          # Lambda รับ CSV → S3 raw
│   ├── presign/         # Lambda ออกลิงก์อัป WAV
│   ├── realtime-hub/    # WebSocket hub บน EC2
│   ├── pipeline/        # Step Functions + Lambda รายชั่วโมง
│   ├── api/             # API ให้เว็บ (Athena)
│   ├── satellite/       # GEE → S3 + pipeline ดาวเทียม
│   ├── analytics/       # สถิติหาปัจจัยความร้อน
│   └── infra/           # IaC + docker-compose
├── web/                 # แผนที่ layer + แดชบอร์ดสถิติ
└── docs/                # เอกสารสำหรับคนอ่าน (ไดอะแกรม, runbook, เล่ม)
```

## สถานะ

| ส่วน | issue | สถานะ |
|---|---|---|
| data schema + devices.csv | #2 | merge แล้ว |
| กริด 90 m + schema ดาวเทียม | #29 | merge แล้ว |
| spike RAM ตอน TLS + อัด WAV | #4 | เสร็จ (branch ยังไม่ merge) — สรุป: บอร์ดนี้ทำ TLS ไม่ได้ระหว่างอัดเสียง |
| Wi-Fi reconnect | #5 | merge แล้ว |
| ingest Lambda + Terraform (storage, ingest) | #9 | merge แล้ว |
| อัป CSV ตาม cursor + backfill (ผ่าน EC2 HTTP relay) | #7 | ทดสอบบนบอร์ด A001 ผ่าน (2026-11-18) รอ merge |
| realtime / อัป WAV / pipeline / web | #6 / #20 / สเปค 30 / สเปค 70 | ยังไม่เริ่ม |
