# cloud/infra

IaC ของทุกอย่างใน `cloud/` ด้วย **Terraform/OpenTofu** (D10) + `docker-compose.yml` ของ EC2 — โครง module อยู่ในสเปค 00

ห้าม commit `*.pem`, `terraform.tfstate*` และ `*.tfvars` ที่มีค่าจริง

ข้อจำกัด Learner Lab: สร้าง IAM role เองไม่ได้ → อ้าง `LabRole` ที่มีอยู่, region us-east-1

## module ที่มีแล้ว

| module | สเปค | issue | มีอะไร |
|---|---|---|---|
| `storage` | 01 | #9, #17 | S3 bucket (block public, SSE-S3, BucketOwnerEnforced) + lifecycle (`athena-results/` 7 วัน) |
| `ingest` | 20 | #9 | Lambda `iot-dt-ingest` (Py3.12, LabRole) + HTTP API `POST /ingest` + log 14 วัน |
| `relay` | 10 | #7 | EC2 t3.micro + Elastic IP + SG port 8080: รับ HTTP จากบอร์ด ส่งต่อ HTTPS ไป ingest (`cloud/relay/`) ปิดได้ด้วย `relay_enabled = false` |
| `pipeline` | 30 | #11–#16 | Lambda 6 ตัว `iot-dt-iot-*` + Step Functions `iot-dt-iot-pipeline` + Scheduler ทุกชั่วโมงนาทีที่ 5 (เวลาไทย) + SNS `iot-dt-iot-pipeline-alerts` (`cloud/pipeline/`) |
| `catalog` | 40 | #17 | Glue database `iot_walking` + ตาราง `raw_csv`, `raw_csv_unknown`, `readings_1s`, `readings_1min`, `quality` (partition projection) + Athena workgroup `iot_walking` + saved queries + lifecycle `athena-results/` 7 วัน (ใน `storage`) |

```bash
aws s3 mb s3://iot-dt-<account-id> --region us-east-1   # สร้าง bucket เองก่อน (ดูด้านล่าง)
cp terraform.tfvars.example terraform.tfvars   # ใส่ค่าจริง
terraform init
terraform plan
terraform apply
```

Learner Lab: credentials เปลี่ยนทุก session ต้องอัปเดต `~/.aws/credentials` ก่อนรัน (ใน CloudShell ไม่ต้อง)

**bucket สร้างนอก Terraform:** resource `aws_s3_bucket` ของ provider จะอ่าน Object Lock ของ bucket ทุกครั้ง แต่ SCP ของ Learner Lab ห้าม `s3:GetBucketObjectLockConfiguration` (403) module `storage` จึงตั้งค่าเฉพาะ public access block / ownership / encryption บน bucket ที่มีอยู่แล้ว ชื่อ bucket ต้องตรงกับ `bucket_name` ใน `terraform.tfvars`

**pipeline:**
- ต้องใส่ `pandas_layer_arn` (layer AWS SDK for pandas ที่มี pyarrow) ใน `terraform.tfvars` ดูเลข version ล่าสุดของ `AWSSDKPandas-Python312` ใน us-east-1 ได้ที่ https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html
- ใส่ `alert_email` แล้วกดยืนยันในอีเมลจาก AWS หนึ่งครั้ง ไม่งั้นจะไม่ได้รับแจ้งเตือน
- ปิดรอบอัตโนมัติด้วย `pipeline_schedule_enabled = false` (รันมือได้ ดู `cloud/pipeline/README.md`)
- Scheduler และ Step Functions ใช้ `LabRole` ถ้า apply แล้วรันไม่ขึ้นเพราะสิทธิ์ ให้เช็ก trust policy ของ LabRole ว่ามี `scheduler.amazonaws.com` และ `states.amazonaws.com`

**catalog (Glue + Athena):**
- ใช้ partition projection ไม่ต้องรัน crawler / `MSCK REPAIR TABLE` ไฟล์ใหม่ query ได้ทันที
- `device_id` อ่านจาก `cloud/pipeline/common/devices.csv` → เพิ่มเครื่อง/station แล้วต้อง `terraform apply` ใหม่ (ดู `terraform output catalog_device_ids`)
- `date` = วันที่เวลาไทย `yyyy-MM-dd` ตั้งแต่ `catalog_date_start` ถึงพรุ่งนี้, `hour` (เฉพาะ raw) = `00`–`23` เป็น string
- แถวที่ไม่มีเวลา (`date=unknown/hour=unknown`) อยู่ในตาราง `raw_csv_unknown` (partition แค่ `device_id`) ใช้นับ/ดีบัก RTC; ตาราง processed มีเฉพาะแถวที่เวลาถูกต้อง
- คอลัมน์ `Device_ID` ใน CSV ชื่อ `csv_device_id` ในตาราง raw (ชื่อซ้ำกับ partition `device_id` ไม่ได้) และชื่อคอลัมน์ทุกตัวเป็นตัวเล็ก
- ใน Athena console เลือก workgroup `iot_walking` ก่อน (ผลไป `s3://<bucket>/athena-results/` ลบหลัง 7 วัน) ตัวอย่าง query อยู่ใน Saved queries และ `modules/catalog/queries/`
- ทุก query ควรมี `WHERE device_id = ... AND date = ...` ถ้าสแกนเกิน `athena_bytes_scanned_cutoff` (default 1 GiB) Athena จะยกเลิก

ทดสอบหลัง apply (CloudShell):

```bash
QID=$(aws athena start-query-execution --work-group iot_walking \
  --query-execution-context Database=iot_walking \
  --query-string "SELECT count(*), min(minute_local), max(minute_local) FROM readings_1min WHERE device_id='IOTW-A001' AND date='2026-11-16'" \
  --query QueryExecutionId --output text)
sleep 3
aws athena get-query-execution --query-execution-id $QID \
  --query 'QueryExecution.[Status.State,Statistics.TotalExecutionTimeInMillis,Statistics.DataScannedInBytes]'
aws athena get-query-results --query-execution-id $QID --output table
```
