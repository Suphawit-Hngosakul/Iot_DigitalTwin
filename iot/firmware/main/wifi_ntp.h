#ifndef WIFI_NTP_H
#define WIFI_NTP_H

#include <Arduino.h>

// Boot-time NTP sync: connect WiFi (settings SSID 1, then SSID 2), sync RTC
// from NTP. Skipped when no RTC (time source is millis()).
// NET_ENABLED = 1: WiFi stays on afterwards and netTask takes over.
// NET_ENABLED = 0: WiFi is turned off afterwards (old behaviour).
// Returns true when the RTC was updated from NTP.
bool syncRTCFromNTP();

// Keeps WiFi connected (issue #5, spec 10 §4). Core 0, priority below the
// mic / SD tasks, not on the task watchdog: a stuck network must never reset
// the logger. On every (re)connect: WiFi.setSleep(false) + NTP -> RTC
// (Thai time, UTC+7). Drop -> retry with backoff NET_BACKOFF_MIN_MS doubling
// up to NET_BACKOFF_MAX_MS. Never touches the SD card.
// Status for health/OLED: netConnected, netRssi, netReconnects (globals.h).
void netTask(void *pv);

#endif // WIFI_NTP_H
