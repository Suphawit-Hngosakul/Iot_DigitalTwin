# cloud/infra

IaC ของทุกอย่างใน `cloud/` ด้วย **Terraform/OpenTofu** (D10) + `docker-compose.yml` ของ EC2 — โครง module อยู่ในสเปค 00

ห้าม commit `*.pem`, `terraform.tfstate*` และ `*.tfvars` ที่มีค่าจริง

AWS account ของตัวเอง (D27): region `ap-southeast-2` เท่านั้น (SCP ของบัญชี Projects เปิดแค่ region ที่เลือกตอนสมัคร) ทุกบริการมี IAM role ของตัวเอง (module `iam`) deploy ทั้งชุดด้วย `python cloud/infra/deploy.py` แล้วเติมข้อมูลด้วย `python cloud/infra/post_apply.py --all`

## module ที่มีแล้ว

| module | สเปค | issue | มีอะไร |
|---|---|---|---|
| `storage` | 01 | #9, #17 | S3 bucket (block public, SSE-S3, BucketOwnerEnforced) + lifecycle (`athena-results/` 7 วัน) |
| `iam` | 00 | R1 | role ต่อบริการ `iot-dt-<service>-lambda` (สิทธิ์ S3/Athena/Glue เท่าที่ใช้) + `iot-dt-sfn` + `iot-dt-trigger` (EventBridge/Scheduler → Step Functions) |
| `ingest` | 20 | #9 | Lambda `iot-dt-ingest` (Py3.12, role `iot-dt-ingest-lambda`) + HTTP API `POST /ingest` + log 14 วัน |
| `relay` | 10, 22 | #7, #10 | EC2 t3.micro + Elastic IP + SG port 8080/8081 (ไม่เปิด 22 ดู log ผ่าน SSM, `relay_instance_id`): relay รับ HTTP จากบอร์ดส่งต่อ HTTPS ไป ingest (`cloud/relay/`) + realtime hub `ws://` (`cloud/realtime-hub/`, D22; ปิดเฉพาะ hub ด้วย `hub_enabled = false`, URL ดู `terraform output hub_device_url` / `hub_live_url`) ปิดทั้งเครื่องด้วย `relay_enabled = false` |
| `pipeline` | 30 | #11–#16 | Lambda 6 ตัว `iot-dt-iot-*` + Step Functions `iot-dt-iot-pipeline` + Scheduler ทุกชั่วโมงนาทีที่ 5 (เวลาไทย) + SNS `iot-dt-iot-pipeline-alerts` (`cloud/pipeline/`) |
| `catalog` | 40 | #17 | Glue database `iot_walking` + ตาราง `raw_csv`, `raw_csv_unknown`, `readings_1s`, `readings_1min`, `quality`, `satellite_grid` (partition projection) + Athena workgroup `iot_walking` + saved queries + lifecycle `athena-results/` 7 วัน (ใน `storage`) |
| `api` | 40 | #18 | Lambda `iot-dt-api` (Py3.12, role `iot-dt-api-lambda`) + HTTP API แยก `iot-dt-api`: `GET /devices /latest /readings /quality` + CORS (`api_allowed_origins`) + log 14 วัน (`cloud/api/`) |
| `satellite` | 50 | #25, #27 | layer `iot-dt-gee` (earthengine-api) + Lambda `iot-dt-satellite-test` และ `iot-dt-satellite-ingest` (Py3.12, role `iot-dt-satellite-lambda`, timeout 900 s) + EventBridge รายวัน `cron(0 2 * * ? *)` (ตั้งด้วย `satellite_ingest_schedule`, `""` = รันมือเท่านั้น) ยืนยันตัวกับ GEE ด้วย WIF ไม่มี key (`cloud/satellite/`) ปิดไว้เป็นค่าเริ่มต้น เปิดด้วย `satellite_enabled = true` แล้ว apply จะรัน `cloud/satellite/layer/build_layer.py` สร้าง zip ให้เอง (ต้องมี Python + pip) |
| `satellite_pipeline` | 32, 51 | #30, #31 | Lambda 6 ตัว `iot-dt-sat-*` + Step Functions `iot-dt-sat-pipeline` + EventBridge rule `iot-dt-sat-complete` (S3 Object Created `raw/satellite/*/_COMPLETE`, ปิดด้วย `satellite_pipeline_trigger_enabled = false`) + data contracts (`cloud/pipeline/contracts/*.yaml` แปลงเป็น JSON ตอน zip) แจ้งเตือนผ่าน SNS ของ `pipeline` ปิดไว้เป็นค่าเริ่มต้น เปิดด้วย `satellite_pipeline_enabled = true` (เปิด S3 → EventBridge ของ bucket ด้วย) |
| `curated_merge` | 60 §2.1 | #32 | Lambda 4 ตัว `iot-dt-curated-*` + Step Functions `iot-dt-curated-merge` (PlanMerge → CheckSources → MergeCurated → VerifyCurated, `WaitingForOtherSources` เมื่อแหล่งไม่ครบ) + EventBridge rule `iot-dt-curated-published` (S3 `curated/_success/satellite|buildings/*.json`) + Scheduler รายวัน 10:00 น. เวลาไทย (`curated_merge_daily_schedule`, `""` = ไม่รัน) สร้างตาราง Iceberg `iot_walking.cell_period` ที่ `curated/cell_period/` (`cloud/analytics/curated_merge/`) ปิดไว้เป็นค่าเริ่มต้น เปิดด้วย `curated_merge_enabled = true` (ต้องเปิด `satellite_pipeline_enabled` ด้วย) |

