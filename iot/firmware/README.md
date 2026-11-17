# iot/firmware

คู่มือ build, แฟลช, ตั้งค่า และแก้ปัญหา firmware ของ ESP32 (Arduino + FreeRTOS)
ภาพรวมระบบ, หน้าที่ของแต่ละไฟล์ และ flow การทำงาน ดูที่ [`../README.md`](../README.md)

```
iot/firmware/
├── README.md               # คู่มือนี้
└── main/                   # Arduino sketch (ชื่อโฟลเดอร์ต้องตรงกับ main.ino)
    ├── main.ino            # จุดเริ่ม: setup() ลำดับบูต, ตารางสร้าง 6 task, loop() พิมพ์ [STAT] ทุก 10 s
    ├── config.h            # ค่าคงที่ทั้งหมด: ขา, ค่าเริ่มต้น [setup], buffer/stack, เกณฑ์ Data Quality
    ├── secrets.example.h   # แม่แบบ Wi-Fi → คัดลอกเป็น secrets.h
    ├── secrets.h           # Wi-Fi จริงของเครื่อง (git ไม่เก็บ)
    ├── globals.h/.cpp      # ตัวแปรใช้ร่วม: struct ข้อมูลเซนเซอร์, mutex, คิว audio, สถานะ, MutexLock, readXxxSafe()
    ├── settings.h/.cpp     # ค่าที่แก้ได้ตอนรัน เก็บใน NVS: โหลด/บันทึก/รีเซ็ต, ตรวจช่วง, Device ID จาก MAC, Session
    ├── config_portal.h/.cpp # Setup Mode: ปุ่ม BOOT, Wi-Fi AP + หน้าเว็บตั้งค่า + ตั้งเวลา RTC
    ├── wifi_ntp.h/.cpp     # ตอนบูต: ต่อ Wi-Fi → ดึงเวลา NTP ตั้ง RTC → ปิด Wi-Fi
    ├── rtc_helper.h/.cpp   # DS3231: อ่าน/ตั้งเวลา, Time_Valid, ตั้งจากเวลา GPS, สร้างชื่อไฟล์/โฟลเดอร์, มาโคร LOG()
    ├── audio_engine.h/.cpp # ไมค์ INMP441 (I2S): micTask, header WAV, init ไดรเวอร์ใหม่เมื่อ error
    ├── sd_logger.h/.cpp    # หัวใจการบันทึก: mount SD, คำนวณ dB SPL/dBA, เขียน WAV + CSV + Flags, remount, sdProcessTask
    ├── sensors.h/.cpp      # BH1750 (กรอง median+EMA, คาลิเบต) และ DHT22 (offset): luxTask, dhtTask
    ├── gps_handler.h/.cpp  # GPS NEO-8M (UART2 + TinyGPSPlus): gpsProbe ตอนบูต, gpsTask
    └── oled_display.h/.cpp # จอ SH1106 (U8g2): หน้าสถานะตอนบูต และ oledTask หน้าหลัก
```

---

## 1. Build และแฟลช

### 1.1 สิ่งที่ต้องติดตั้ง

เวอร์ชันด้านล่างคือที่ติดตั้งจริงในเครื่องที่ใช้ build ตอนนี้ (Library Manager → ค้นชื่อ → เลือกเวอร์ชันให้ตรง)

| Library | เวอร์ชัน | ผู้พัฒนา | ใช้กับ |
|---|---|---|---|
| **esp32** (Boards Manager) | 3.x | Espressif | core — ต้อง 3.x เพราะใช้ไดรเวอร์ I2S แบบใหม่ (`driver/i2s_std.h`) |
| `U8g2` | 2.36.19 | oliver (olikraus) | OLED SH1106 |
| `RTClib` | 2.1.4 | Adafruit | DS3231 |
| `Adafruit BusIO` | 1.17.4 | Adafruit | dependency ของ RTClib |
| `BH1750` | 1.3.0 | Christopher Laws | แสง |
| `DHT sensor library` | 1.4.7 | Adafruit | DHT22 |
| `Adafruit Unified Sensor` | 1.1.15 | Adafruit | dependency ของ DHT sensor library |
| `TinyGPSPlus` | 1.0.3 | Mikal Hart | GPS NEO-8M |

`WiFi`, `WebServer`, `DNSServer`, `Preferences`, `SD_MMC` มากับ ESP32 core แล้ว

### 1.2 ตั้งค่าใน Arduino IDE (เมนู Tools)

