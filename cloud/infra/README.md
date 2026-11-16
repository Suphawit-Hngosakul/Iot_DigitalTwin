# cloud/infra

IaC ของทุกอย่างใน `cloud/` ด้วย **Terraform/OpenTofu** (D10) + `docker-compose.yml` ของ EC2 — โครง module อยู่ในสเปค 00

ห้าม commit `*.pem`, `terraform.tfstate*` และ `*.tfvars` ที่มีค่าจริง

ข้อจำกัด Learner Lab: สร้าง IAM role เองไม่ได้ → อ้าง `LabRole` ที่มีอยู่, region us-east-1

