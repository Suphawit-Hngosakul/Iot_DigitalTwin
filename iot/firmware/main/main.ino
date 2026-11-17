#include "audio_engine.h"
#include "globals.h"
#include "gps_handler.h"
#include "oled_display.h"
#include "rtc_helper.h"
#include "sd_logger.h"
#include "sensors.h"
#include "settings.h"
#include "config_portal.h"
#include "wifi_ntp.h"
#include "csv_uploader.h"

#include <Wire.h>
#include <esp_heap_caps.h>
#include <esp_task_wdt.h>


// =============================================================================
//  Task Table — add a FreeRTOS task by appending one row
// =============================================================================

struct TaskSpec {
  TaskFunction_t fn;
  const char    *name;
  uint32_t       stack;
  UBaseType_t    priority;
  BaseType_t     core;
};

static const TaskSpec kTasks[] = {
  // fn,             name,             stack,            prio, core
  { micTask,         "micTask",        STACK_MIC,        2,    1 },
  { sdProcessTask,   "sdProcessTask",  STACK_SD_PROCESS, 1,    0 },
  { gpsTask,         "gpsTask",        STACK_GPS,        1,    0 },
  { luxTask,         "luxTask",        STACK_LUX,        1,    0 },
  { dhtTask,         "dhtTask",        STACK_DHT,        1,    0 },
  { oledTask,        "oledTask",       STACK_OLED,       1,    0 },
#if NET_ENABLED
  // Below mic/SD (prio 0): network is optional, SD is the source of truth
  { netTask,         "netTask",        STACK_NET,        0,    0 },
  // CSV -> ingest (#7): lowest priority, one TLS connection at a time
  { uploadTask,      "uploadTask",     STACK_UPLOAD,     0,    0 },
#endif
};


// =============================================================================
//  Init Helpers
// =============================================================================

// Must run first: RTC, SD and I2C helpers lock these mutexes.
static void createQueuesAndMutexes() {
  freeQueue  = xQueueCreate(AUDIO_POOL_SIZE, sizeof(AudioBlock *));
  readyQueue = xQueueCreate(AUDIO_POOL_SIZE, sizeof(AudioBlock *));

  sdMutex   = xSemaphoreCreateMutex();
  gpsMutex  = xSemaphoreCreateMutex();
  luxMutex  = xSemaphoreCreateMutex();
  dhtMutex  = xSemaphoreCreateMutex();
  wireMutex = xSemaphoreCreateMutex();

  if (!freeQueue || !readyQueue ||
      !sdMutex || !gpsMutex || !luxMutex || !dhtMutex || !wireMutex) {
    LOG("ERROR", "Failed to create Queue/Mutex! Restarting in 3s...");
    delay(3000);
    ESP.restart();
  }

  for (int i = 0; i < AUDIO_POOL_SIZE; i++) {
    AudioBlock *blk = &audioPool[i];
    xQueueSend(freeQueue, &blk, 0);
  }
}

// No card is not fatal any more: sensors keep running, the OLED shows
// NO SD CARD and sdProcessTask mounts the card when it is inserted.
static void initSD() {
  oledShowStatus("SD Card", "Initializing...");
  if (!mountSDCard()) {
    LOG("ERROR", "SD_MMC init failed - no card? Retrying every %ds", SD_REMOUNT_INTERVAL_MS / 1000);
    oledShowStatus("SD Card", "NOT FOUND!", "Insert card (no log)");
    delay(3000);
    return;
  }

  char buf[24];
  snprintf(buf, sizeof(buf), "Free: %lu MB", (unsigned long)sdFreeMB);
  LOG("INFO", "SD OK. %s", buf);
  oledShowStatus("SD Card", "Ready", buf);
  delay(700);
  createCSVLog();
}


// =============================================================================
//  Setup
// =============================================================================