| ค่า | ตั้งเป็น |
|---|---|
| Board | **ESP32 Dev Module** |
| Partition Scheme | Default ก็ได้ ถ้าขึ้น `Sketch too big` → **Huge APP (3MB No OTA)** |
| Erase All Flash Before Sketch Upload | **Disabled** (ถ้าเปิด ค่าที่ตั้งจากหน้า Setup จะหายทุกครั้งที่อัปโหลด) |
| Port | COM ของบอร์ด |

### 1.3 ขั้นตอน

1. คัดลอก `main/secrets.example.h` → `main/secrets.h` แล้วใส่ Wi-Fi สำหรับ sync เวลา (ไม่ใส่ก็ได้ ตั้งจากหน้า Setup ทีหลัง)
2. เปิด `main/main.ino` → Upload
3. ถ้าค้างที่ `Connecting....` กดปุ่ม **BOOT** ค้างจนขึ้น `Writing at ...` ถ้ายังไม่ได้ให้ถอด SD card ก่อน (GPIO 2/15 เป็น strapping pin)
4. เปิด Serial Monitor **115200** ดูว่าบูตผ่านทุกขั้น (ข้อ 5)

> compile ครั้งแรกใช้เวลาหลายนาที ถ้าโฟลเดอร์อยู่บน Google Drive / OneDrive จะช้ามาก แนะนำให้วางไว้ในไดรฟ์ C

---

## 2. ค่าตั้งใน `config.h`

ค่ามี 2 แบบ:
- **[setup]** = ค่าเริ่มต้นเท่านั้น แก้จากหน้า Setup Mode ได้ (เก็บใน NVS และ**ทับ**ค่าใน `config.h`)
- **compile** = ต้องแก้ `config.h` แล้วแฟลชใหม่

> แก้ค่า [setup] ใน `config.h` แล้วแฟลช แต่ค่าไม่เปลี่ยน = บอร์ดเคยบันทึกค่าจากหน้า Setup ไว้ → แก้ในหน้า Setup หรือกด "คืนค่าเริ่มต้น" ดูค่าที่ใช้จริงได้จากบรรทัด `[CFG]` ใน Serial

### 2.1 ค่าต่อเครื่อง [setup]

| ค่า | ตอนนี้ | ความหมาย |
|---|---|---|
| `DEVICE_ID` | `""` | ชื่อเครื่องใน CSV เช่น `walk-01`, `station-01` (A-Z a-z 0-9 - _ ≤ 16 ตัว) ว่าง = `IOTW-` + 3 byte ท้ายของ MAC |
| `DEVICE_TYPE` | `1` | 0 = Stationary, 1 = Mobile |
| `MIC_OFFSET_DB` | `123.2` | แปลง dBFS → dB (ใช้ทั้ง dB SPL และ dBA) — ข้อ 3.1 |
| `DHT_TEMP_OFFSET` / `DHT_HUMID_OFFSET` | `-0.24` / `-6.0` | บวกเข้ากับค่าที่อ่านได้ |
| `LUX_CAL_SLOPE` / `LUX_CAL_OFFSET` | `0.753` / `1.5` | `lux = raw × slope + offset` |
| `WIFI_SSID_1/2`, `WIFI_PASS_1/2` | จาก `secrets.h` | Wi-Fi สำหรับ NTP ตอนบูต (ลองชุด 1 ก่อน) |
| `RECORD_TIME` | `60` | วินาทีต่อไฟล์ WAV |
| `CSV_INTERVAL_MS` | `1000` | ระยะห่างแถว CSV |
| `SD_MIN_FREE_MB` | `50` | พื้นที่เหลือต่ำกว่านี้ = ไม่เริ่มไฟล์ WAV ใหม่ (CSV ยังเขียน) |

### 2.2 ค่าที่ต้องแฟลชใหม่ (compile)