## Deploy ตั้งแต่เริ่มต้น (บัญชี AWS ใหม่ที่ไม่มีอะไรเลย)

ลำดับนี้ใช้ได้ทั้งบัญชีใหม่และ deploy ซ้ำ ทำทีละขั้นตามลำดับ **ขั้น 1-5 คือ IoT** (ใช้งานได้เลย) **ขั้น 6-8 คือดาวเทียม** (เปิดทีละชั้นเมื่อพร้อม)

> หมายเหตุ: ขั้นที่ไม่ต้องรอบริการภายนอก (1-5) ผ่าน `terraform fmt` แล้ว แต่ลำดับทั้งชุดยังไม่เคยรันครบในบัญชีใหม่ ถ้าติดตรงไหนดู "แก้ปัญหาที่เจอบ่อย" ท้ายหัวข้อนี้

### ขั้น 0: เตรียมเครื่อง

| ต้องมี | ใช้ทำอะไร | เช็ก |
|---|---|---|
| Terraform >= 1.6 (หรือ OpenTofu) | สร้างทุก resource | `terraform version` |
| AWS CLI v2 | Terraform เรียกสร้าง bucket + ใช้ตรวจผล | `aws --version` |
| bash | `local-exec` ของ module `storage` (Windows ใช้ Git Bash) | `bash --version` |
| Python 3.12 + pip | สร้างโทเค็นอุปกรณ์ + build GEE layer (เฉพาะถ้าเปิดดาวเทียม) | `python --version` |
| git | clone repo | `git --version` |

เครื่องที่ไม่มีของข้างบน ใช้ **AWS CloudShell** แทนได้ (มี aws, bash, python, git อยู่แล้ว ต้องติดตั้ง Terraform เอง และไม่ต้องตั้ง credentials)

**CloudShell: เตรียมเครื่องด้วยสคริปต์** (รันซ้ำได้ทุกครั้งที่ได้ CloudShell ใหม่ ไม่แตะ state หรือ resource)
```bash
cd ~                                               # clone ใน home เท่านั้น: /tmp ถูกล้างทุกครั้งที่ session เริ่มใหม่
git clone https://github.com/suphawit-hngosakul/iot_digitaltwin.git
cd iot_digitaltwin && git checkout develop
bash cloud/infra/setup-cloudshell.sh
source ~/.bashrc
```
`setup-cloudshell.sh` ติดตั้ง Terraform 1.9.8 ลง `~/bin` (ข้ามถ้ามีแล้ว, เปลี่ยนเวอร์ชันด้วย `TF_VERSION=...`), เพิ่ม `PATH` กับ `TF_DATA_DIR=/tmp/tf-data` ลง `~/.bashrc`, เช็ก aws / python3 / pip / git, credentials, ว่า repo อยู่ใน home ไม่ใช่ `/tmp`, มี `terraform.tfvars` หรือยัง และพื้นที่ home เหลือพอ

