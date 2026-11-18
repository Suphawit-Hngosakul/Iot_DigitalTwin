# cloud/

ทุกอย่างบน AWS (Learner Lab, region us-east-1, ทุกบริการใช้ `LabRole`)

| โฟลเดอร์ | เนื้อหา | รันที่ |
|---|---|---|
| `ingest/` | Lambda รับ CSV จากอุปกรณ์ → S3 `raw/` | API Gateway + Lambda |
| `presign/` | Lambda ออก presigned URL ให้อุปกรณ์อัป WAV | API Gateway + Lambda |
| `realtime-hub/` | WebSocket hub (realtime + สตรีมเสียงในเฟสเว็บ) | systemd บน EC2 ตัวเดียวกับ relay (D22) |
| `pipeline/` | Step Functions + Lambda 5 ตัว (รายชั่วโมง) | Step Functions |
| `api/` | API ให้เว็บดึงข้อมูลที่ผ่าน pipeline (อ่าน Athena) | API Gateway + Lambda |
| `satellite/` | ดึง Landsat + ข้อมูลอาคารจาก GEE และ pipeline ดาวเทียม → กริด 90 m + layer | Lambda (container) + Step Functions |
| `analytics/` | สถิติหาปัจจัยความร้อน S1–S9 | Lambda/ECS + Step Functions |
| `infra/` | IaC (Terraform/OpenTofu) + docker-compose ของ EC2 | — |
