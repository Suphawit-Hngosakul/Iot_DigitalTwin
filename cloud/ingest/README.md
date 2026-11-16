# cloud/ingest (issue #9)

`POST /ingest` — body เป็น CSV ดิบ ~300 แถว

Headers: `Authorization: Bearer <device token>`, `X-Device-Id`, `X-Chunk-Id: <session>-<first_record>-<last_record>`

หน้าที่: เช็ค token → เช็ค Device_ID ในแถวตรงกับ header → แยกแถวตามชั่วโมง (เวลาไทย) → เขียน
`raw/csv/device_id=<id>/date=YYYY-MM-DD/hour=HH/part-<chunk_id>.csv` → ตอบ 200

ส่งซ้ำ = ชื่อไฟล์เดิม = เขียนทับ (idempotent)
