# cloud/analytics

รวมข้อมูล IoT + ดาวเทียมบนกริดกลาง แล้วคำนวณสถิติ S1–S9 (correlation, regression, RF importance, hot spot, Moran's I, รูปแบบรายวันของ station) ลง `processed/analytics/`

## curated_merge (#32)

ตารางรวม `curated.cell_period` (Iceberg) สร้างโดย `curated_merge/` ดู `curated_merge/README.md` สถิติ S1–S9 (#33) อ่านจากตารางนี้เท่านั้น