ทำไมต้อง `TF_DATA_DIR`: home ของ CloudShell มีแค่ 1 GB provider AWS ใน `.terraform/` ใช้หลายร้อย MB รวมกับ GEE layer แล้วเต็ม ทำให้ apply สร้าง resource ได้แต่ **บันทึก state ไม่ได้** จึงเก็บ provider ไว้ใน `/tmp` แทน (หายเมื่อ session เริ่มใหม่ ให้ `terraform init` ใหม่ ส่วน `terraform.tfstate` อยู่ใน home ไม่หาย)

Windows (PowerShell): `winget install --id Hashicorp.Terraform -e` แล้วเปิด terminal ใหม่ หรือดาวน์โหลด zip จาก https://developer.hashicorp.com/terraform/install แล้ววาง `terraform.exe` ในโฟลเดอร์ที่อยู่ใน `PATH`

### ขั้น 1: ตั้ง credentials ของ AWS

1. ใน AWS console: IAM → Users → สร้าง user (เช่น `view-admin`) แนบ `AdministratorAccess` แล้วสร้าง **Access key** แบบ CLI (เก็บ secret ไว้เอง ห้าม commit/ห้ามส่งในแชต)
2. `aws configure` ใส่ access key, secret, region `ap-southeast-2`, output `json`
3. เช็ก: `aws sts get-caller-identity` ต้องเห็น account id ของบัญชีนี้

บัญชี AWS "Projects" (Free plan) มี SCP ที่ AWS ตั้งไว้: ใช้ได้แค่ region ที่เลือกตอนสมัคร region อื่นจะได้ `explicit deny in a service control policy` ทุกบริการ (แก้ไม่ได้ด้วย IAM) อย่ากด "Activate advanced features" (ย้อนไม่ได้ และลบ spend limit ของบัญชี)

### ขั้น 2: เอาโค้ดมา

clone ไว้ใน **home** (`cd ~` ก่อน) ห้ามไว้ใน `/tmp` เพราะ CloudShell ล้าง `/tmp` ทุกครั้งที่ session เริ่มใหม่

บน CloudShell ถ้าทำตามขั้น 0 แล้ว ข้ามขั้นนี้ได้ (clone ไปแล้ว)

```bash
cd ~
git clone https://github.com/suphawit-hngosakul/iot_digitaltwin.git
cd iot_digitaltwin
git checkout develop            # branch หลัก
cd cloud/infra
```

### ขั้น 3: สร้างโทเค็นอุปกรณ์ + ค่าที่ต้องใส่

1. สร้างโทเค็นต่ออุปกรณ์ 1 ครั้ง (รันที่ root ของ repo):
   ```bash
   python cloud/ingest/make_token.py IOTW-A001
   python cloud/ingest/make_token.py IOTW-A002
   ```
   ได้ 3 บรรทัดต่อเครื่อง: `token` เก็บไว้ใน `secrets.h` ของบอร์ด (ห้าม commit, ห้ามใส่ใน tfvars) ส่วนบรรทัด `tfvars` คือ hash ที่จะคัดลอกไปใส่ในขั้นถัดไป
2. คัดลอกไฟล์ตัวอย่างแล้วแก้ค่าในส่วน "ต้องแก้":
   ```bash
   cp terraform.tfvars.example terraform.tfvars    # ห้าม commit ไฟล์นี้ (gitignore ไว้แล้ว)
   ```
   | ตัวแปร | ใส่อะไร |
   |---|---|
   | `bucket_name` | `digitaltwin-lake-<account-id>` (ไม่ซ้ำทั่วโลก; `aws sts get-caller-identity` ให้ account id) |
   | `device_token_hashes` | บรรทัด `tfvars` จากขั้น 3.1 ของทุกอุปกรณ์ |
   | `alert_email` | อีเมลที่รับแจ้งเตือน pipeline ล้มเหลว + budget alert (R7) |
   | `gee_wif.provider_id` | provider ของ AWS account นี้ใน pool ของ GEE (ขั้น 6) |

   pandas layer ไม่ต้องใส่: ใช้ layer สาธารณะ `AWSSDKPandas-Python312` ของ region ตาม `pandas_layer_version` (default 31 ตรวจแล้วว่ามีใน ap-southeast-2) override ทั้ง ARN ได้ด้วย `pandas_layer_arn`

   ส่วนอื่นของ tfvars ปล่อยไว้ตามตัวอย่างได้ (IoT เปิดอยู่ ดาวเทียมปิดอยู่)

### ขั้น 4: Deploy