| หมวด | ค่า | ตอนนี้ |
|---|---|---|
| ขา | I2S `33/25/26`, I2C `21/22`, GPS `16/17` @9600, DHT `23`, SD_MMC `2/14/15`, BH1750 `0x23` | ดู `../hardware/README.md` |
| เสียง | `SAMPLE_RATE` / `BUFFER_LEN` / `AUDIO_POOL_SIZE` / `RMS_WINDOW` / `EMA_ALPHA` | 16000 / 512 / 32 / 30 / 0.3 |
| เวลา | `NTP_SERVER` / `NTP_GMT_OFFSET` / `WIFI_TIMEOUT_MS` | pool.ntp.org / 25200 (UTC+7) / 10000 |
| SD | `SD_ERROR_THRESHOLD` / `SD_REMOUNT_INTERVAL_MS` | 10 ครั้ง / 10 s |
| Data Quality | `SPL_MIN_DB`–`SPL_MAX_DB`, `TEMP_MIN_C`–`TEMP_MAX_C`, `HUMID_*`, `LUX_RAW_MAX`, `GPS_MAX_HDOP`, `SPL_STALE_MS`, `TIME_MIN_YEAR` | 30–120 dB, −40–80 °C, 0–100 %, 54612, 5, 3000 ms, 2025 |
| ระบบ | `WDT_TIMEOUT_S` / `I2S_ERROR_THRESHOLD` / `GPS_VALID_TIMEOUT_MS` / `SENSOR_WARMUP_SEC` | 10 / 50 / 5000 / 3 |
| Stack | `STACK_MIC`, `STACK_SD_PROCESS`, `STACK_GPS`, `STACK_LUX`, `STACK_DHT`, `STACK_OLED` | 20000, 20000, 4096 ×3, 8192 |
| Setup Mode | `CONFIG_AP_SSID` / `CONFIG_AP_PASS` / `CONFIG_PORTAL_TIMEOUT_MS` / `CONFIG_BUTTON_PIN` | IoT-Walking-Setup / walking123 / 5 นาที / GPIO0 |

### 2.3 เวลาบน SD (`DateTime`)

- คอลัมน์ `DateTime` ใน CSV และชื่อโฟลเดอร์/ไฟล์บน SD เป็น**เวลาไทย (UTC+7)** รูปแบบ `YYYY-MM-DD HH:MM:SS` **ไม่มี offset** ทุกเครื่องต้องเหมือนกัน เพราะ cloud แบ่งข้อมูลรายชั่วโมงจากค่านี้ และแปลงเป็น UTC เอง (`ts_utc = ts_local − 7 ชม.`)
- แหล่งตั้งเวลา RTC ทุกทางให้ผลเป็นเวลาไทย: NTP ตอนบูต (`configTime(NTP_GMT_OFFSET, …)`), หน้า Setup จากเวลาในมือถือ, หรือ GPS (UTC + `NTP_GMT_OFFSET`) ห้ามเปลี่ยน `NTP_GMT_OFFSET` เป็นค่าอื่น
- ไม่มี RTC → `DateTime` เป็นเลขวินาทีตั้งแต่บูต, `Time_Valid=0`
- มี RTC แต่ไฟเคยดับและยังไม่ได้ตั้งเวลา (หรือปี < `TIME_MIN_YEAR`) → `Time_Valid=0` + flag `TIME_INVALID`
- ทั้งสองกรณี cloud จะนับเป็น `NO_RTC` และไม่เอาแถวไปใช้ (ดู `docs/data-schema.md`)

---

## 3. คาลิเบต

ทำแยกทุกเครื่อง และทำใหม่เมื่อเปลี่ยนกล่องหรือตำแหน่งเซนเซอร์ ใส่ค่าใหม่ผ่านหน้า Setup (ไม่ต้องแฟลช)

### 3.1 ไมค์ (`MIC_OFFSET_DB`)

สูตรในเครื่อง: `dB = 20·log10(V_rms) + MIC_OFFSET_DB` (V_rms เป็นสัดส่วนของ full scale) → offset ที่ถูก = `dB จากเครื่องอ้างอิง − dBFS`

**วิธีที่ใช้อยู่: เทียบกับ Sound Level Meter Class 1 หลายจุด** (ค่า 123.2 ได้จากวิธีนี้)
1. วางไมค์ ESP32 คู่กับเครื่อง Class 1 ตั้งเครื่องเป็น **Z-weighting** (เพราะ dB SPL คือค่าหลัก)
2. เปิดโทนคงที่ 500 / 1000 / 2000 Hz ความถี่ละ 3 ระดับ (ดังกว่าเสียงพื้นหลัง ≥ 10 dB) จดค่าจากเครื่อง
3. ใช้ไฟล์ WAV จาก SD ช่วงเดียวกัน คำนวณ dBFS ของแต่ละช่วง (สคริปต์และคู่มือใน project files: `tools/wav_spl.py`, `tools/tutorial/`)
4. offset = ค่าเฉลี่ยของ `meter_dB − dBFS` ทุกจุด ถ้า offset แต่ละความถี่ต่างกันเกิน 1–2 dB ให้ยึดค่าจาก 1000 Hz