void setup() {
  Serial.begin(115200);
  delay(500);
  LOG("INFO", "Sound + GPS + Lux + DHT22 Logger Starting...");

  createQueuesAndMutexes();

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);
  initOLED();

  // ── Settings (NVS, fallback config.h) ───────────────────────────────────
  settingsLoad();
  settingsPrint();
  sessionId();   // new session number for this boot (stored in NVS)
  {
    char sess[20];
    snprintf(sess, sizeof(sess), "Session %lu", (unsigned long)sessionId());
    oledShowStatus(settings.deviceId, deviceTypeName(settings.deviceType), sess);
    delay(1000);
  }

  // ── RTC ──────────────────────────────────────────────────────────────────
  oledShowStatus("Init RTC...");
  rtcAvailable = initRTC();
  oledShowStatus("RTC", rtcAvailable ? "DS3231 OK" : "No RTC (millis)");
  delay(700);

  // ── Setup Mode: hold BOOT now -> WiFi AP + web page (restarts when done)
  if (configButtonRequested())
    runConfigPortal();

  // ── NTP -> RTC (WiFi stays on for netTask when NET_ENABLED) ──────────────
  if (rtcAvailable) {
    oledShowStatus("NTP Sync", "WiFi connecting...",
                   settings.wifiSsid1[0] ? settings.wifiSsid1 : settings.wifiSsid2);
    oledShowStatus("NTP Sync", syncRTCFromNTP() ? "RTC updated" : "Skipped (RTC kept)");
    delay(700);
  }

  // ── Sensors ──────────────────────────────────────────────────────────────
  oledShowStatus("Sensors", "BH1750 / DHT22");
  initBH1750();
  initDHT();

  for (int i = SENSOR_WARMUP_SEC; i > 0; i--) {
    char buf[20];
    snprintf(buf, sizeof(buf), "Warmup: %ds...", i);
    oledShowStatus("Sensor Warmup", "Stabilizing...", buf);
    delay(1000);
  }

  // ── GPS ──────────────────────────────────────────────────────────────────
  oledShowStatus("GPS", "UART2 starting...");
  initGPS();
  if (gpsProbe(GPS_PROBE_MS))
    oledShowStatus("GPS", "Module OK (NMEA)", "Searching sats...");
  else
    oledShowStatus("GPS", "No data from GPS!", "Check TX->GPIO16");
  delay(1000);

  // ── SD Card + CSV ────────────────────────────────────────────────────────
  initSD();

  // ── Watchdog: panic + reboot if any registered task hangs ───────────────
  esp_task_wdt_config_t wdtCfg = {
    .timeout_ms     = WDT_TIMEOUT_S * 1000,
    .idle_core_mask = 0,
    .trigger_panic  = true,
  };
  esp_task_wdt_reconfigure(&wdtCfg);

  // ── Launch Tasks ─────────────────────────────────────────────────────────
  oledShowStatus("Ready!", "Starting tasks...");
  delay(800);

  for (const TaskSpec &t : kTasks)
    xTaskCreatePinnedToCore(t.fn, t.name, t.stack, NULL, t.priority, NULL, t.core);

  LOG("INFO", "All tasks started. OFFSET=%.1fdB | RMS_WIN=%d | EMA=%.2f",
      settings.micOffsetDb, RMS_WINDOW, EMA_ALPHA);
}


// =============================================================================
//  Loop — status print every 10 s (Core 1)
// =============================================================================

void loop() {
  GPSData g; LuxData l; DHTData d;
  readGPSSafe(g); readLuxSafe(l); readDHTSafe(d);

  LOG("STAT", "%s | Heap:%u MaxBlk:%u | SDFree:%luMB | SPL:%.1fdB (A %.1fdBA) | "
      "Lux:%.1flux | Temp:%.1fC | Humi:%.1f%% | "
      "GPS:%s(%.6f,%.6f) S:%d HDOP:%.1f | SD:%s | Rec:%s | "
      "WiFi:%s RSSI:%d Reconn:%lu | Up:%s Backlog:%lu OK:%lu Skip:%lu Code:%d",
      settings.deviceId, ESP.getFreeHeap(),
      (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
      (unsigned long)sdFreeMB, currentSPL, currentSPLA,
      l.valid ? l.value : -1.0f,
      d.valid ? d.temp : -99.0f, d.valid ? d.humidity : -1.0f,
      g.valid ? "FIX " : "NoFx", g.lat, g.lng, g.sats, g.hdop,
      sdAvailable ? "OK" : "NONE",
      isRecording ? "YES" : "No",
      netConnected ? "UP" : "DOWN", (int)netRssi, (unsigned long)netReconnects,
      uploadStopped ? "STOP" : "on", (unsigned long)uploadBacklogRows,
      (unsigned long)uploadChunksOk, (unsigned long)uploadRowsSkipped, (int)uploadLastCode);

  // Stack left per task (bytes, lowest so far) every 60 s: tune STACK_* in
  // config.h with it. Looked up by name: micTask deletes itself on I2S failure.
  static uint8_t stackTick = 0;
  if (++stackTick >= 6) {
    stackTick = 0;
    char buf[200] = "";
    size_t n = 0;
    for (const TaskSpec &t : kTasks) {
      TaskHandle_t h = xTaskGetHandle(t.name);
      if (h && n < sizeof(buf))
        n += snprintf(buf + n, sizeof(buf) - n, "%s:%u ", t.name,
                      (unsigned)uxTaskGetStackHighWaterMark(h));
    }
    LOG("STACK", "%s", buf);
  }

  delay(10000);
}