```bash
python cloud/infra/deploy.py --plan   # build เว็บ + terraform plan อ่านรายการ resource ก่อน
python cloud/infra/deploy.py          # build เว็บ (web/dist) → terraform init (ครั้งแรก) → apply → พิมพ์ URL
```
ต้อง build เว็บก่อน apply ทุกครั้งที่เว็บเปลี่ยน เพราะ Terraform อ่านรายชื่อไฟล์ใน `web/dist` ตอน plan (`deploy.py` ทำให้เอง) วาง `buildings.geojson` ไว้ที่ `web/public/data/` ก่อน build ถ้าจะแสดงอาคารจริง (D26)

สิ่งที่เกิดขึ้นตอน apply ครั้งแรก:
- state เก็บใน `terraform.tfstate` ข้าง `main.tf` (ใน home ของ CloudShell ไม่หายเมื่อปิด session) ถ้าไฟล์นี้หาย Terraform จะจำ resource ที่สร้างไปไม่ได้ ควรสำรองไว้หลัง apply เช่น `aws s3 cp terraform.tfstate s3://<bucket>/backup/terraform.tfstate`
- module `storage` สร้าง S3 bucket ด้วย AWS CLI (`terraform_data` + `local-exec`) ถ้ามี bucket ชื่อนี้อยู่แล้วจะข้ามไป
- ได้ Lambda ingest + HTTP API, Step Functions pipeline + Scheduler ทุกชั่วโมงนาทีที่ 5 (เวลาไทย), Glue database `iot_walking` + Athena workgroup, API ให้เว็บ, และ EC2 relay + Elastic IP + realtime hub
- EC2 ติดตั้ง relay/hub เองผ่าน user-data ตอนบูตครั้งแรก รอประมาณ 2-3 นาทีหลัง apply จบ service ถึงจะขึ้น (ตั้ง `systemd enable` ไว้ ถ้าเครื่องถูกหยุดแล้วเปิดใหม่ service จะขึ้นเอง และ Elastic IP คงเดิม)

จด URL ที่ต้องใช้ต่อ:

```bash
terraform output ingest_url        # Lambda ingest (HTTPS)
terraform output relay_url         # http://<Elastic IP>:8080/ingest  -> ใส่ในบอร์ด
terraform output hub_device_url    # ws://<Elastic IP>:8081/device
terraform output hub_live_url      # ws://<Elastic IP>:8081/live
terraform output api_url           # API ของเว็บ
```

### ขั้น 5: ยืนยันอีเมล + ตั้งค่าบอร์ด + ตรวจผล

ตรวจทั้งระบบในคำสั่งเดียว (รันใน `cloud/infra` ได้ทุกเมื่อ ไม่แก้อะไรในระบบ):
```bash
bash healthcheck.sh            # เพิ่ม --no-gee ถ้าไม่อยากเรียก GEE
```
แสดง PASS / WARN / FAIL ทีละส่วน: credentials, bucket, ingest, relay, hub, API, Lambda, Step Functions (รันล่าสุด), schedule, อีเมล SNS, Glue/Athena, GEE และจำนวนไฟล์ในแต่ละ zone ของ bucket WARN คือยังไม่พร้อมแต่ไม่ใช่ความผิดพลาด (เช่น ยังไม่มีข้อมูล ยังไม่กดยืนยันอีเมล) ถ้ามี FAIL จะจบด้วย exit 1

1. **ยืนยันอีเมล SNS:** เปิดอีเมลจาก AWS Notifications แล้วกด *Confirm subscription* ไม่งั้นจะไม่ได้รับแจ้งเตือน
2. **ตั้งบอร์ด** (บัญชีใหม่ = Elastic IP ใหม่ ต้องแก้และ flash ใหม่): ใส่ `relay_url` กับ `hub_device_url` และ token ของอุปกรณ์นั้นใน `secrets.h` ดูวิธีใน `iot/firmware/README.md`
3. **ตรวจว่าข้อมูลเข้า:** หลังบอร์ดอัปโหลดไฟล์แล้ว ดูว่ามีไฟล์ใน `s3://<bucket>/raw/csv/` (`aws s3 ls s3://<bucket>/raw/csv/ --recursive | head`) pipeline รันเองทุกชั่วโมง นาทีที่ 5 หรือรันมือตาม `cloud/pipeline/README.md`
4. **ตรวจ catalog:** ใช้ query ใน "ทดสอบ catalog หลัง apply" ด้านล่าง
5. **ตรวจ API:** `curl "$(terraform output -raw api_url)/devices"` ต้องได้รายชื่ออุปกรณ์กลับมา