**วิธีเร็ว: acoustic calibrator 94 dB @ 1 kHz**
1. ครอบ calibrator แนบรูไมค์ให้สนิท รอค่าบนจอนิ่ง ~5 s
2. `offset ใหม่ = offset เดิม + (94 − ค่าบนจอ)` แล้วตรวจซ้ำ ควรได้ 94.0 ± 0.3
(ที่ 1 kHz ค่า A-weighting = 0 dB จึงใช้ offset ตัวเดียวได้ทั้ง dB SPL และ dBA)

### 3.2 DHT22

วางคู่เครื่องวัดอ้างอิงในที่ร่ม ลมนิ่ง ≥ 30 นาที → `offset = ค่าอ้างอิง − ค่าที่เครื่องอ่าน` (ใส่ทั้งอุณหภูมิและความชื้น)

### 3.3 BH1750 (2 จุด)

วัดที่แสงน้อยและแสงมากคู่ lux meter อ้างอิง → `slope = (ref2 − ref1) / (raw2 − raw1)`, `offset = ref1 − slope × raw1`
(ค่าในเครื่องคือ `raw` ก่อนคาลิเบต — ตั้ง slope = 1, offset = 0 ชั่วคราวเพื่ออ่าน raw)

---

## 4. Setup Mode (ตั้งค่าผ่าน Wi-Fi ไม่ต้องแฟลชใหม่)

ใช้ได้แม้ไม่มีเราเตอร์หรืออินเทอร์เน็ต

1. กด **EN/RST** แล้ว**ปล่อย**
2. พอจอขึ้น `Hold BOOT button` / `to enter Setup Mode` ให้**กด BOOT ค้าง ~2 วินาที**
   > อย่ากด BOOT ค้างตั้งแต่ตอนกด reset เพราะ ESP32 จะเข้าโหมดอัปโหลดแทน
3. จอขึ้น `SETUP MODE (WiFi)` → มือถือต่อ Wi-Fi **`IoT-Walking-Setup`** รหัส **`walking123`** (ถ้าเตือนว่าไม่มีเน็ต ให้เลือกใช้ต่อ)
4. หน้าตั้งค่าเด้งเอง ถ้าไม่เด้งเปิด **`http://192.168.4.1`**

| ส่วนในหน้าเว็บ | ทำอะไร |
|---|---|
| **ตั้งเวลา RTC ตามมือถือ** | เขียนเวลามือถือลง DS3231 ทันที (ต้องมี RTC) |
| อุปกรณ์ | Device ID และประเภท Stationary / Mobile |
| WiFi สำหรับ sync เวลา | SSID/รหัส 2 ชุด (ช่องรหัสว่าง = ใช้รหัสเดิม, ลบ SSID = ไม่ใช้ชุดนั้น) |
| Calibration | `MIC_OFFSET_DB`, offset DHT, slope/offset Lux |
| การบันทึก | `RECORD_TIME`, `CSV_INTERVAL_MS`, `SD_MIN_FREE_MB` |
| **บันทึกและรีสตาร์ท** | เก็บลง NVS แล้วบูตเข้าโหมดเก็บข้อมูล |
| **ออก (ไม่บันทึก)** | กลับไปเก็บข้อมูลโดยไม่แก้อะไร |
| **คืนค่าเริ่มต้น (config.h)** | ลบค่าใน NVS ทั้งหมด |

- ไม่มีการใช้งาน **5 นาที** → ออกจาก Setup Mode เอง
- ค่าใน NVS อยู่รอดแม้ปิดเครื่องหรือแฟลชใหม่ (ยกเว้นสั่ง Erase Flash)
- ทุกค่าถูกตรวจช่วงทั้งตอนบันทึกและตอนโหลด ค่าที่เพี้ยนจะกลับเป็นค่าใน `config.h`

---

## 5. จอ OLED และ Serial log

### 5.1 จอขณะเก็บข้อมูล

| แถว | ตัวอย่าง | ความหมาย |
|---|---|---|
| 1 | `17/11/2026 14:30:00` | เวลาจาก RTC (`uptime: Ns` = ไม่พบ RTC) |
| 2 | `62.3dB REC(12/60s)` | dB SPL + สถานะอัด WAV · `IDLE xxMB` = ไม่ได้อัด · `NO SD CARD!` = ไม่มีการ์ด/การ์ดเสีย · `--.-dB` = ไมค์ไม่มีข้อมูล |
| 3 | `Lux:350.2` | ความเข้มแสง |
| 4 | `T:30.1°C  H:65.0%` | อุณหภูมิ / ความชื้น |
| 5 | `13.12345,100.12345 S:8` | พิกัด + จำนวนดาวเทียม (`NoFix` = ยังไม่มีพิกัด) |

