#ifndef CONFIG_H
#define CONFIG_H

// Values marked [setup] are only DEFAULTS: they can be changed from the
// Setup Mode web page (see "Setup Mode" at the bottom) and are kept in NVS.
// Pins, buffers and stack sizes stay compile-time only.

// =============================================================================
//  Device Identity  (each record carries Device_ID + Device_Type)
// =============================================================================

// Empty = build from the chip MAC, e.g. "IOTW-A1B2C3" (unique per board).
// Allowed: A-Z a-z 0-9 - _ , 1..16 chars.
#define DEVICE_ID ""          // [setup]
#define DEVICE_TYPE 1         // [setup] 0 = Stationary, 1 = Mobile

// =============================================================================
//  Pin Assignments
// =============================================================================

// ── I2S INMP441 ──────────────────────────────────────────────────────────────
#define I2S_SD 33
#define I2S_WS 25
#define I2S_SCK 26
#define I2S_PORT I2S_NUM_0

// ── I2C Bus (DS3231 + SH1106 + BH1750) ──────────────────────────────────────
#define I2C_SDA 21
#define I2C_SCL 22

// ── GPS NEO-8M (UART2) ──────────────────────────────────────────────────────
#define GPS_RX_PIN 16
#define GPS_TX_PIN 17
#define GPS_BAUD 9600

// ── BH1750FVI (GY-302) ──────────────────────────────────────────────────────
#define BH1750_ADDR 0x23

// ── DHT22 ────────────────────────────────────────────────────────────────────
#define DHT_PIN 23
#define DHT_TYPE 22

// ── SD_MMC (1-bit mode) ─────────────────────────────────────────────────────
#define SDMMC_DAT0_PIN 2
#define SDMMC_CLK_PIN 14
#define SDMMC_CMD_PIN 15

// =============================================================================
//  Sensor Calibration
// =============================================================================

// ── DHT22 offset ─────────────────────────────────────────────────────────────
#define DHT_TEMP_OFFSET -0.24f // [setup]
#define DHT_HUMID_OFFSET -6.0f // [setup]

// ── BH1750 2-point linear calibration ────────────────────────────────────────
//  corrected = raw * slope + offset
//  Ref 10 lux → 78 displayed | Ref 1300 lux → 1080 displayed (2025-04-03)
#define LUX_CAL_SLOPE 0.753f // [setup]
#define LUX_CAL_OFFSET 1.5f // [setup]
#define LUX_MEDIAN_WINDOW 3 // odd number — cuts I2C glitch spikes
#define LUX_EMA_ALPHA 0.3f

// ── dB SPL (INMP441) ────────────────────────────────────────────────────────
#define MIC_OFFSET_DB 123.2f // [setup] calibrated vs Class 1 meter, mean of 9 points (500/1000/2000 Hz)
#define RMS_WINDOW 30
#define EMA_ALPHA 0.3f

// =============================================================================
//  WiFi / NTP  (boot-time RTC sync only; WiFi is turned off afterwards)
// =============================================================================

// WiFi credentials are NOT stored in this file. Set them from the Setup Mode
// page, or copy secrets.example.h -> secrets.h (git-ignored) to give defaults.
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef WIFI_SSID_1
#define WIFI_SSID_1 "" // [setup]
#endif
#ifndef WIFI_PASS_1
#define WIFI_PASS_1 "" // [setup]
#endif
#ifndef WIFI_SSID_2
#define WIFI_SSID_2 "" // [setup]
#endif
#ifndef WIFI_PASS_2
#define WIFI_PASS_2 "" // [setup]
#endif
#define WIFI_TIMEOUT_MS 10000
#define NTP_SERVER "pool.ntp.org"
#define NTP_GMT_OFFSET 25200 // UTC+7
#define NTP_DAYLIGHT_OFFSET 0

// =============================================================================
//  Audio Recording (SD WAV)
// =============================================================================

#define SAMPLE_RATE 16000
#define BITS_PER_SAMPLE 16
#define BUFFER_CNT 8
#define BUFFER_LEN 512
#define RECORD_TIME 60 // [setup] seconds per WAV file
#define AUDIO_POOL_SIZE 32 // ~1s of audio buffered while SD is busy (8 = only 256ms)

// =============================================================================
//  SD Card
// =============================================================================

#define SD_MIN_FREE_MB 50            // [setup]
#define CSV_INTERVAL_MS 1000         // [setup] CSV row interval (ms)
#define SD_ERROR_THRESHOLD 10        // consecutive SD errors → stop recording
#define SD_REMOUNT_INTERVAL_MS 10000 // retry SD mount every 10s when missing / unhealthy

// =============================================================================
//  Data Quality  (CSV "Flags" column, see README 3.5)
//  A value outside its range is still written, but flagged *_OOR.
// =============================================================================

#define SPL_MIN_DB 30.0f        // INMP441 noise floor is ~33 dB SPL
#define SPL_MAX_DB 120.0f       // INMP441 acoustic overload point
#define TEMP_MIN_C -40.0f       // DHT22 operating range
#define TEMP_MAX_C 80.0f
#define HUMID_MIN_PCT 0.0f
#define HUMID_MAX_PCT 100.0f
#define LUX_RAW_MAX 54612.0f    // BH1750 saturates here (65535 / 1.2)
#define GPS_MAX_HDOP 5.0f       // above this -> GPS_POOR
#define SPL_STALE_MS 3000       // no new SPL value for this long -> SPL_ERR
#define TIME_MIN_YEAR 2025      // RTC year below this -> TIME_INVALID

// =============================================================================
//  Watchdog
// =============================================================================

#define WDT_TIMEOUT_S 10 // panic + reboot if any registered task hangs

// =============================================================================
//  I2S Error Recovery
// =============================================================================

#define I2S_ERROR_THRESHOLD 50 // consecutive read errors → re-init driver

// =============================================================================
//  GPS
// =============================================================================

#define GPS_VALID_TIMEOUT_MS 5000
#define GPS_PROBE_MS 3000          // boot check: wait this long for NMEA

// =============================================================================
//  Startup
// =============================================================================

#define SENSOR_WARMUP_SEC 3

// =============================================================================
//  FreeRTOS Task Stack Sizes
// =============================================================================

#define STACK_MIC 20000
#define STACK_SD_PROCESS 20000
#define STACK_GPS 4096
#define STACK_LUX 4096
#define STACK_DHT 4096
#define STACK_OLED 8192

// =============================================================================
//  OLED
// =============================================================================

#define OLED_REFRESH_MS 500

// =============================================================================
//  Setup Mode (WiFi AP + web page, see config_portal.h)
//  Reset the board, then hold BOOT (GPIO0) while the OLED shows
//  "Hold BOOT button". Don't hold BOOT *while* pressing reset — that enters
//  the ROM download mode instead.
// =============================================================================

#define CONFIG_BUTTON_PIN 0               // BOOT button on most dev boards (active LOW)
#define CONFIG_BUTTON_WINDOW_MS 3000      // how long the boot prompt is shown
#define CONFIG_BUTTON_HOLD_MS 1500        // hold time needed to enter Setup Mode
#define CONFIG_AP_SSID "IoT-Walking-Setup"
#define CONFIG_AP_PASS "walking123"       // >= 8 chars, or "" for an open AP
#define CONFIG_PORTAL_TIMEOUT_MS 300000   // leave Setup Mode after 5 min idle

#endif // CONFIG_H
