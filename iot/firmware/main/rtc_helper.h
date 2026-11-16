#ifndef RTC_HELPER_H
#define RTC_HELPER_H

#include <Arduino.h>
#include <time.h>

// =============================================================================
//  RTC / Time Helpers  (DS3231 on I2C, guarded by wireMutex)
// =============================================================================

#define DATETIME_BUF_SIZE   24
#define TIMESTAMP_BUF_SIZE  20
#define DATEFOLDER_BUF_SIZE 12

bool initRTC();
void rtcSetTime(const struct tm &t);

// Write into caller buffer. Fallback in brackets is used when no RTC.
void getDateTimeStr(char *buf, size_t len);     // "2025-04-17 14:30:00"  [uptime s]

// Same as getDateTimeStr(), and returns whether the time can be trusted:
// RTC present, set since its last power loss (NTP / phone / GPS) and year
// >= TIME_MIN_YEAR. false -> the row gets Time_Valid = 0 / TIME_INVALID.
bool getDateTimeStrChecked(char *buf, size_t len);

// true once the RTC holds a time from NTP, the Setup page or GPS (or kept
// it across power-off on its backup battery).
bool rtcTimeTrusted();

// Set the RTC from a GPS UTC time (converted with NTP_GMT_OFFSET).
void rtcSetFromUtc(uint16_t y, uint8_t mo, uint8_t d, uint8_t h, uint8_t mi, uint8_t s);
void getTimestampStr(char *buf, size_t len);    // "20250417_143000"      [millis]
void getDateFolderStr(char *buf, size_t len);   // "/20250417"            ["/logs"]
void getDisplayTimeStr(char *buf, size_t len);  // "17/04/2025 14:30:00"  ["uptime: Ns"]


// =============================================================================
//  LOG Macro  — zero-alloc Serial.printf with timestamp
//    LOG("INFO", "value=%d", 42);   ->   [2025-04-17 14:30:00] [INFO] value=42
// =============================================================================

#define LOG(tag, fmt, ...) do { \
    char _dt[DATETIME_BUF_SIZE]; \
    getDateTimeStr(_dt, sizeof(_dt)); \
    Serial.printf("[%s] [" tag "] " fmt "\n", _dt, ##__VA_ARGS__); \
} while (0)

#endif // RTC_HELPER_H
