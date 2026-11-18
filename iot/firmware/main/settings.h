#ifndef SETTINGS_H
#define SETTINGS_H

#include <Arduino.h>

// =============================================================================
//  Runtime Settings
//  Values the user can change from the setup web page (config_portal) without
//  re-flashing. Stored in NVS (Preferences, namespace "iotwalk"); any key that
//  is missing falls back to the default in config.h.
//
//  Loaded once in setup() before any task starts, then read-only — tasks read
//  `settings` directly without a mutex.
// =============================================================================

#define SETTINGS_SSID_LEN 33   // 32 chars + NUL (802.11 max)
#define SETTINGS_PASS_LEN 65   // 64 chars + NUL (WPA2 max)
#define SETTINGS_DEVID_LEN 17  // 16 chars + NUL
#define SETTINGS_URL_LEN 129   // 128 chars + NUL

enum DeviceType : uint8_t { DEVICE_STATIONARY = 0, DEVICE_MOBILE = 1 };

struct RuntimeSettings {
  // Identity (written into every CSV row)
  char    deviceId[SETTINGS_DEVID_LEN];
  uint8_t deviceType;       // DeviceType

  // WiFi for NTP + uplink (tried in order, empty SSID = skip)
  char wifiSsid1[SETTINGS_SSID_LEN];
  char wifiPass1[SETTINGS_PASS_LEN];
  char wifiSsid2[SETTINGS_SSID_LEN];
  char wifiPass2[SETTINGS_PASS_LEN];

  // Calibration
  float micOffsetDb;
  float dhtTempOffset;
  float dhtHumidOffset;
  float luxCalSlope;
  float luxCalOffset;

  // Logging
  uint32_t recordTimeSec;   // seconds per WAV file
  uint32_t csvIntervalMs;   // CSV row interval
  uint32_t sdMinFreeMb;     // stop starting new WAV files below this

  // Server endpoints (empty = feature off). Token: DEVICE_TOKEN in secrets.h
  char ingestUrl[SETTINGS_URL_LEN];    // https://  CSV upload (#7)
  char presignUrl[SETTINGS_URL_LEN];   // https://  WAV upload link (#20)
  char wssUrl[SETTINGS_URL_LEN];       // ws://     realtime hub (#6, D22: no TLS)
};

extern RuntimeSettings settings;

// Fill `out` with the compile-time defaults from config.h.
void settingsDefaults(RuntimeSettings &out);

// Load from NVS into `settings` (missing keys -> config.h defaults).
void settingsLoad();

// Write `in` to NVS and copy it into `settings`. Returns false on NVS error.
bool settingsSave(const RuntimeSettings &in);

// Erase everything stored in NVS and go back to config.h defaults.
void settingsReset();

// Print the active values to Serial.
void settingsPrint();

// true if `id` is 1..16 chars of A-Z a-z 0-9 - _
bool deviceIdValid(const char *id);

// true if `url` is empty, or starts with http:// / https:// (websocket=false)
// or ws:// / wss:// (websocket=true), fits SETTINGS_URL_LEN and has no
// spaces / quotes / control characters.
bool settingsUrlValid(const char *url, bool websocket);

// "Stationary" / "Mobile"
const char *deviceTypeName(uint8_t type);

// Session number for this boot: read, +1 and stored in its own NVS namespace,
// so "reset to defaults" never reuses an old number. Device_ID + Session +
// Record_ID is unique for every row this device ever writes.
// Call once in setup(); later calls return the same value.
uint32_t sessionId();

#endif // SETTINGS_H
