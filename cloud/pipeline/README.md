# cloud/pipeline (issue #11–#16)

Step Functions รายชั่วโมง (EventBridge Scheduler)

```
cloud/pipeline/
├── statemachine/pipeline.asl.json
├── functions/
│   ├── check_batch/
│   ├── validate_batch/
│   ├── transform_batch/
│   ├── verify_batch/
│   └── quarantine_batch/
├── common/        # schema.py, ช่วงค่า, devices.csv, grid.py
└── tests/         # pytest + CSV ตัวอย่าง (ดี/เสีย/ซ้ำ/ขาด)
```