### 5.2 Serial Monitor (115200)

รูปแบบ: `[เวลา] [TAG] ข้อความ`

| Tag | เมื่อไร | ดูอะไร |
|---|---|---|
| `[CFG]` | ตอนบูต | ค่าที่ใช้จริง (หลังรวม NVS) เช่น `MIC_OFFSET=123.20dB` |
| `[INFO]` | ทั่วไป | ขั้นบูต, `Recording: /20261117/14/audio_....wav`, `Recording complete` (ขนาด / ความยาว / dropped blocks) |
| `[STAT]` | ทุก 10 s | `Heap` (ควรนิ่ง ไม่ลดเรื่อย ๆ), `SDFree`, `SPL:..dB (A ..dBA)`, Lux, Temp, Humi, `GPS:FIX/NoFx` + ดาวเทียม + HDOP, `SD:OK/NONE`, `Rec:YES/No` |
| `[ERROR]` | มีปัญหา | SD mount/เขียนไม่ได้, I2S error, RTC not found, สร้างไฟล์ไม่ได้ ฯลฯ |

---

## 6. แก้ปัญหาที่พบบ่อย

| อาการ | สาเหตุ / วิธีแก้ |
|---|---|
| compile: `xxx.h: No such file or directory` | ยังไม่ได้ลง library (ข้อ 1.1) หรือ Sketchbook location ไม่ใช่โฟลเดอร์ที่มี `libraries` |
| compile: error เกี่ยวกับ `i2s_std.h` | ESP32 core ต่ำกว่า 3.x → อัปเดตใน Boards Manager |
| `Sketch too big` | Partition Scheme → **Huge APP (3MB No OTA)** |
| อัปโหลดค้าง `Connecting....` | กด BOOT ค้างตอนขึ้น Connecting / ถอด SD card |
| ค่าใน `config.h` ไม่มีผล | ค่าใน NVS ทับอยู่ → แก้ในหน้า Setup หรือคืนค่าเริ่มต้น |
| จอขึ้น `uptime: Ns`, ไฟล์ไปอยู่ `/logs` | หา DS3231 ไม่เจอ → ตรวจสาย SDA 21 / SCL 22 / VCC / GND แล้วถอดไฟเสียบใหม่ |
| CSV มี `TIME_INVALID` ตลอด | ถ่าน RTC หมดและยังไม่ได้ตั้งเวลา → ใส่ Wi-Fi ให้ sync NTP, กดตั้งเวลาในหน้า Setup หรือรอ GPS fix |
| จอขึ้น `NO SD CARD!` | ไม่มีการ์ด / การ์ดหลวม / ฟอร์แมตไม่ใช่ FAT32 — เสียบใหม่ได้เลย เครื่อง mount เองภายใน 10 s |
| จอขึ้น `--.-dB`, CSV มี `SPL_ERR` | ไมค์ไม่ส่งข้อมูล → ตรวจสาย SD 33 / WS 25 / SCK 26 / L/R ต่อ GND |
| GPS `NoFix` นาน / `No data from GPS!` ตอนบูต | อยู่ในอาคาร (ออกที่โล่ง รอ 1–5 นาที) / ถ้าไม่มี NMEA เลย ตรวจสาย TX ของ GPS → GPIO16 |
| `Recording complete` มี dropped blocks | SD ช้า → ใช้การ์ด Class 10 / A1 ขึ้นไป และฟอร์แมตใหม่ |
| บอร์ดรีบูตเอง | ดู log ก่อนรีบูต: `task_wdt` = task ค้าง, `Brownout` = ไฟไม่พอ (ใช้สาย/พาวเวอร์แบงก์ที่จ่ายได้ ≥ 1 A) |
| กด BOOT แล้วไม่เข้า Setup Mode | ต้องกด**หลัง**ปล่อย reset และตอนจอขึ้น `Hold BOOT button` ค้าง ~2 วินาที |
| มือถือไม่เด้งหน้าตั้งค่า | ปิด mobile data ชั่วคราว แล้วเปิด `http://192.168.4.1` เอง |
