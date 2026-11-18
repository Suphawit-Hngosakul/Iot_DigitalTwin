# cloud/satellite

ระบบดึงและประมวลผลข้อมูลดาวเทียม Landsat 8/9 (`LC08/C02/T1_L2`, `LC09/C02/T1_L2`) พื้นที่เขตหลักสี่ (`EPSG:32647`, Grid 30 เมตร, Origin `660015.0, 1545015.0`) จาก Google Earth Engine (GEE) ลง S3

- `ingest/` — AWS Lambda (`iot-dt-satellite-ingest`) เชื่อมต่อ GEE ผ่าน Workload Identity Federation (WIF) โดยไม่ใช้ Key File คำนวณค่า `lst_c`, `ndvi`, `ndbi` และ `cloud_mask` พร้อมรวมภาพถ่ายวันเดียวกัน (Mosaic)
  - `handler.py` — Entry point รองรับโหมด `ingest` (ค่าเริ่มต้น ดึงย้อนหลัง 35 วัน), `composite` และ `catalog`
  - `ingest.py` — ตรรกะคัดกรองเมฆ (`max_cloud_pct <= 75%`, `min_valid_ratio >= 15%`), แทนค่าพิกเซลเมฆ/นอกพื้นที่ด้วย `nodata = -9999.0` และสร้างสารบัญ `catalog.json`
  - `gee-wif-config.json` — ค่ากำหนด WIF สำหรับแลกสิทธิ์จาก AWS IAM Role ไปยัง GCP Service Account
  - `spike26_single_scene.js` — สคริปต์ GEE Code Editor ต้นแบบสำหรับตรวจสอบสูตรและค่า Grid
- `build_layer.sh` — สคริปต์สร้าง Lambda Layer (`gee-layer.zip`: `earthengine-api`, `google-auth`, `requests` สำหรับ Python 3.12 `x86_64`)
- `pipeline/` — Step Functions และ Schema กลาง (`common/satellite_schema.py`, `common/grid.py`) สำหรับแปลงข้อมูลเข้าสู่ตารางระดับ Grid 90 เมตร

## โครงสร้างข้อมูลใน S3 (`iot-dt-<account-id>`)

- `raw/satellite/scene_<YYYY-MM-DD>/` — ข้อมูลรายฉากที่ผ่านเกณฑ์ความชัด (รวมภาพถ่ายวันเดียวกันแล้ว) ประกอบด้วย `bands.tif`, `pixels.csv`, `metadata.json` และ `_COMPLETE`
- `raw/satellite/season_<YYYY>_03-05/` — ค่ามัธยฐาน (Median Composite) ช่วงหน้าร้อน (มี.ค.–พ.ค.) พร้อมแบนด์นับจำนวนครั้งที่ท้องฟ้าเปิด (`n_valid`)
- `raw/satellite/skipped/<YYYY-MM-DD>.json` — บันทึกเหตุผลการข้ามฉากที่เมฆปกคลุมเกินเกณฑ์
- `curated/satellite/catalog.json` — สารบัญกลางเก็บตัวชี้เป้าฉากล่าสุดที่ใช้ได้จริง (`latest_valid_scene`), รายการหน้าร้อนย้อนหลัง และสถานะรายเดือนสำหรับให้ Frontend ดึงไปสร้างตัวเลือกอัตโนมัติ

## การสั่งรันย้อนหลัง (Backfill)

```bash
# สร้าง Seasonal Composite หน้าร้อนรายปี
aws lambda invoke --function-name iot-dt-satellite-ingest --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode": "composite", "year": 2024}' response.json

# ดึงข้อมูลรายฉากตามช่วงวันที่กำหนด
aws lambda invoke --function-name iot-dt-satellite-ingest --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode": "ingest", "start_date": "2024-01-01", "end_date": "2024-07-01"}' response.json