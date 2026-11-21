# cloud/satellite

- `ingest/` — Lambda `satellite_ingest` (#27) ดึง Landsat 8/9 (LST, NDVI, NDBI) จาก Google Earth Engine ลง S3 `raw/satellite/` (ดู "satellite_ingest" ด้านล่าง) และสคริปต์ GEE Code Editor (`*.js`, ไม่เข้า zip ของ Lambda)
- `pipeline/` — Step Functions `iot-dt-sat-pipeline` (#30, #31, spec 51) ตรวจ batch ใน `raw/satellite/` ด้วย data contract แล้วเขียน grid 90 m ลง `trusted/` + PNG layers ลง `curated/satellite/layers/` (ดู `pipeline/README.md`)
- `layer/` — สูตรสร้าง Lambda layer `earthengine-api` + `google-auth` (ใช้ร่วมกันทั้ง test Lambda และ `satellite_ingest`)
- `test_lambda/` — Lambda ทดสอบว่าเรียก GEE ได้ (#25)

## ยืนยันตัวกับ GEE (#25)

ใช้ **Workload Identity Federation (WIF)** แทน service-account key: Lambda เอา credentials ของ `LabRole` ไปแลกเป็น token ของ Google แล้ว impersonate service account `service-gee-iot@turnkey-tribute-511903-t6.iam.gserviceaccount.com` จึงไม่มี key ใน repo หรือใน SSM

ค่า WIF (project number, pool, provider, service account) อยู่ในตัวแปร `gee_wif` ของ `cloud/infra/variables.tf` ไม่ใช่ความลับ Terraform สร้างเป็น JSON ส่งเข้า env `GEE_WIF_CONFIG` ของ Lambda ถ้าเปลี่ยน project (spec 50 ทาง ข) ให้ override `gee_project`, `gee_assets`, `gee_wif` ใน `terraform.tfvars` โดยไม่ต้องแก้โค้ด

ฝั่ง Google ต้องมี: identity pool + AWS provider ที่ผูกกับ AWS account ของ Learner Lab, สิทธิ์ `roles/iam.workloadIdentityUser` ให้ principal ของ `LabRole` บน service account และ asset ที่แชร์ให้ service account แล้ว

## สร้าง layer แล้ว deploy

```bash
# ใน cloud/infra/terraform.tfvars: satellite_enabled = true
cd cloud/infra && terraform apply   # รัน build_layer.py ให้เอง -> layer/build/gee-layer.zip (~30 MB)
```

- Terraform รัน `build_layer.py` ให้เองตอน apply ครั้งแรก และรันใหม่เฉพาะตอน `requirements.txt` หรือ `build_layer.py` เปลี่ยน เครื่องที่รัน Terraform ต้องมี Python + pip และออกเน็ตได้ (ตั้ง `python_cmd = "python3"` ถ้าเครื่องไม่มีคำสั่ง `python`)
- ถ้ามี zip สำเร็จรูปอยู่แล้ว ตั้ง `gee_layer_zip` แทน Terraform จะไม่ build เอง
- `build_layer.py` ดึง wheel ของ manylinux x86_64 / Python 3.12 ให้เอง รันบน Windows ได้ (รันมือก็ได้ถ้าอยากเช็กก่อน)
- zip อยู่ใน `build/` ที่ gitignore ไว้ ห้าม commit ไฟล์ zip ถ้าจะเปลี่ยนเวอร์ชันให้แก้ `layer/requirements.txt`
- `satellite_enabled = true` สร้างทั้ง layer, test Lambda และ `satellite_ingest` + EventBridge รายวันในครั้งเดียว (ใช้ layer และค่า WIF ชุดเดียวกัน)

## ทดสอบ

```bash
aws lambda invoke --function-name iot-dt-satellite-test --payload '{}' \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
# {"status": "ok", "calc_result": 2, "asset_path": "...laksi_boundary", "feature_count": 2}
```

ถ้าล้มเหลว invocation จะขึ้น error (ดู log `/aws/lambda/iot-dt-satellite-test`) สาเหตุที่พบบ่อย: AWS account ของ Learner Lab ไม่ตรงกับที่ผูกใน provider, service account ยังไม่ได้สิทธิ์ `workloadIdentityUser`, asset ยังไม่ได้แชร์

## satellite_ingest (#27)

Lambda `iot-dt-satellite-ingest` (`ingest/handler.py` + `ingest/ingest.py`, timeout 900 s) ดึง Landsat 8/9 C2 L2 ของ AOI (`gee_assets`) แล้วเขียนลง bucket เดียวกับ IoT

- **รอบรัน:** EventBridge วันละครั้ง 09:00 น. เวลาไทย (`satellite_ingest_schedule`, ใส่ `""` = รันมืออย่างเดียว) มองย้อน 35 วันเพราะ L2 ขึ้น GEE ช้าได้หลายวัน
- **ต่อวันที่:** รวมภาพวันเดียวกัน (สองแถว WRS / L8+L9) เป็น mosaic เดียว → ถ้าเมฆใน AOI > `satellite_max_cloud_pct` (40) หรือพิกเซลใสน้อยกว่า `satellite_min_valid_ratio` (0.15) บันทึกเหตุผลใน `skipped/` แล้วข้าม
- **ไม่ดึงซ้ำ:** วันที่มี `_COMPLETE` หรือมีบันทึก `skipped/` แล้วจะไม่ถูกประเมินใหม่ ยกเว้นส่ง `"force": true`
- **composite หน้าร้อน:** ถ้ารอบนั้นเจอวันที่ใหม่ในช่วง มี.ค.–พ.ค. จะสร้าง `season_<ปี>_03-05` ใหม่ให้เอง เฉพาะเมื่อชุดวันที่ต่างจาก `metadata.json` เดิม (พ้นฤดูแล้วจึงหยุดเอง)
- **กริด:** ทุก reduce/download ใช้ `crsTransform = [30, 0, 660015, 0, -30, 1545015]` (ตรงกับ `cloud/pipeline/common/grid.py`) พิกเซลจึงตรงกับ pixel grid ของ Landsat และกริด 90 m ไม่ถูก resample (D11)
- **error:** ถ้ามีวันที่หรือ composite ที่ล้ม ส่วนที่สำเร็จยังเขียนลง S3 และอัปเดต catalog แต่ invocation จะ fail (เห็นใน Lambda Errors / log) แล้วรันซ้ำได้เพราะ idempotent

### โครงสร้างใน S3

```
raw/satellite/scene_<YYYY-MM-DD>/        # 1 วันที่ = 1 batch (ตรงกับ satellite_schema.scene_period)
├── bands.tif        # lst_c, ndvi, ndbi, cloud_mask (1 = ใส), nodata -9999, EPSG:32647 30 m
├── pixels.csv       # x, y (จุดกลางพิกเซล EPSG:32647), lon, lat, lst_c, ndvi, ndbi, cloud_mask
├── metadata.json    # scene_ids, sensor, cloud_pct, valid_ratio, เกณฑ์ที่ใช้, crs_transform
└── _COMPLETE        # เขียนเป็นไฟล์สุดท้าย (ลบก่อนเขียนทับ)
raw/satellite/season_<YYYY>_03-05/       # median หน้าร้อน โครงเดียวกัน แต่คอลัมน์สุดท้ายเป็น n_valid
raw/satellite/skipped/<YYYY-MM-DD>.json  # วันที่ที่ถูกข้าม + เหตุผล
curated/satellite/catalog.json           # สารบัญ: ฉากล่าสุดที่ใช้ได้, composite ย้อนหลัง, สถานะรายเดือน (ให้เว็บ/API)
```

`bands.tif` ถูก clip ตามขอบเขตจาก `laksi_boundary` (ข้อมูล กทม. ห้ามเผยแพร่, D17) ใช้ภายใน pipeline เท่านั้น ห้ามส่งไฟล์นี้ตรงไปเว็บ

### รันมือ / backfill

timeout ของ Lambda สูงสุด 900 s พอสำหรับประมาณ 1–2 เดือนต่อครั้ง ให้ backfill ทีละช่วง (แนะนำ 2021–ปัจจุบัน)

```bash
# ดึงรายฉากทีละ 2 เดือน (composite ของฤดูในช่วงนั้นสร้างให้อัตโนมัติ)
aws lambda invoke --function-name iot-dt-satellite-ingest --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode": "ingest", "start_date": "2024-01-01", "end_date": "2024-03-01"}' out.json

# สร้าง composite หน้าร้อนของปีหนึ่งโดยตรง / บังคับสร้างใหม่
aws lambda invoke --function-name iot-dt-satellite-ingest --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode": "composite", "year": 2024, "force": true}' out.json

# ดูผลโดยไม่เขียน S3
--payload '{"mode": "ingest", "start_date": "2024-03-01", "end_date": "2024-04-01", "dry_run": true}'
```

ถ้า Lambda ติดเรื่องเวลา/ dependency: รัน `ingest.py` ฟังก์ชันเดียวกันบนเครื่องแล้วอัปขึ้น S3 ได้ (ผลใน S3 เหมือนกัน spec 50 §4)

### เทสต์

```bash
cd cloud/satellite/ingest
pip install -r requirements-dev.txt
python -m pytest -q tests     # ไม่เรียก GEE/AWS จริง (ee ถูก stub, S3 เป็นของปลอมในหน่วยความจำ)
```
