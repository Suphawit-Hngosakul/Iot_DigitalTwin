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

