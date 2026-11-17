#include "wifi_ntp.h"
#include "globals.h"
#include "rtc_helper.h"
#include "settings.h"

#include <WiFi.h>
#include <esp_heap_caps.h>
#include <esp_sntp.h>


// =============================================================================
//  NTP
// =============================================================================

static void startNTP() {
  // Multiple servers — first one that responds wins. Restarts SNTP, which
  // then also re-syncs on its own every hour while WiFi is up.
  configTime(NTP_GMT_OFFSET, NTP_DAYLIGHT_OFFSET,
             NTP_SERVER, "time.google.com", "time.nist.gov");
}

// Copy system time (already Thai local time via configTime) into the RTC.
static bool applySystemTimeToRTC() {
  if (!rtcAvailable) return false;
  struct tm t;
  if (!getLocalTime(&t, 0) || t.tm_year + 1900 < TIME_MIN_YEAR) return false;
  rtcSetTime(t);
  LOG("NTP", "RTC set from NTP: %04d-%02d-%02d %02d:%02d:%02d (UTC+7)",
      t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec);
  return true;
}


// =============================================================================
//  Boot: try one SSID — blocks up to WIFI_TIMEOUT_MS
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
  startNTP();

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

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  bool synced = false;
  if (tryConnect(settings.wifiSsid1, settings.wifiPass1) ||
      tryConnect(settings.wifiSsid2, settings.wifiPass2))
    synced = waitForNTP();
  else
    LOG("WARN", "No WiFi available - using existing RTC time.");

#if !NET_ENABLED
  // No network tasks — WiFi is only needed for NTP, turn radio off
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
#endif
  return synced;
}


// =============================================================================
//  netTask — WiFi state machine (core 0, low priority)
// =============================================================================

enum NetState : uint8_t { NET_START, NET_CONNECTING, NET_CONNECTED, NET_BACKOFF };

// Set from the SNTP callback (lwIP task) — handled in netTask, so the RTC is
// written from netTask only and never from the network stack's context.
static volatile bool ntpSyncPending = false;
static void onNtpSync(struct timeval *) { ntpSyncPending = true; }

static const char *slotSsid(int i) { return i == 0 ? settings.wifiSsid1 : settings.wifiSsid2; }
static const char *slotPass(int i) { return i == 0 ? settings.wifiPass1 : settings.wifiPass2; }

// First configured slot at index >= from, or -1.
static int nextSlot(int from) {
  for (int i = from; i < 2; i++)
    if (slotSsid(i)[0]) return i;
  return -1;
}

static void beginSlot(int slot) {
  LOG("NET", "Connecting '%s'...", slotSsid(slot));
  WiFi.disconnect(false);
  WiFi.begin(slotSsid(slot), slotPass(slot));
}

static void logHealth(const char *what) {
  LOG("NET", "%s | RSSI:%d | Reconnects:%lu | Heap:%u MaxBlk:%u | Stack free:%u",
      what, (int)netRssi, (unsigned long)netReconnects,
      ESP.getFreeHeap(), heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
      (unsigned)uxTaskGetStackHighWaterMark(NULL));
}

void netTask(void *pv) {
  sntp_set_time_sync_notification_cb(onNtpSync);

  WiFi.persistent(false);         // don't write credentials to flash on every begin()
  WiFi.setAutoReconnect(false);   // this task owns reconnect + backoff
  if (WiFi.getMode() != WIFI_STA) WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);           // modem sleep makes uploads very slow (SP1)

  NetState      state      = NET_START;
  int           slot       = 0;
  uint32_t      backoffMs  = NET_BACKOFF_MIN_MS;
  unsigned long stateAt    = millis();
  bool          everUp     = false;
  bool          noSsidLogged = false;

  // Boot sync (setup) may have left WiFi connected with NTP already done.
  if (WiFi.status() == WL_CONNECTED) {
    everUp       = true;
    netConnected = true;
    netRssi      = WiFi.RSSI();
    state        = NET_CONNECTED;
    logHealth("Up (from boot)");
  }

  for (;;) {
    unsigned long now = millis();

    switch (state) {
      case NET_START:
        slot = nextSlot(0);
        if (slot < 0) {
          if (!noSsidLogged) LOG("NET", "No WiFi configured - network off (use Setup Mode).");
          noSsidLogged = true;
          vTaskDelay(pdMS_TO_TICKS(NET_BACKOFF_MAX_MS));
          continue;
        }
        beginSlot(slot);
        stateAt = now;
        state   = NET_CONNECTING;
        break;

      case NET_CONNECTING:
        if (WiFi.status() == WL_CONNECTED) {
          if (everUp) netReconnects++;
          everUp       = true;
          netConnected = true;
          netRssi      = WiFi.RSSI();
          backoffMs    = NET_BACKOFF_MIN_MS;
          WiFi.setSleep(false);
          LOG("NET", "Connected '%s' IP %s", slotSsid(slot),
              WiFi.localIP().toString().c_str());
          logHealth("Up");
          startNTP();                 // -> onNtpSync -> RTC
          state = NET_CONNECTED;
        } else if (now - stateAt >= WIFI_TIMEOUT_MS) {
          LOG("NET", "Failed '%s'", slotSsid(slot));
          int next = nextSlot(slot + 1);
          if (next >= 0) {
            slot = next;
            beginSlot(slot);
            stateAt = now;
          } else {
            WiFi.disconnect(false);
            LOG("NET", "No WiFi - retry in %lus", (unsigned long)(backoffMs / 1000));
            stateAt = now;
            state   = NET_BACKOFF;
          }
        }
        break;

      case NET_BACKOFF:
        if (now - stateAt >= backoffMs) {
          backoffMs = (backoffMs * 2 > NET_BACKOFF_MAX_MS) ? NET_BACKOFF_MAX_MS : backoffMs * 2;
          state     = NET_START;
        }
        break;

      case NET_CONNECTED:
        if (WiFi.status() != WL_CONNECTED) {
          netConnected = false;
          netRssi      = 0;
          backoffMs    = NET_BACKOFF_MIN_MS;
          WiFi.disconnect(false);
          LOG("NET", "WiFi lost - retry in %lus", (unsigned long)(backoffMs / 1000));
          stateAt = now;
          state   = NET_BACKOFF;
        } else {
          netRssi = WiFi.RSSI();
        }
        break;
    }

    if (ntpSyncPending) {
      ntpSyncPending = false;
      if (!applySystemTimeToRTC())
        LOG("NTP", "Synced (RTC not updated: no RTC or bad time)");
    }

    vTaskDelay(pdMS_TO_TICKS(NET_LOOP_MS));
  }
}
