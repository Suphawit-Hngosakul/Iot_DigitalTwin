#include "settings.h"
#include "config.h"
#include "rtc_helper.h"

#include <Preferences.h>
#include <esp_mac.h>


RuntimeSettings settings;

static const char *NVS_NAMESPACE = "iotwalk";

// NVS keys (max 15 chars each)
static const char *K_SSID1   = "ssid1";
static const char *K_PASS1   = "pass1";
static const char *K_SSID2   = "ssid2";
static const char *K_PASS2   = "pass2";
static const char *K_MIC     = "micOff";
static const char *K_TEMP    = "dhtTOff";
static const char *K_HUMID   = "dhtHOff";
static const char *K_LUX_S   = "luxSlope";
static const char *K_LUX_O   = "luxOff";
static const char *K_REC     = "recSec";
static const char *K_CSV     = "csvMs";
static const char *K_SDMIN   = "sdMinMb";
static const char *K_DEVID   = "devId";
static const char *K_DEVTYPE = "devType";

// Kept apart from NVS_NAMESPACE so settingsReset() does not clear it.
static const char *NVS_SYS_NAMESPACE = "iotwalk_sys";
static const char *K_SESSION         = "session";


static void copyStr(char *dst, size_t len, const char *src) {
  strncpy(dst, src ? src : "", len - 1);
  dst[len - 1] = '\0';
}


bool deviceIdValid(const char *id) {
  size_t n = strlen(id);
  if (n == 0 || n >= SETTINGS_DEVID_LEN) return false;
  for (size_t i = 0; i < n; i++) {
    char c = id[i];
    if (!isalnum((unsigned char)c) && c != '-' && c != '_') return false;
  }
  return true;
}

const char *deviceTypeName(uint8_t type) {
  return type == DEVICE_STATIONARY ? "Stationary" : "Mobile";
}

// DEVICE_ID from config.h, or "IOTW-" + last 3 bytes of the WiFi MAC.
static void defaultDeviceId(char *out, size_t len) {
  if (deviceIdValid(DEVICE_ID)) {
    copyStr(out, len, DEVICE_ID);
    return;
  }
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  snprintf(out, len, "IOTW-%02X%02X%02X", mac[3], mac[4], mac[5]);
}

void settingsDefaults(RuntimeSettings &out) {
  defaultDeviceId(out.deviceId, sizeof(out.deviceId));
  out.deviceType = (DEVICE_TYPE == DEVICE_STATIONARY) ? DEVICE_STATIONARY : DEVICE_MOBILE;

  copyStr(out.wifiSsid1, sizeof(out.wifiSsid1), WIFI_SSID_1);
  copyStr(out.wifiPass1, sizeof(out.wifiPass1), WIFI_PASS_1);
  copyStr(out.wifiSsid2, sizeof(out.wifiSsid2), WIFI_SSID_2);
  copyStr(out.wifiPass2, sizeof(out.wifiPass2), WIFI_PASS_2);

  out.micOffsetDb    = MIC_OFFSET_DB;
  out.dhtTempOffset  = DHT_TEMP_OFFSET;
  out.dhtHumidOffset = DHT_HUMID_OFFSET;
  out.luxCalSlope    = LUX_CAL_SLOPE;
  out.luxCalOffset   = LUX_CAL_OFFSET;

  out.recordTimeSec  = RECORD_TIME;
  out.csvIntervalMs  = CSV_INTERVAL_MS;
  out.sdMinFreeMb    = SD_MIN_FREE_MB;
}


static void loadStr(Preferences &p, const char *key, char *dst, size_t len) {
  if (!p.isKey(key)) return;            // keep default
  String v = p.getString(key, "");
  copyStr(dst, len, v.c_str());
}

// Out of range (or NaN) -> back to the config.h default. Ranges match the
// checks in config_portal.cpp handleSave().
template <typename T>
static void keepInRange(T &v, T lo, T hi, T def, const char *key) {
  if (v >= lo && v <= hi) return;   // false for NaN too
  LOG("WARN", "NVS '%s' out of range - using default", key);
  v = def;
}

