# cloud/infra

IaC ของทุกอย่างใน `cloud/` ด้วย **Terraform/OpenTofu** (D10) + `docker-compose.yml` ของ EC2 — โครง module อยู่ในสเปค 00

ห้าม commit `*.pem`, `terraform.tfstate*` และ `*.tfvars` ที่มีค่าจริง

ข้อจำกัด Learner Lab: สร้าง IAM role เองไม่ได้ → อ้าง `LabRole` ที่มีอยู่, region us-east-1

## module ที่มีแล้ว

| module | สเปค | issue | มีอะไร |
|---|---|---|---|
| `storage` | 01 | #9 | S3 bucket (block public, SSE-S3, BucketOwnerEnforced) |
| `ingest` | 20 | #9 | Lambda `iot-dt-ingest` (Py3.12, LabRole) + HTTP API `POST /ingest` + log 14 วัน |

```bash
cp terraform.tfvars.example terraform.tfvars   # ใส่ค่าจริง
terraform init
terraform plan
terraform apply
```

Learner Lab: credentials เปลี่ยนทุก session ต้องอัปเดต `~/.aws/credentials` ก่อนรัน
