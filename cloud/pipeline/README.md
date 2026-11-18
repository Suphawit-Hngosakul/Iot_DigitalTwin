# cloud/pipeline (issue #11–#16)

Step Functions รายชั่วโมง (EventBridge Scheduler)

```
cloud/pipeline/
├── statemachine/pipeline.asl.json
├── functions/
│   ├── plan_batches/      # สร้างรายการ batch ให้ Map (D21)
│   ├── check_batch/
│   ├── validate_batch/
│   ├── transform_batch/   # รวม raw ทุก part ของชั่วโมงเป็น Parquet 1 ไฟล์ต่อตาราง
│   ├── verify_batch/      # นับแถว raw = processed + dropped แล้วเขียน manifest
│   └── quarantine_batch/
├── common/        # schema.py, batch_manifest.py, batch_eval.py, devices.csv, grid.py
└── tests/         # pytest + CSV ตัวอย่าง (ดี/เสีย/ซ้ำ/ขาด)
```

## รันเทสต์

```bash
pip install -r cloud/pipeline/requirements-dev.txt
python -m pytest cloud/pipeline/tests -q
```

Lambda ทุกตัวอยู่ที่ `functions/<ชื่อ>/handler.py` และ import `common` (Terraform จะ zip `common/` ไปด้วยแบบเดียวกับ ingest)

## ลำดับใน state machine

```
PlanBatches → Map (ทีละ 4 batch) → CheckBatch
  ALREADY_PROCESSED / NO_DATA → จบ
  NEW / CHANGED → ValidateBatch
     PASS → TransformBatch → VerifyBatch → Published
     FAIL → QuarantineBatch → Quarantined
  error ใดๆ → NotifyFailure (SNS) → PipelineFailed
```

- `batch_eval.py` ใช้ร่วมกันโดย Validate/Transform: อ่าน raw ชุดเดียวกันแล้วตัดสินแถวเหมือนกันทุกครั้ง จึงไม่ต้องส่ง flag รายแถวผ่าน Step Functions (จำกัด 256 KB)
- ทุกแถวดิบตกอยู่ในกลุ่มเดียว: kept / duplicate / invalid ดังนั้น `raw = processed + duplicate + invalid` คือสิ่งที่ VerifyBatch ตรวจ
- `processed/_manifests/<batch_id>.json` คือเครื่องหมายว่า publish แล้ว (แทน `_SUCCESS`) เก็บ `manifest_hash` ที่ CheckBatch เห็น
- Map ตั้ง `ToleratedFailurePercentage: 100`: batch หนึ่งพังไม่หยุด batch อื่น ส่งอีเมลแจ้งทุกครั้ง แต่ execution รวมยังจบ Succeeded ให้ดู batch ที่พังจากอีเมลหรือหน้า Map run

## รันมือ

```bash
# ชั่วโมงที่ระบุ (ข้าม PlanBatches ไปใช้รายการนี้)
aws stepfunctions start-execution --state-machine-arn <pipeline_state_machine_arn> \
  --input '{"mode":"normal","batch_ids":["IOTW-A001__2026-11-16__08"]}'
# ทำใหม่แม้ publish แล้ว
aws stepfunctions start-execution --state-machine-arn <arn> \
  --input '{"mode":"force","batch_ids":["IOTW-A001__2026-11-16__08"]}'
```