void settingsLoad() {
  settingsDefaults(settings);

  Preferences p;
  if (!p.begin(NVS_NAMESPACE, true /* read-only */)) {
    // Namespace does not exist yet (never saved) — defaults only.
    LOG("CFG", "No saved settings - using config.h defaults");
    return;
  }

  loadStr(p, K_DEVID, settings.deviceId, sizeof(settings.deviceId));
  settings.deviceType = p.getUChar(K_DEVTYPE, settings.deviceType);

  loadStr(p, K_SSID1, settings.wifiSsid1, sizeof(settings.wifiSsid1));
  loadStr(p, K_PASS1, settings.wifiPass1, sizeof(settings.wifiPass1));
  loadStr(p, K_SSID2, settings.wifiSsid2, sizeof(settings.wifiSsid2));
  loadStr(p, K_PASS2, settings.wifiPass2, sizeof(settings.wifiPass2));

  settings.micOffsetDb    = p.getFloat(K_MIC,   settings.micOffsetDb);
  settings.dhtTempOffset  = p.getFloat(K_TEMP,  settings.dhtTempOffset);
  settings.dhtHumidOffset = p.getFloat(K_HUMID, settings.dhtHumidOffset);
  settings.luxCalSlope    = p.getFloat(K_LUX_S, settings.luxCalSlope);
  settings.luxCalOffset   = p.getFloat(K_LUX_O, settings.luxCalOffset);

  settings.recordTimeSec  = p.getUInt(K_REC,   settings.recordTimeSec);
  settings.csvIntervalMs  = p.getUInt(K_CSV,   settings.csvIntervalMs);
  settings.sdMinFreeMb    = p.getUInt(K_SDMIN, settings.sdMinFreeMb);

  p.end();

  // NVS values are trusted only within the same ranges the Setup page allows
  // (e.g. recSec = 0 would close and reopen a WAV file on every audio block).
  RuntimeSettings def;
  settingsDefaults(def);
  if (!deviceIdValid(settings.deviceId)) {
    LOG("WARN", "NVS '%s' invalid - using default", K_DEVID);
    copyStr(settings.deviceId, sizeof(settings.deviceId), def.deviceId);
  }
  keepInRange<uint8_t>(settings.deviceType, DEVICE_STATIONARY, DEVICE_MOBILE,
                       def.deviceType, K_DEVTYPE);
  keepInRange<float>(settings.micOffsetDb,    50.0f,    200.0f,   def.micOffsetDb,    K_MIC);
  keepInRange<float>(settings.dhtTempOffset,  -20.0f,   20.0f,    def.dhtTempOffset,  K_TEMP);
  keepInRange<float>(settings.dhtHumidOffset, -50.0f,   50.0f,    def.dhtHumidOffset, K_HUMID);
  keepInRange<float>(settings.luxCalSlope,    0.01f,    10.0f,    def.luxCalSlope,    K_LUX_S);
  keepInRange<float>(settings.luxCalOffset,   -1000.0f, 1000.0f,  def.luxCalOffset,   K_LUX_O);
  keepInRange<uint32_t>(settings.recordTimeSec,  5,       3600,    def.recordTimeSec,  K_REC);
  keepInRange<uint32_t>(settings.csvIntervalMs,  100,     60000,   def.csvIntervalMs,  K_CSV);
  keepInRange<uint32_t>(settings.sdMinFreeMb,    0,       100000,  def.sdMinFreeMb,    K_SDMIN);

  LOG("CFG", "Settings loaded from NVS");
}


bool settingsSave(const RuntimeSettings &in) {
  Preferences p;
  if (!p.begin(NVS_NAMESPACE, false)) {
    LOG("ERROR", "NVS open failed - settings not saved");
    return false;
  }

  bool ok = true;
  ok &= p.putString(K_DEVID, in.deviceId) == strlen(in.deviceId);
  ok &= p.putUChar(K_DEVTYPE, in.deviceType) > 0;

  ok &= p.putString(K_SSID1, in.wifiSsid1) == strlen(in.wifiSsid1);
  ok &= p.putString(K_PASS1, in.wifiPass1) == strlen(in.wifiPass1);
  ok &= p.putString(K_SSID2, in.wifiSsid2) == strlen(in.wifiSsid2);
  ok &= p.putString(K_PASS2, in.wifiPass2) == strlen(in.wifiPass2);

  ok &= p.putFloat(K_MIC,   in.micOffsetDb)    > 0;
  ok &= p.putFloat(K_TEMP,  in.dhtTempOffset)  > 0;
  ok &= p.putFloat(K_HUMID, in.dhtHumidOffset) > 0;
  ok &= p.putFloat(K_LUX_S, in.luxCalSlope)    > 0;
  ok &= p.putFloat(K_LUX_O, in.luxCalOffset)   > 0;

  ok &= p.putUInt(K_REC,   in.recordTimeSec) > 0;
  ok &= p.putUInt(K_CSV,   in.csvIntervalMs) > 0;
  ok &= p.putUInt(K_SDMIN, in.sdMinFreeMb)   > 0;

  p.end();

  settings = in;
  if (ok) LOG("CFG", "Settings saved to NVS");
  else    LOG("ERROR", "Some settings failed to save to NVS");
  return ok;
}


void settingsReset() {
  Preferences p;
  if (p.begin(NVS_NAMESPACE, false)) {
    p.clear();
    p.end();
  }
  settingsDefaults(settings);
  LOG("CFG", "Settings reset to config.h defaults");
}


void settingsPrint() {
  LOG("CFG", "DEVICE_ID='%s' TYPE=%s", settings.deviceId, deviceTypeName(settings.deviceType));
  LOG("CFG", "WiFi1='%s' WiFi2='%s'", settings.wifiSsid1, settings.wifiSsid2);
  LOG("CFG", "MIC_OFFSET=%.2fdB  DHT T%+.2f H%+.2f  LUX slope=%.4f off=%.2f",
      settings.micOffsetDb, settings.dhtTempOffset, settings.dhtHumidOffset,
      settings.luxCalSlope, settings.luxCalOffset);
  LOG("CFG", "RECORD=%lus  CSV=%lums  SD_MIN_FREE=%luMB",
      (unsigned long)settings.recordTimeSec, (unsigned long)settings.csvIntervalMs,
      (unsigned long)settings.sdMinFreeMb);
}


uint32_t sessionId() {
  static uint32_t id = 0;
  if (id) return id;

  Preferences p;
  if (p.begin(NVS_SYS_NAMESPACE, false)) {
    id = p.getUInt(K_SESSION, 0) + 1;
    if (id == 0) id = 1;                       // wrapped (never in practice)
    if (p.putUInt(K_SESSION, id) == 0)
      LOG("ERROR", "NVS: session counter not saved");
    p.end();
  } else {
    LOG("ERROR", "NVS open failed - session counter not saved");
    id = 1;
  }
  LOG("CFG", "Session %lu", (unsigned long)id);
  return id;
}
