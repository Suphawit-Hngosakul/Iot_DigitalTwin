# cloud/infra

IaC ของทุกอย่างใน `cloud/` ด้วย **Terraform/OpenTofu** (D10) + `docker-compose.yml` ของ EC2 — โครง module อยู่ในสเปค 00

ห้าม commit `*.pem`, `terraform.tfstate*` และ `*.tfvars` ที่มีค่าจริง

ข้อจำกัด Learner Lab: สร้าง IAM role เองไม่ได้ → อ้าง `LabRole` ที่มีอยู่, region us-east-1

## module ที่มีแล้ว

| module | สเปค | issue | มีอะไร |
|---|---|---|---|
| `storage` | 01 | #9 | S3 bucket (block public, SSE-S3, BucketOwnerEnforced) |
| `ingest` | 20 | #9 | Lambda `iot-dt-ingest` (Py3.12, LabRole) + HTTP API `POST /ingest` + log 14 วัน |
| `relay` | 10 | #7 | EC2 t3.micro + Elastic IP + SG port 8080: รับ HTTP จากบอร์ด ส่งต่อ HTTPS ไป ingest (`cloud/relay/`) ปิดได้ด้วย `relay_enabled = false` |
| `pipeline` | 30 | #11–#16 | Lambda 6 ตัว `iot-dt-iot-*` + Step Functions `iot-dt-iot-pipeline` + Scheduler ทุกชั่วโมงนาทีที่ 5 (เวลาไทย) + SNS `iot-dt-iot-pipeline-alerts` (`cloud/pipeline/`) |

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
