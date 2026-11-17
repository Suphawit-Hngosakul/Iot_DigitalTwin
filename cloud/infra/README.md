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

```bash
aws s3 mb s3://iot-dt-<account-id> --region us-east-1   # สร้าง bucket เองก่อน (ดูด้านล่าง)
cp terraform.tfvars.example terraform.tfvars   # ใส่ค่าจริง
terraform init
terraform plan
terraform apply
```

Learner Lab: credentials เปลี่ยนทุก session ต้องอัปเดต `~/.aws/credentials` ก่อนรัน (ใน CloudShell ไม่ต้อง)

**bucket สร้างนอก Terraform:** resource `aws_s3_bucket` ของ provider จะอ่าน Object Lock ของ bucket ทุกครั้ง แต่ SCP ของ Learner Lab ห้าม `s3:GetBucketObjectLockConfiguration` (403) module `storage` จึงตั้งค่าเฉพาะ public access block / ownership / encryption บน bucket ที่มีอยู่แล้ว ชื่อ bucket ต้องตรงกับ `bucket_name` ใน `terraform.tfvars`