บัญชีใหม่เริ่มจากข้อมูลว่าง IoT จะมีข้อมูลหลังบอร์ดอัปโหลดรอบแรก ถ้าบอร์ดเก็บข้อมูลค้างใน SD จะ backfill ให้เองตอนส่ง

### ขั้น 6: ดาวเทียม A (ดึง Landsat จาก GEE)

**ทำก่อน (นอก AWS):** ฝั่ง Google ต้องให้ AWS account ใหม่เข้ามาได้ เพราะ Lambda ยืนยันตัวกับ GEE ด้วย WIF ของ project ที่เป็นของ Get ต้องขอให้เจ้าของ project:
- เพิ่ม AWS account id ใหม่ให้ AWS provider ของ identity pool (`gee_wif.pool_id` / `provider_id`)
- ให้ attribute condition ของ provider อนุญาต `arn:aws:sts::<account>:assumed-role/iot-dt-satellite-lambda` และให้สิทธิ์ `roles/iam.workloadIdentityUser` กับ principalSet `.../attribute.aws_role/<ค่า terraform output satellite_wif_role>` บน service account `gee_wif.service_account` (role ยังไม่ต้องมีจริงก็ตั้งล่วงหน้าได้ ชื่อคงที่)

แต่ละ AWS account ใช้ **provider คนละตัว** ใน pool เดียวกัน (provider ผูกกับ account id) ค่า default ของ `gee_wif.provider_id` ใน `variables.tf` เป็นของอีก account ถ้า account นี้มี provider แล้ว ให้ override ใน `terraform.tfvars` เช่น `gee_wif = { project_number = "959910184696", pool_id = "6609650251", provider_id = "aws-view", service_account = "service-gee-iot@turnkey-tribute-511903-t6.iam.gserviceaccount.com" }` อาการถ้าใช้ provider ผิดตัว: `invalid_grant: Specified AWS account ID ... not found in the configured identity provider`

ถ้ายังไม่ทำ Lambda จะได้ error ตอนแลก token ไม่ใช่ปัญหาของ Terraform รายละเอียดดู `cloud/satellite/README.md`

**แล้ว deploy:** แก้ `terraform.tfvars` เป็น `satellite_enabled = true` (ถ้าเครื่องมีแค่ `python3` เพิ่ม `python_cmd = "python3"` ด้วย) แล้ว:

```bash
terraform apply
```

Terraform รัน `cloud/satellite/layer/build_layer.py` สร้าง `gee-layer.zip` ให้เองครั้งแรก (ต้องมี Python + pip + เน็ต) จากนั้นทดสอบ:

```bash
aws lambda invoke --function-name iot-dt-satellite-test --payload '{}' out.json && cat out.json
# ได้ผลจาก GEE = WIF ใช้ได้; ได้ error สิทธิ์ = ยังตั้งฝั่ง Google ไม่ครบ
```

`satellite_ingest` ตั้งเวลารันเองทุกวัน 02:00 UTC (เปลี่ยนด้วย `satellite_ingest_schedule`) บัญชีใหม่ไม่มีข้อมูลย้อนหลัง ถ้าต้องการต้องรัน backfill เอง ดูวิธีใน `cloud/satellite/README.md`

### ขั้น 7: ดาวเทียม B (ตรวจ batch → trusted + layer)

แก้ `satellite_pipeline_enabled = true` แล้ว `terraform apply` ข้อควรระวัง: ขั้นนี้เปิด S3 → EventBridge ของ bucket ด้วย `aws_s3_bucket_notification` ซึ่งจะ **ทับ notification อื่นที่ตั้งไว้เองบน bucket นี้** (บัญชีใหม่ไม่มี จึงไม่เป็นปัญหา) pipeline เริ่มเองเมื่อมีไฟล์ `_COMPLETE` ใน `raw/satellite/<batch>/` หรือรันมือด้วย `satellite_pipeline_trigger_enabled = false`

### ขั้น 8: ดาวเทียม C (รวมเป็น `cell_period`)

เปิดได้เมื่อขั้น 7 ทำงานแล้ว (ต้องมี `trusted/satellite_grid/` และตาราง `buildings_grid` จาก catalog):

```bash
# terraform.tfvars: curated_merge_enabled = true
terraform apply -target=module.catalog -target=module.curated_merge
```

