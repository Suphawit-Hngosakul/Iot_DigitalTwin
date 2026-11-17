# cloud/pipeline (issue #11–#16)

Step Functions รายชั่วโมง (EventBridge Scheduler)

```
cloud/pipeline/
├── statemachine/pipeline.asl.json
├── functions/
│   ├── plan_batches/      # สร้างรายการ batch ให้ Map (D21)
│   ├── check_batch/
│   ├── validate_batch/
│   ├── transform_batch/
│   ├── verify_batch/
│   └── quarantine_batch/
├── common/        # schema.py, batch_manifest.py, devices.csv, grid.py
└── tests/         # pytest + CSV ตัวอย่าง (ดี/เสีย/ซ้ำ/ขาด)
```

## รันเทสต์

```bash
pip install -r cloud/pipeline/requirements-dev.txt
python -m pytest cloud/pipeline/tests -q
```

Lambda ทุกตัวอยู่ที่ `functions/<ชื่อ>/handler.py` และ import `common` (Terraform จะ zip `common/` ไปด้วยแบบเดียวกับ ingest)
