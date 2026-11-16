# iot/firmware

ESP32 + Arduino + FreeRTOS

โครง:

```
iot/firmware/
├── main/                 # Arduino sketch (main.ino + *.cpp/*.h)
│   ├── config.h          # ค่าคงที่ [setup], ขา, offset
│   └── secrets.example.h # คัดลอกเป็น secrets.h (ห้าม commit)
└── README.md
```

ค่าตั้งต่อเครื่องใน `config.h`: `DEVICE_ID` = `walk-01` / `walk-02` / `station-01`, `DEVICE_MODE` = `mobile` / `stationary`, `MIC_OFFSET_DB` ≈ 123