หลัง apply รัน `iot-dt-curated-merge` ด้วย `{"mode": "force"}` เพื่อสร้างตาราง Iceberg `iot_walking.cell_period` ครั้งแรก (ดูหัวข้อ "curated_merge" ท้ายไฟล์)

### ถ้าเปิดดาวเทียมแล้วมีปัญหา

เปิด flag ทีละอันตามลำดับ **A → B → C** อย่าเปิดพร้อมกัน เพราะ C ต้องการผลของ B และ B ต้องการข้อมูลของ A ถ้าเปิดแล้วมีปัญหา ตั้งกลับเป็น `false` แล้ว `terraform apply` จะลบ resource ของชั้นนั้น (ข้อมูลใน bucket ไม่ถูกลบ)

### แก้ปัญหาที่เจอบ่อย

| อาการ | สาเหตุ / วิธีแก้ |
|---|---|
| `no space left on device` ตอน apply (CloudShell) | home เต็ม: **อย่าปิดหน้าต่างและอย่า apply ซ้ำ** ลบ `~/.cache/pip`, `cloud/satellite/layer/build/`, `.terraform/` (รัน `bash setup-cloudshell.sh` ดูพื้นที่ที่เหลือได้) แล้วคัดลอก JSON ของ state ที่ Terraform พิมพ์ไว้ในหน้าจอ (ตั้งแต่ `{` แรกถึง `}` สุดท้าย) ลง `errored.tfstate` แล้ว `terraform state push errored.tfstate` จากนั้นตั้ง `TF_DATA_DIR` ตามขั้น 0 แล้ว init + apply ใหม่ ถ้ากู้ state ไม่ได้ ต้อง import resource เข้า state ใหม่ หรือลบ resource ใน console แล้ว apply ใหม่ |
| `ExpiredToken` / `InvalidClientTokenId` | access key ผิดหรือถูกลบ ทำขั้น 1 ใหม่ |
| `explicit deny in a service control policy` | ใช้ region ผิด บัญชีนี้ใช้ได้แค่ `ap-southeast-2` เช็ก `aws configure get region` และตัวแปร `region` |
| apply ล้มตอน `terraform_data.bucket` (`aws: command not found` / `bash`) | เครื่องไม่มี aws หรือ bash ใช้ CloudShell หรือสร้าง bucket เอง `aws s3api create-bucket --bucket <ชื่อ> --region ap-southeast-2 --create-bucket-configuration LocationConstraint=ap-southeast-2` แล้ว apply ซ้ำ (จะข้ามการสร้าง) |
| `BucketAlreadyExists` | ชื่อ bucket ซ้ำทั่วโลก เปลี่ยน `bucket_name` |
| `AccessDenied` ที่ `s3:GetBucketObjectLockConfiguration` | เกิดถ้าใช้ resource `aws_s3_bucket` ตรงๆ module `storage` เลี่ยงไว้แล้ว อย่าเปลี่ยนไปใช้ resource นั้น |
| build layer ล้ม (`python: command not found`, pip ล้มเหลว) | ตั้ง `python_cmd = "python3"` และเช็กว่าออกเน็ตได้ หรือ build เองแล้วตั้ง `gee_layer_zip` |
| Step Functions / Scheduler รันไม่ขึ้นเพราะสิทธิ์ | ดู role `iot-dt-sfn` / `iot-dt-trigger` ใน module `iam` (trust `states`, `events`, `scheduler`) |
| budget สร้างไม่ได้ (`AccessDenied` ที่ budgets) | ตั้ง `budget_limit_usd = 0` แล้ว apply ใหม่ ยังมี spend limit ของบัญชีกันอยู่ |
| `satellite_test` error ตอนแลก token | ฝั่ง Google ยังไม่เพิ่ม AWS account / role ใหม่ หรือ `gee_wif.provider_id` ไม่ตรง (ขั้น 6) |
| `AlreadyExists` ของตาราง Glue | ตารางเดิมที่สร้างนอก Terraform ลบรายการนั้นใน Glue แล้ว apply ใหม่ |
| ไม่ได้อีเมลแจ้งเตือน | ยังไม่กดยืนยันอีเมล SNS (ขั้น 5.1) |

### ล้างทั้งหมด (ถ้าต้องการ)

