# cloud/satellite

- `ingest/` — Lambda ดึง Landsat + ข้อมูลอาคาร (building.shp ของ กทม. + Open Buildings) จาก Google Earth Engine ลง S3 `raw/satellite/`
- `pipeline/` — Step Functions ดาวเทียม → grid 90 m + PNG layers
- `layer/` — สูตรสร้าง Lambda layer `earthengine-api` + `google-auth` (ใช้ร่วมกันทุก Lambda ที่เรียก GEE)
- `test_lambda/` — Lambda ทดสอบว่าเรียก GEE ได้ (#25)

## ยืนยันตัวกับ GEE (#25)

ใช้ **Workload Identity Federation (WIF)** แทน service-account key: Lambda เอา credentials ของ `LabRole` ไปแลกเป็น token ของ Google แล้ว impersonate service account `service-gee-iot@turnkey-tribute-511903-t6.iam.gserviceaccount.com` จึงไม่มี key ใน repo หรือใน SSM

ค่า WIF (project number, pool, provider, service account) อยู่ในตัวแปร `gee_wif` ของ `cloud/infra/variables.tf` ไม่ใช่ความลับ Terraform สร้างเป็น JSON ส่งเข้า env `GEE_WIF_CONFIG` ของ Lambda ถ้าเปลี่ยน project (spec 50 ทาง ข) ให้ override `gee_project`, `gee_assets`, `gee_wif` ใน `terraform.tfvars` โดยไม่ต้องแก้โค้ด

ฝั่ง Google ต้องมี: identity pool + AWS provider ที่ผูกกับ AWS account ของ Learner Lab, สิทธิ์ `roles/iam.workloadIdentityUser` ให้ principal ของ `LabRole` บน service account และ asset ที่แชร์ให้ service account แล้ว

## สร้าง layer แล้ว deploy

```bash
python cloud/satellite/layer/build_layer.py      # -> cloud/satellite/layer/build/gee-layer.zip (~30 MB)
# ใน cloud/infra/terraform.tfvars: satellite_enabled = true
cd cloud/infra && terraform apply
```

- `build_layer.py` ดึง wheel ของ manylinux x86_64 / Python 3.12 ให้เอง รันบน Windows ได้
- zip อยู่ใน `build/` ที่ gitignore ไว้ ห้าม commit ไฟล์ zip ถ้าจะเปลี่ยนเวอร์ชันให้แก้ `layer/requirements.txt`
- `terraform output gee_layer_arn` คือ layer ที่ Lambda `satellite_ingest` (#27) ใช้ต่อได้

## ทดสอบ

```bash
aws lambda invoke --function-name iot-dt-satellite-test --payload '{}' \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
# {"status": "ok", "calc_result": 2, "asset_path": "...laksi_boundary", "feature_count": 2}
```

ถ้าล้มเหลว invocation จะขึ้น error (ดู log `/aws/lambda/iot-dt-satellite-test`) สาเหตุที่พบบ่อย: AWS account ของ Learner Lab ไม่ตรงกับที่ผูกใน provider, service account ยังไม่ได้สิทธิ์ `workloadIdentityUser`, asset ยังไม่ได้แชร์
