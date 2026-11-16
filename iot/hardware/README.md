# iot/hardware

## ตารางขา (ตาม `config.h`)

| อุปกรณ์ | ขา ESP32 | หมายเหตุ |
|---|---|---|
| ไมค์ INMP441 (I2S) | SD 33 / WS 25 / SCK 26 | 16 kHz, 16-bit, mono |
| SD card (SD_MMC 1-bit) | DAT0 2 / CLK 14 / CMD 15 | |
| DHT22 | 23 | offset temp −0.24 °C, humidity −6.0 % |
| I2C bus | SDA 21 / SCL 22 | DS3231 RTC, OLED SH1106, BH1750 |
| BH1750FVI (GY-302) | I2C 0x23 | Lux, calibration slope 0.753 / offset 1.5 |
| GPS NEO-8M | RX 16 / TX 17 | 9600 baud |