`terraform destroy` ลบทุก resource ยกเว้น bucket กับข้อมูลข้างใน (ตั้งใจให้ปลอดภัย) ถ้าต้องการลบ bucket จริงให้ทำเองหลังตรวจแล้ว: `aws s3 rb s3://<bucket> --force` ไฟล์ `terraform.tfstate` อยู่ในเครื่องที่รัน เก็บสำรองไว้ถ้าจะ apply จากเครื่องอื่น หายแล้ว Terraform จะไม่รู้จัก resource ที่สร้างไป

---

## อ้างอิง

**bucket:** module `storage` สร้าง bucket ด้วย AWS CLI ผ่าน `terraform_data` + `local-exec` (ต้องมี `aws` และ `bash` ในเครื่องที่รัน Windows ใช้ Git Bash) สืบมาจากสมัย Learner Lab ที่ SCP ห้าม `s3:GetBucketObjectLockConfiguration` ซึ่ง resource `aws_s3_bucket` อ่านทุกครั้ง ใช้กับบัญชีใดก็ได้ ถ้ามี bucket อยู่แล้วจะข้ามไป Terraform ตั้งค่าเฉพาะ public access block / ownership / encryption / lifecycle / notification และ `terraform destroy` **ไม่ลบ** bucket กับข้อมูลข้างใน

**ย้ายไป bucket ใหม่ (เช่นจาก `iot-dt-<account-id>` เป็น `digitaltwin-lake-<account-id>`):** S3 เปลี่ยนชื่อ bucket ไม่ได้ ต้องสร้างใหม่แล้ว copy ข้อมูลไป ส่วนโค้ดอ้างชื่อผ่าน `bucket_name` ตัวเดียว (Lambda env `DATA_BUCKET`, Glue location, EventBridge rule) ยกเว้นตาราง Iceberg `cell_period` ที่เก็บ path เต็มไว้ใน metadata จึงต้องสร้างใหม่ ทำในรอบเดียวกับ D23 ขั้น 4 ได้ เพราะ IoT ต้องรัน force อยู่แล้ว
1. `aws s3 mb s3://<ใหม่> --region ap-southeast-2` แล้วปิด scheduler ชั่วคราว (`pipeline_schedule_enabled = false`, `satellite_ingest_schedule = ""`, `curated_merge_daily_schedule = ""`) + `terraform apply`
2. `aws s3 sync s3://<เก่า> s3://<ใหม่> --exclude "athena-results/*" --exclude "processed/*" --exclude "curated/cell_period/*"` (`processed/` สร้างใหม่จาก raw ด้วย force, `cell_period` สร้างใหม่ด้วย curated-merge)
3. `aws glue delete-table --database-name iot_walking --name cell_period` (ลบแค่รายการใน Glue ไม่ลบไฟล์; อย่าใช้ `DROP TABLE` ใน Athena เพราะ Iceberg จะลบไฟล์ด้วย)
4. แก้ `bucket_name` ใน `terraform.tfvars` เปิด scheduler คืน แล้ว `terraform apply`
5. รัน `iot-dt-iot-pipeline` ด้วย `{"batch_ids": [...ทุก batch ใน raw/csv/], "mode": "force"}` แล้วรัน `iot-dt-curated-merge` ด้วย `{"mode": "force"}` (สร้าง `cell_period` ที่ bucket ใหม่)
6. เช็ก API (`/devices /latest /layers /grid`) และจำนวนแถวใน Athena แล้วค่อยลบ bucket เก่า
- ข้อมูลดาวเทียม (raw/trusted/layers/markers) ไม่ต้องรันใหม่ เพราะ marker และ layer เก็บแค่ key ไม่เก็บชื่อ bucket
- บอร์ดและ relay ไม่ต้องแก้ เพราะส่งผ่าน `/ingest` ไม่รู้จักชื่อ bucket

**pipeline:**
- layer AWS SDK for pandas (pyarrow) ใช้ของสาธารณะใน region อัตโนมัติ (`pandas_layer_version`) ดู version ที่มีได้ที่ https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html
- ใส่ `alert_email` แล้วกดยืนยันในอีเมลจาก AWS หนึ่งครั้ง ไม่งั้นจะไม่ได้รับแจ้งเตือน
- ปิดรอบอัตโนมัติด้วย `pipeline_schedule_enabled = false` (รันมือได้ ดู `cloud/pipeline/README.md`)
- Step Functions ใช้ role `iot-dt-sfn` และ Scheduler/EventBridge ใช้ `iot-dt-trigger` (module `iam`)
- IoT ที่มาช้ากี่วันก็ถูกทำ: ingest เขียน `raw/_pending/iot/<batch_id>` แล้ว `plan_batches` หยิบทุก batch ที่ค้าง (lookback 6 ชม. เป็นตาข่าย)

