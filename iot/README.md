# iot/

ทุกอย่างที่อยู่บนตัวอุปกรณ์ ทั้งแบบเดินเก็บ (`IOTW-A001`, `IOTW-A002`) และแบบตั้งประจำจุด (station)

อุปกรณ์คือ ESP32 ที่วัดเสียง แสง อุณหภูมิ ความชื้น และพิกัด GPS แล้วบันทึกลง SD card ทุก 1 วินาที พร้อมอัดเสียงดิบเป็นไฟล์ WAV
**SD card คือแหล่งความจริง** — อัป CSV ขึ้น S3 ทำแล้ว (#7: ส่ง HTTP ผ่าน relay บน EC2 `cloud/relay/` เพราะบอร์ดไม่มี RAM พอสำหรับ TLS) ส่วน realtime (WebSocket, #6) และอัป WAV (#20) ยังไม่ได้ทำ

| โฟลเดอร์ | เนื้อหา |
|---|---|
| `firmware/` | Arduino sketch ของ ESP32 (`firmware/main/`) |
| `hardware/` | ตารางขา, รายการอุปกรณ์, ไฟล์ 3D กล่อง (`hardware/enclosure/*.stl`) |
| `simulator/` | สคริปต์ Python จำลองอุปกรณ์ (ยังไม่ได้ทำ — issue #8) |

---

## 1. ระบบทำอะไรได้บ้าง (สถานะปัจจุบัน)

| ความสามารถ | รายละเอียด |
|---|---|
| วัดระดับเสียง | ไมค์ INMP441 (I2S, 16 kHz, 16-bit) คำนวณทั้ง **dB SPL (Z, ค่าหลัก)** และ **dBA** จากตัวอย่างชุดเดียวกัน, Leq ~1 วินาที แล้วทำ EMA, คาลิเบตด้วย `MIC_OFFSET_DB` (ตอนนี้ 123.2) |
| อัดเสียงดิบ | WAV mono 16 kHz ไฟล์ละ 60 วินาที (ปรับได้) แยกโฟลเดอร์รายชั่วโมง, อัปเดต header ทุก 5 วินาที → ไฟดับเสียไม่เกิน ~5 วินาที |
| วัดแสง | BH1750 (I2C 0x23) ทุก 200 ms, กรอง median + EMA, คาลิเบต 2 จุด (slope/offset) |
| วัดอุณหภูมิ/ความชื้น | DHT22 ทุก 2 วินาที พร้อม offset |
| พิกัด | GPS NEO-8M (UART2) lat/lon/alt/speed/ดาวเทียม/HDOP, fix เก่าเกิน 5 วินาที = ไม่ valid |
| เวลา | RTC DS3231 → ตั้งจาก NTP ตอนบูต (ถ้ามี Wi-Fi), จากหน้า Setup หรือ**จากเวลา GPS** เมื่อ RTC ไม่น่าเชื่อถือ; ไม่มี RTC ใช้วินาทีตั้งแต่บูตแทน |
| บันทึก CSV | 1 แถว/วินาที มี `Device_ID`, `Session`, `Record_ID` (นับแถวหายได้), ค่าเซนเซอร์, ธง valid และคอลัมน์ `Flags` บอกคุณภาพข้อมูล |
| ตรวจคุณภาพข้อมูล | ค่าไม่ valid = ช่องว่าง, ค่านอกช่วงยังเขียนแต่ติด flag `*_OOR`, ไมค์เสียก็ยังเขียนแถวต่อ (`SPL_ERR`) |
| จอ OLED | SH1106 แสดงเวลา, dB SPL + สถานะอัด, lux, อุณหภูมิ/ความชื้น, สถานะ GPS |
| Setup Mode | กดปุ่ม BOOT ตอนบูต → ESP32 เปิด Wi-Fi AP `IoT-Walking-Setup` + หน้าเว็บ (captive portal) ตั้งค่า Device ID, Wi-Fi, ค่าคาลิเบต, ช่วงบันทึก, ตั้งเวลา RTC จากมือถือ โดยไม่ต้องแฟลชใหม่ |
| ทนความผิดพลาด | watchdog รีบูตถ้า task ค้าง, I2S error ต่อเนื่อง → init ไดรเวอร์ใหม่, SD error → หยุดอัดแล้ว remount ทุก 10 วินาที, ไม่มี SD ก็ทำงานต่อ (จอขึ้น `NO SD CARD!`) |

ยังไม่มี: ส่ง realtime / อัป CSV-WAV ขึ้น cloud, `DEVICE_MODE` แบบ station ตามสเปค 10

---

## 2. โครงสร้างระบบ

### ฮาร์ดแวร์ (รายละเอียดขา: `hardware/README.md`)

```
                ┌── I2S ────── INMP441 (ไมค์)
                ├── SD_MMC ─── SD card (1-bit)
   ESP32 ───────┼── GPIO23 ─── DHT22
                ├── UART2 ──── GPS NEO-8M
                └── I2C ────── DS3231 RTC, OLED SH1106, BH1750
```

### ซอฟต์แวร์: FreeRTOS 6 task บน 2 core

| Task | Core | Priority | รอบ | หน้าที่ |
|---|---|---|---|---|
| `micTask` | 1 | 2 | ต่อเนื่อง | อ่าน I2S → ใส่ `AudioBlock` ลงคิว |
| `sdProcessTask` | 0 | 1 | ต่อเนื่อง | คำนวณ dB, เขียน WAV, เขียน CSV ทุก 1 วินาที, ดูแลสุขภาพ SD |
| `gpsTask` | 0 | 1 | 50 ms | parse NMEA |
| `luxTask` | 0 | 1 | 200 ms | อ่าน BH1750 + กรอง |
| `dhtTask` | 0 | 1 | 2 s | อ่าน DHT22 |
| `oledTask` | 0 | 1 | 500 ms | วาดหน้าจอ |
| `loop()` | 1 | — | 10 s | พิมพ์บรรทัด `[STAT]` ทาง Serial |

ไมค์แยกอยู่ core 1 คนเดียวเพื่อไม่ให้เสียงขาด งานเขียน SD ที่ช้าอยู่ core 0
ข้อมูลระหว่าง task ใช้ mutex แยกต่ออุปกรณ์ (`sdMutex`, `gpsMutex`, `luxMutex`, `dhtMutex`, `wireMutex` สำหรับบัส I2C ที่ใช้ร่วมกัน)

---

## 3. แต่ละไฟล์ทำหน้าที่อะไร (`firmware/main/`)

| ไฟล์ | หน้าที่ |
|---|---|
| `main.ino` | จุดเริ่ม: `setup()` ลำดับการบูต (ดูข้อ 4.1), ตาราง task `kTasks[]`, `loop()` พิมพ์สถานะทุก 10 วินาที |
| `config.h` | ค่าคงที่ทั้งหมด: ขา, ค่าคาลิเบตเริ่มต้น, ขนาด buffer/stack, เกณฑ์ Data Quality — ค่าที่มี `[setup]` เป็นแค่ค่าเริ่มต้น แก้ทับได้จากหน้า Setup |
| `secrets.example.h` | แม่แบบ Wi-Fi → คัดลอกเป็น `secrets.h` (git ไม่เก็บ) |
| `globals.h/.cpp` | ตัวแปรที่ใช้ร่วม: struct `AudioBlock`/`GPSData`/`LuxData`/`DHTData`, mutex, คิว audio pool, สถานะ (`currentSPL`, `isRecording`, …), `MutexLock` (RAII), `readXxxSafe()` อ่านค่าเซนเซอร์อย่างปลอดภัย |
| `settings.h/.cpp` | ค่าที่แก้ได้ตอนรัน (`RuntimeSettings`) เก็บใน NVS: โหลด/บันทึก/รีเซ็ต, ตรวจช่วงค่า, สร้าง Device ID จาก MAC ถ้าเว้นว่าง, นับ `Session` ใหม่ทุกครั้งที่บูต |
| `config_portal.h/.cpp` | Setup Mode: ตรวจปุ่ม BOOT, เปิด AP + DNS + WebServer, ฟอร์มตั้งค่าและตั้งเวลา, บันทึกแล้วรีสตาร์ท (ออกเองถ้าไม่ใช้งาน 5 นาที) |
| `wifi_ntp.h/.cpp` | ตอนบูตเท่านั้น: ต่อ Wi-Fi ตัวที่ 1 แล้วตัวที่ 2 → ดึงเวลา NTP ตั้ง RTC → ปิด Wi-Fi |
| `rtc_helper.h/.cpp` | DS3231: อ่าน/ตั้งเวลา, ตัดสินว่าเวลาเชื่อถือได้ไหม (`Time_Valid`), ตั้งจากเวลา UTC ของ GPS, สร้างสตริงเวลา/ชื่อไฟล์/ชื่อโฟลเดอร์, มาโคร `LOG()` |
| `audio_engine.h/.cpp` | ไดรเวอร์ I2S (INMP441), `micTask`, เขียน header WAV 44 ไบต์, init ใหม่เมื่อ error ต่อเนื่อง |
| `sd_logger.h/.cpp` | หัวใจของการบันทึก: mount SD, สร้าง CSV, คำนวณ dB Z/A, เขียน/หมุนไฟล์ WAV, เขียนแถว CSV + Flags, นับ error และ remount, `sdProcessTask` |
| `sensors.h/.cpp` | BH1750 (median + EMA + คาลิเบต, init ใหม่เมื่ออ่านพลาดติดกัน) และ DHT22 (offset), `luxTask`, `dhtTask` |
| `gps_handler.h/.cpp` | UART2 + TinyGPSPlus, `gpsProbe()` เช็กตอนบูตว่ามี NMEA, `gpsTask`, ส่งเวลา GPS ไปตั้ง RTC |
| `oled_display.h/.cpp` | U8g2 SH1106: หน้าสถานะตอนบูต และ `oledTask` หน้าหลัก |

Library ที่ใช้และเวอร์ชัน: ดู [`firmware/README.md`](firmware/README.md) ข้อ 1.1

---

## 4. กระบวนการทำงานโดยรวม

### 4.1 ตอนบูต (`setup()`)

```
สร้างคิว/mutex → เริ่ม I2C + OLED
→ โหลดค่าจาก NVS (ไม่มี = ใช้ config.h) → Session ใหม่
→ เริ่ม RTC
→ [กด BOOT ค้าง ~1.5 s ภายใน 3 s] → Setup Mode → บันทึก → รีสตาร์ท
→ ต่อ Wi-Fi ดึง NTP ตั้ง RTC → ปิด Wi-Fi   (ข้ามถ้าไม่มี RTC / ต่อไม่ได้)
→ เริ่ม BH1750, DHT22 → warm-up 3 s
→ เริ่ม GPS + เช็กว่ามี NMEA
→ mount SD + สร้าง CSV   (ไม่มีการ์ด = ทำงานต่อ ลองใหม่ทุก 10 s)
→ ตั้ง watchdog 10 s → สร้าง 6 task
```

### 4.2 ระหว่างทำงาน (การไหลของข้อมูล)

```
INMP441 ─I2S─► micTask ─► readyQueue ─► sdProcessTask ─► freeQueue ─► (กลับไปใช้ซ้ำ)
                         (pool 32 บล็อก ≈ 1 s)    │
                                                  ├─► เขียน WAV  /YYYYMMDD/HH/audio_<ts>.wav
                                                  ├─► คำนวณ dB SPL (Z) + dBA → currentSPL / currentSPLA
                                                  └─► ทุก 1 s: อ่าน GPS/Lux/DHT ล่าสุด
                                                       → ตรวจ valid + Flags
                                                       → เขียนแถว /YYYYMMDD/spl_log_<ts>.csv
gpsTask / luxTask / dhtTask ─► อัปเดตค่าล่าสุด (ภายใต้ mutex) ─► oledTask แสดงผล
```

- **เสียง:** แต่ละบล็อก 512 ตัวอย่าง (32 ms) → สะสม mean-square 30 บล็อก (~0.96 s) → `dB = 20·log10(rms) + MIC_OFFSET_DB` → EMA (α 0.3) ค่า A ผ่านฟิลเตอร์ A-weighting ก่อน ส่วน WAV เก็บเสียงดิบไม่ถ่วงน้ำหนัก
- **WAV:** หมุนไฟล์ตามจำนวนไบต์เสียงที่เขียนจริง (ไม่ใช่เวลานาฬิกา) ทุกไฟล์จึงยาวเท่ากัน; ไม่เริ่มไฟล์ใหม่ถ้าพื้นที่ SD เหลือน้อยกว่า `SD_MIN_FREE_MB`
- **CSV:** เปิดไฟล์ค้างไว้และ flush ทุกแถว, `Record_ID` เพิ่มทุกแถวแม้เขียนพลาด → เลขที่ขาด = แถวที่หาย
- **ถ้าไมค์ค้างเกิน 3 s:** `splValid = false` → แถวยังเขียนต่อแต่ dB ว่างและ Flags มี `SPL_ERR`, ปิด WAV ที่ค้าง

### 4.3 รูปแบบ CSV (สเปคเต็ม: `.claude/specs/01-data-schema.md`)

```
Device_ID,Device_Type,Session,Record_ID,DateTime,dBA,dB_SPL,Lux,Temp_C,Humidity_pct,Latitude,Longitude,Altitude_m,Speed_kmh,Satellites,HDOP,GPS_Valid,Lux_Valid,DHT_Valid,Time_Valid,SPL_Valid,Flags
```

- `DateTime` = เวลาไทยไม่มี offset (`YYYY-MM-DD HH:MM:SS`) ถ้าไม่มี RTC เป็นวินาทีตั้งแต่บูต
- `Flags` = `OK` หรือรหัสคั่น `;` : `TIME_INVALID`, `SPL_ERR`/`SPL_OOR`, `LUX_ERR`/`LUX_OOR`, `DHT_ERR`, `TEMP_OOR`/`HUMID_OOR`, `GPS_MISSING`/`GPS_POOR`

### 4.4 โครงไฟล์บน SD card

```
/20261116/
├── spl_log_20261116_083000.csv   ← 1 ไฟล์ต่อการบูต 1 ครั้ง
├── 08/audio_20261116_083005.wav  ← WAV แยกโฟลเดอร์รายชั่วโมง
└── 09/...
/logs/                            ← ใช้แทนเมื่อไม่มี RTC
```

### 4.5 การจัดการเมื่อผิดพลาด

| เหตุการณ์ | ระบบทำอะไร |
|---|---|
| task ค้างเกิน 10 s | watchdog panic → รีบูต (Session ใหม่) |
| I2S อ่านพลาด 50 ครั้งติด | init ไดรเวอร์ I2S ใหม่ |
| SD error 10 ครั้งติด / ถอดการ์ด | หยุดอัด, ปิด WAV ให้สมบูรณ์, จอ `NO SD CARD!`, remount ทุก 10 s แล้วทำต่อเอง |
| BH1750 อ่านพลาดติดกัน | init เซนเซอร์ใหม่, แถวนั้น `Lux_Valid=0` |
| ไม่มี GPS fix | พิกัดว่าง, `GPS_MISSING` |
| ถ่าน RTC หมด | `Time_Valid=0` จนได้เวลาจาก NTP / หน้า Setup / GPS |
| ไฟดับกะทันหัน | WAV เสียไม่เกิน ~5 s, CSV เสียไม่เกิน 1 แถว |

---

## 5. เริ่มใช้งาน

คู่มือ build/แฟลช, ค่าใน `config.h`, การคาลิเบต, Setup Mode, การอ่านจอ/Serial log และการแก้ปัญหา อยู่ใน [`firmware/README.md`](firmware/README.md)
