# cloud/api (issue #18, #34)

API ให้เว็บ อ่านจาก Athena (`processed/`)

- `GET /devices`
- `GET /latest?device=`
- `GET /readings?device=&from=&to=&interval=1s|1min`
- `GET /quality?device=&date=` (loss rate, แถวที่ถูกตัด)
- `GET /layers?period=` (PNG layer ดาวเทียม + legend)
- `GET /grid?period=&vars=`, `GET /grid/{cell_id}` (กริด 90 m)
- `GET /analytics/{name}?period=&match=` (ผลสถิติ S1–S9)

ข้อมูลอาคารออกได้แค่ค่าต่อเซลล์ 90 m ห้ามมี endpoint ที่คืนอาคารรายหลังหรือเส้นขอบเขตเขต

มี OpenAPI และ CORS ให้โดเมนเว็บ

