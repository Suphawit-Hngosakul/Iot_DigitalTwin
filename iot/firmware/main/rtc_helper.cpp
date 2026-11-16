#include "rtc_helper.h"
#include "globals.h"

#include <RTClib.h>


static RTC_DS3231 rtc;


// Last good RTC reading + millis() at that moment. Used when the I2C bus is
// busy or a read fails, so CSV rows / file names keep a sensible time instead
// of falling back to 2000-01-01 00:00:00 (which also made file names repeat).
static portMUX_TYPE  lastGoodMux  = portMUX_INITIALIZER_UNLOCKED;
static DateTime      lastGood;
static unsigned long lastGoodMs   = 0;
static bool          haveLastGood = false;

// false while the RTC holds the compile time after losing power
static volatile bool timeTrusted  = false;

static void rememberGood(const DateTime &dt) {
  unsigned long ms = millis();
  portENTER_CRITICAL(&lastGoodMux);
  lastGood = dt;  lastGoodMs = ms;  haveLastGood = true;
  portEXIT_CRITICAL(&lastGoodMux);
}

// Read RTC under wireMutex. On mutex timeout or an invalid reading, estimate
// from the last good reading. Returns false (out untouched) only if there is
// no good reading yet.
static bool rtcNowSafe(DateTime &out) {
  {
    MutexLock lock(wireMutex, 100);
    if (lock) {
      DateTime dt = rtc.now();
      if (dt.isValid() && dt.year() >= 2020) {
        out = dt;
        rememberGood(dt);
        return true;
      }
    }
  }

  portENTER_CRITICAL(&lastGoodMux);
  bool          have = haveLastGood;
  DateTime      base = lastGood;
  unsigned long ms   = lastGoodMs;
  portEXIT_CRITICAL(&lastGoodMux);
  if (!have) return false;

  out = base + TimeSpan((int32_t)((millis() - ms) / 1000));
  return true;
}


// =============================================================================
//  Setup
// =============================================================================

bool initRTC() {
  if (!rtc.begin()) {
    LOG("WARN", "DS3231 RTC not found! Using millis()");
    return false;
  }
  if (rtc.lostPower()) {
    LOG("WARN", "RTC lost power, setting build time (Time_Valid=0 until NTP/phone/GPS sets it)");
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
    timeTrusted = false;
  } else {
    timeTrusted = true;
  }
  DateTime ts = rtc.now();
  rememberGood(ts);
  LOG("INFO", "RTC: %04d-%02d-%02d %02d:%02d:%02d",
      ts.year(), ts.month(), ts.day(), ts.hour(), ts.minute(), ts.second());
  return true;
}

void rtcSetTime(const struct tm &t) {
  DateTime dt(t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
              t.tm_hour, t.tm_min, t.tm_sec);
  MutexLock lock(wireMutex, 100);
  if (lock) {
    rtc.adjust(dt);
    rememberGood(dt);
    timeTrusted = true;
  }
}

void rtcSetFromUtc(uint16_t y, uint8_t mo, uint8_t d, uint8_t h, uint8_t mi, uint8_t s) {
  DateTime local = DateTime(y, mo, d, h, mi, s) + TimeSpan((int32_t)NTP_GMT_OFFSET);
  struct tm t = {};
  t.tm_year = local.year() - 1900;
  t.tm_mon  = local.month() - 1;
  t.tm_mday = local.day();
  t.tm_hour = local.hour();
  t.tm_min  = local.minute();
  t.tm_sec  = local.second();
  rtcSetTime(t);
}

bool rtcTimeTrusted() { return rtcAvailable && timeTrusted; }


// =============================================================================
//  Time Strings
// =============================================================================

bool getDateTimeStrChecked(char *buf, size_t len) {
  if (!rtcAvailable) {
    snprintf(buf, len, "%lu", millis() / 1000);
    return false;
  }
  DateTime ts(2000, 1, 1, 0, 0, 0);
  bool ok = rtcNowSafe(ts);
  snprintf(buf, len, "%04d-%02d-%02d %02d:%02d:%02d",
           ts.year(), ts.month(), ts.day(), ts.hour(), ts.minute(), ts.second());
  return ok && timeTrusted && ts.year() >= TIME_MIN_YEAR;
}

void getDateTimeStr(char *buf, size_t len) {
  getDateTimeStrChecked(buf, len);
}

void getTimestampStr(char *buf, size_t len) {
  if (!rtcAvailable) {
    snprintf(buf, len, "%lu", millis());
    return;
  }
  DateTime ts(2000, 1, 1, 0, 0, 0);
  rtcNowSafe(ts);
  snprintf(buf, len, "%04d%02d%02d_%02d%02d%02d",
           ts.year(), ts.month(), ts.day(), ts.hour(), ts.minute(), ts.second());
}

void getDateFolderStr(char *buf, size_t len) {
  if (!rtcAvailable) {
    snprintf(buf, len, "/logs");
    return;
  }
  DateTime ts(2000, 1, 1, 0, 0, 0);
  rtcNowSafe(ts);
  snprintf(buf, len, "/%04d%02d%02d", ts.year(), ts.month(), ts.day());
}

void getDisplayTimeStr(char *buf, size_t len) {
  if (!rtcAvailable) {
    snprintf(buf, len, "uptime: %lus", millis() / 1000);
    return;
  }
  DateTime ts;
  if (!rtcNowSafe(ts)) {
    snprintf(buf, len, "--/--/---- --:--:--");
    return;
  }
  snprintf(buf, len, "%02d/%02d/%04d %02d:%02d:%02d",
           ts.day(), ts.month(), ts.year(), ts.hour(), ts.minute(), ts.second());
}
