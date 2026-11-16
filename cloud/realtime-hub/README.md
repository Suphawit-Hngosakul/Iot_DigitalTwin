# cloud/realtime-hub (issue #10, #23)

WebSocket hub บน EC2 (Docker + reverse proxy ทำ TLS เช่น Caddy)

- ช่องอุปกรณ์: รับ text frame JSON ค่าเซนเซอร์ (+ binary frame เสียงในเฟสเว็บ) ต้องมี token
- ช่องเว็บ: subscribe ตาม device_id
- EC2 ใน Learner Lab ถูกปิดทุก ~4 ชม. → realtime ใช้ได้เฉพาะตอน EC2 เปิด (ข้อมูลจริงไม่ผ่านทางนี้)
