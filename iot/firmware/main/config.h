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
//  WiFi / NTP / Network  (issue #5, spec 10 §4)
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

// 1 = keep WiFi on after boot: netTask reconnects with backoff and re-syncs
//     NTP -> RTC on every reconnect (+ hourly while connected).
// 0 = old behaviour: WiFi only at boot for NTP, then off (saves RAM/power).
#define NET_ENABLED 1
#define NET_BACKOFF_MIN_MS 1000   // first retry after a drop
#define NET_BACKOFF_MAX_MS 60000  // backoff doubles up to this
#define NET_LOOP_MS 250           // netTask poll period

// Server endpoints (used by #6 realtime / #7 CSV upload / #20 WAV upload).
// [setup] defaults; empty = that feature stays off. Put real URLs in
// secrets.h (git-ignored) or the Setup page, not here: config.h is committed.
#ifndef INGEST_URL
#define INGEST_URL ""   // [setup] https://.../ingest
#endif
#ifndef PRESIGN_URL
#define PRESIGN_URL ""  // [setup] https://.../presign
#endif
#ifndef WSS_URL
#define WSS_URL ""      // [setup] ws://<eip>:8081/device (plain ws://, D22)
#endif

// Device token for the server: secrets.h only (never in NVS / Setup page)
#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN ""
#endif

// =============================================================================
//  CSV Upload to ingest  (issue #7, spec 10 §6, spec 20) — see csv_uploader.h
//  Optional INGEST_ROOT_CA (PEM) in secrets.h: verify the server certificate.
//  Without it TLS is encrypted but the server is not verified (setInsecure).
// =============================================================================

#define UPLOAD_CHUNK_ROWS      300     // rows per POST (spike #4: ~36 KB OK)
#define UPLOAD_MIN_CHUNK_ROWS  25      // 413 halves the chunk down to this
#define UPLOAD_CHUNK_MAX_BYTES 40960   // body cap incl. header (ingest max 1 MB)
#define HTTP_TIMEOUT_MS        15000   // connect / TLS handshake / response
#define UPLOAD_BACKOFF_MIN_MS  5000    // 429 / 5xx / timeout: doubles up to NET_BACKOFF_MAX_MS
#define UPLOAD_CHUNK_GAP_MS    10000   // pause after each 200 so realtime (#6) can reconnect
#define UPLOAD_PARTIAL_MS      300000  // file being written: send < full chunk after this long
#define UPLOAD_POLL_MS         5000    // wait when WiFi / SD is not ready
#define UPLOAD_ACTIVE_POLL_MS  30000   // wait for new rows when everything is sent

// =============================================================================
//  Realtime to the hub  (issue #6, spec 10 §5, D22) — see rt_client.h
//  Plain ws:// (no TLS) to the hub on the relay EC2: settings.wssUrl.
// =============================================================================

#define RT_INTERVAL_MS        2000    // one "reading" message every ...
#define RT_BACKOFF_MIN_MS     2000    // reconnect: doubles up to NET_BACKOFF_MAX_MS
#define RT_CONNECT_TIMEOUT_MS 5000    // TCP connect + upgrade reply
#define RT_IDLE_TIMEOUT_MS    60000   // nothing from the hub (it pings every 20 s) -> reconnect

// Sent in "hello" (spec 01 §3). Override in secrets.h if you like.
#ifndef FW_VERSION
#define FW_VERSION "sp2 " __DATE__
#endif

// =============================================================================
//  Audio Recording (SD WAV)
// =============================================================================

#define SAMPLE_RATE 16000
#define BITS_PER_SAMPLE 16
#define BUFFER_CNT 4 // I2S DMA buffers (spike #4: 8 -> 4 frees ~8 KB for TLS, no drops with pool 32)
#define BUFFER_LEN 512
#define RECORD_TIME 60 // [setup] seconds per WAV file
#define AUDIO_POOL_SIZE 32 // ~1s of audio buffered while SD is busy (spike #4: keep >= 32, SD stalls ~470 ms)

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

// Spike #4: with mic/SD stacks of 20000 the largest free block was ~43 KB and
// the TLS handshake failed. Check the [STACK] log (bytes left) on the board.
#define STACK_MIC 4096          // spike measured ~1 KB used
#define STACK_SD_PROCESS 6144   // board: ~3.2 KB used ([STACK] 5004 left of 8192)
#define STACK_GPS 3072   // board: ~1.6 KB used
#define STACK_LUX 4096   // board: ~2.3 KB used (keep margin)
#define STACK_DHT 3072   // board: ~1.5 KB used
#define STACK_OLED 4096 // board: ~2.2 KB used
#define STACK_NET 4096   // board: ~1.7 KB used
#define STACK_UPLOAD 8192 // TLS handshake runs on this stack (spike #4: ~5.3 KB used)
#define STACK_RT 4096     // rtTask: buffers are static; check [STACK] rtTask on the board

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
