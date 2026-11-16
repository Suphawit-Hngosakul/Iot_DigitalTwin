#ifndef WIFI_NTP_H
#define WIFI_NTP_H

#include <Arduino.h>

// Boot-time only: connect WiFi (settings SSID 1, then SSID 2), sync RTC from
// NTP, then turn WiFi off. Skipped when no RTC (time source is millis()).
// Returns true when the RTC was updated from NTP.
bool syncRTCFromNTP();

#endif // WIFI_NTP_H
