# cloud/ingest (issue #9, สเปค 20)

`POST /ingest` — body เป็น CSV ดิบ ≤ 300 แถว (~36 KB) ส่งทีละก้อน

Headers: `Authorization: Bearer <device token>`, `X-Device-Id`, `X-Chunk-Id: <session>-<first_record>-<last_record>`

หน้าที่: เช็ค token → เช็ค Device_ID ในแถวตรงกับ header → แยกแถวตามชั่วโมง (เวลาไทย) → เขียน
`raw/csv/device_id=<id>/date=YYYY-MM-DD/hour=HH/part-<chunk_id>.csv` → ตอบ 200

ส่งซ้ำ = ชื่อไฟล์เดิม = เขียนทับ (idempotent)

| ไฟล์ | |
|---|---|
| `handler.py` | Lambda (ใช้ `cloud/pipeline/common/schema.py` — Terraform zip ไปด้วย) |
| `make_token.py` | สร้าง token ต่อเครื่อง + hash สำหรับ `terraform.tfvars` |
| `tests/` | pytest + moto (mock S3) ครบทุก status |

| Status | เมื่อ |
|---|---|
| 200 | เขียนครบ `{"written": [...], "rows": n}` |
| 400 | X-Chunk-Id ผิดรูป, body ว่าง/มีแต่ header, header ไม่ตรง schema, Session/Record_ID นอกช่วง chunk_id |
| 401 | token ผิด/หาย |
| 403 | X-Device-Id หรือ Device_ID ในแถวไม่ตรงกับ token |
| 413 | body > 1 MB |
| 503 | เขียน S3 ไม่ได้ → เครื่อง backoff แล้วส่งซ้ำ |

## รันเทสต์

```bash
pip install -r cloud/ingest/requirements-dev.txt
python -m pytest cloud/ingest/tests -q
```

## Deploy (ต้องได้ OK ก่อน)

```bash
python cloud/ingest/make_token.py IOTW-A001     # ทำทีละเครื่อง
cd cloud/infra
cp terraform.tfvars.example terraform.tfvars    # ใส่ bucket_name + hash
terraform init && terraform plan && terraform apply
terraform output ingest_url                     # → .env INGEST_URL
```

ทดสอบด้วย curl:

```bash
curl -i -X POST "$INGEST_URL" \
  -H "Authorization: Bearer $DEVICE_TOKEN_WALK_01" \
  -H "X-Device-Id: IOTW-A001" -H "X-Chunk-Id: 12-1-4" \
  -H "Content-Type: text/csv" --data-binary @cloud/pipeline/tests/fixtures/good.csv
```