**catalog (Glue + Athena):**
- ใช้ partition projection ไม่ต้องรัน crawler / `MSCK REPAIR TABLE` ไฟล์ใหม่ query ได้ทันที
- `device_id` อ่านจาก `cloud/pipeline/common/devices.csv` → เพิ่มเครื่อง/station แล้วต้อง `terraform apply` ใหม่ (ดู `terraform output catalog_device_ids`)
- `date` = วันที่เวลาไทย `yyyy-MM-dd` ตั้งแต่ `catalog_date_start` ถึงพรุ่งนี้, `hour` (เฉพาะ raw) = `00`–`23` เป็น string
- แถวที่ไม่มีเวลา (`date=unknown/hour=unknown`) อยู่ในตาราง `raw_csv_unknown` (partition แค่ `device_id`) ใช้นับ/ดีบัก RTC; ตาราง IoT (trusted/curated) มีเฉพาะแถวที่เวลาถูกต้อง
- คอลัมน์ `Device_ID` ใน CSV ชื่อ `csv_device_id` ในตาราง raw (ชื่อซ้ำกับ partition `device_id` ไม่ได้) และชื่อคอลัมน์ทุกตัวเป็นตัวเล็ก
- ใน Athena console เลือก workgroup `iot_walking` ก่อน (ผลไป `s3://<bucket>/athena-results/` ลบหลัง 7 วัน) ตัวอย่าง query อยู่ใน Saved queries และ `modules/catalog/queries/`
- ทุก query ควรมี `WHERE device_id = ... AND date = ...` ถ้าสแกนเกิน `athena_bytes_scanned_cutoff` (default 1 GiB) Athena จะยกเลิก
- query ที่ไม่มีเงื่อนไข `date` ทำให้ Athena list S3 ทุก partition ที่ projection สร้าง (เครื่อง × วัน × 24 ชม. สำหรับ `raw_csv`) จะช้ามากแม้ข้อมูลน้อย จึงตั้ง `catalog_date_start` ใกล้วันที่มีข้อมูลจริง (default 2026-03-01 = วันแรกของ log รุ่นเก่าของ IOTW-A001 ที่ import เข้ามา) และควรใส่ `date` ทุกครั้ง
- `satellite_grid` (#31) อ่าน `trusted/satellite_grid/period_type=<scene|season>/year=<YYYY>/` ที่ WriteTrusted เขียน (1 ไฟล์ต่อ batch) partition `period_type` (string) + `year` (int, ช่วง `satellite_year_range` ใน module default `2013,2035`) ควรใส่ `WHERE period_type = ... AND year = ...` ทุกครั้ง ตัวอย่างคือ saved query `satellite-grid-per-batch`

**api:**
- URL ดูจาก `terraform output api_url` ส่วนวิธีเรียก/ทดสอบดู `cloud/api/README.md`
- CORS default `["*"]` เมื่อเว็บ (#35) มีโดเมนแล้วให้ใส่ `api_allowed_origins` ใน `terraform.tfvars`
- Lambda เรียก Athena / Glue / S3 ด้วย role `iot-dt-api-lambda` ถ้าได้ 502 ให้ดู log `/aws/lambda/iot-dt-api`

ทดสอบ catalog หลัง apply (CloudShell):

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

**curated_merge (#32):**
- ตาราง `cell_period` สร้างโดย Lambda ด้วย `CREATE TABLE IF NOT EXISTS` (Iceberg ต้องสร้างผ่าน Athena DDL ไม่ใช่ Terraform) จึงไม่อยู่ใน `terraform state`; ลบตารางต้อง `DROP TABLE` ใน Athena แล้วลบ `curated/cell_period/` กับ `curated/_success/merge/` เอง
- apply แบบ `-target`: `terraform apply -target=module.catalog -target=module.curated_merge`
- รันมือ: `{}` = merge ทุก period ที่ stale, `{"periods": ["scene_2024-03-17"], "mode": "force"}` = บังคับ period นั้น
- ดู `cloud/analytics/curated_merge/README.md`
