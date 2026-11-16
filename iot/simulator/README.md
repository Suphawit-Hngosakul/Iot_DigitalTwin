# iot/simulator

สคริปต์ Python จำลองอุปกรณ์ (issue #8) ใช้ทดสอบ ingest, hub และ pipeline โดยไม่ต้องใช้บอร์ดจริง

ต้องทำได้:
- สร้างแถว CSV ตาม schema ของ CSV แล้ว POST ไป ingest เป็นก้อน
- ส่ง JSON realtime ผ่าน WSS
- ใส่ข้อมูลเสีย / แถวซ้ำ / Record_ID ขาด / DateTime เป็นตัวเลขวินาที ได้ตามสั่ง
