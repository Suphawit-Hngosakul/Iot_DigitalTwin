#include "wifi_ntp.h"
#include "globals.h"
#include "rtc_helper.h"
#include "settings.h"

#include <WiFi.h>


// =============================================================================
//  Try one SSID — blocks up to WIFI_TIMEOUT_MS
// =============================================================================

static bool tryConnect(const char *ssid, const char *pass) {
  if (!ssid || !*ssid) return false;   // slot not configured
  LOG("WIFI", "Trying '%s'...", ssid);

  WiFi.disconnect(false, true);
  WiFi.begin(ssid, pass);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS)
    delay(250);

  if (WiFi.status() != WL_CONNECTED) {
    LOG("WIFI", "Failed to connect '%s'.", ssid);
    return false;
  }
  LOG("WIFI", "Connected to '%s'. IP: %s", ssid, WiFi.localIP().toString().c_str());
  return true;
}

static bool waitForNTP() {
  // Multiple servers — first one that responds wins
  configTime(NTP_GMT_OFFSET, NTP_DAYLIGHT_OFFSET,
             NTP_SERVER, "time.google.com", "time.nist.gov");

  // Retry up to 3 x 5s — handles slow networks / first DNS resolve
  struct tm timeinfo;
  for (int attempt = 1; attempt <= 3; attempt++) {
    LOG("NTP", "Sync attempt %d/3...", attempt);
    if (getLocalTime(&timeinfo, 5000)) {
      LOG("INFO", "NTP synced: %04d-%02d-%02d %02d:%02d:%02d",
          timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
          timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
      rtcSetTime(timeinfo);
      LOG("INFO", "RTC updated from NTP.");
      return true;
    }
    delay(1000);
  }

  LOG("WARN", "NTP sync failed after 3 attempts - using existing RTC time.");
  return false;
}


// =============================================================================
//  Boot: WiFi + NTP  (called once from setup, before tasks start)
// =============================================================================

bool syncRTCFromNTP() {
  if (!rtcAvailable) {
    LOG("INFO", "No RTC - skipping NTP sync.");
    return false;
  }

  if (!settings.wifiSsid1[0] && !settings.wifiSsid2[0]) {
    LOG("INFO", "No WiFi configured - skipping NTP sync (use Setup Mode).");
    return false;
  }

  WiFi.mode(WIFI_STA);

  bool synced = false;
  if (tryConnect(settings.wifiSsid1, settings.wifiPass1) ||
      tryConnect(settings.wifiSsid2, settings.wifiPass2))
    synced = waitForNTP();
  else
    LOG("WARN", "No WiFi available - using existing RTC time.");

  // WiFi is only needed for NTP — turn radio off
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  return synced;
}
