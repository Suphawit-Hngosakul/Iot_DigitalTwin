#include "oled_display.h"
#include "globals.h"
#include "rtc_helper.h"
#include "settings.h"

#include <U8g2lib.h>
#include <esp_task_wdt.h>


static U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(U8G2_R2, U8X8_PIN_NONE);


// =============================================================================
//  Setup
// =============================================================================

void initOLED() {
  u8g2.begin();
  u8g2.setFlipMode(1);
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_6x10_tf);
  u8g2.drawStr(14, 24, "SPL+GPS Logger");
  u8g2.setFont(u8g2_font_5x7_tf);
  u8g2.drawStr(30, 40, "Initializing...");
  u8g2.sendBuffer();
  LOG("INFO", "OLED SH1106 initialized.");
}

void oledShowStatus(const char *step, const char *detail, const char *extra) {
  u8g2.clearBuffer();

  u8g2.setFont(u8g2_font_5x7_tf);
  u8g2.drawStr(0, 8, "[ SETUP ]");
  u8g2.drawHLine(0, 10, 128);

  u8g2.setFont(u8g2_font_6x10_tf);
  u8g2.drawStr(0, 26, step);

  u8g2.setFont(u8g2_font_5x7_tf);
  if (detail) u8g2.drawStr(0, 40, detail);
  if (extra)  u8g2.drawStr(0, 54, extra);

  u8g2.sendBuffer();
}


// =============================================================================
//  oledTask  (Core 0) — refresh every OLED_REFRESH_MS
//
//    Row 1: Date/Time
//    Row 2: SPL + recording status / SD free
//    Row 3: Lux
//    Row 4: Temp + Humidity
//    Row 5: GPS status (FIX / Searching / NoData) + satellites
//    Row 6: GPS coordinates (or hint when there is no fix)
// =============================================================================

void oledTask(void *parameter) {
  esp_task_wdt_add(NULL);

  char timeBuf[24];
  char splBuf[28];
  char luxBuf[28];
  char dhtBuf[28];
  char gpsBuf[36];    // GPS status line
  char gpsBuf2[36];   // GPS coordinates / hint line

  // Kept across refreshes: if a mutex read times out, show the previous
  // snapshot instead of a default-constructed (invalid) one.
  GPSData g; LuxData l; DHTData d;

  while (1) {
    esp_task_wdt_reset();

    readGPSSafe(g); readLuxSafe(l); readDHTSafe(d);

    getDisplayTimeStr(timeBuf, sizeof(timeBuf));

    char splVal[10];
    if (splValid) snprintf(splVal, sizeof(splVal), "%.1f", currentSPL);
    else          snprintf(splVal, sizeof(splVal), "--.-");

    if (!sdAvailable)
      snprintf(splBuf, sizeof(splBuf), "%sdB NO SD CARD!", splVal);
    else if (isRecording)
      snprintf(splBuf, sizeof(splBuf), "%sdB REC(%lu/%lus)",
               splVal, (unsigned long)recordElapsedSec,
               (unsigned long)settings.recordTimeSec);
    else
      snprintf(splBuf, sizeof(splBuf), "%sdB IDLE %luMB",
               splVal, (unsigned long)sdFreeMB);

    if (l.valid)
      snprintf(luxBuf, sizeof(luxBuf), "Lux:%.1f", l.value);
    else
      snprintf(luxBuf, sizeof(luxBuf), "Lux:---");

    if (d.valid)
      snprintf(dhtBuf, sizeof(dhtBuf), "T:%.1f%cC  H:%.1f%%", d.temp, 0xB0, d.humidity);
    else
      snprintf(dhtBuf, sizeof(dhtBuf), "T:--.-  H:--.-%%");

    // Three states so the screen shows what the GPS is actually doing:
    //   FIX       -> position valid, show coordinates
    //   Searching -> module talking (NMEA OK) but no position yet
    //   NoData    -> nothing parsed from UART2 (wiring / baud)
    if (g.valid) {
      snprintf(gpsBuf,  sizeof(gpsBuf),  "GPS:FIX S:%u HDOP:%.1f",
               (unsigned)g.sats, g.hdop);
      snprintf(gpsBuf2, sizeof(gpsBuf2), "%.6f,%.6f", g.lat, g.lng);
    } else if (gpsSentencesOk > 0) {
      snprintf(gpsBuf,  sizeof(gpsBuf),  "GPS:Searching S:%u", (unsigned)g.sats);
      snprintf(gpsBuf2, sizeof(gpsBuf2), "Waiting for fix...");
    } else {
      snprintf(gpsBuf,  sizeof(gpsBuf),  "GPS:NoData");
      snprintf(gpsBuf2, sizeof(gpsBuf2), "Check TX->GPIO16 9600bd");
    }

    // ── Draw ───────────────────────────────────────────────────────────
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_5x7_tf);
    u8g2.drawStr(0, 7,  timeBuf);   u8g2.drawHLine(0, 9,  128);
    u8g2.drawStr(0, 19, splBuf);    u8g2.drawHLine(0, 21, 128);
    u8g2.drawStr(0, 31, luxBuf);    u8g2.drawHLine(0, 33, 128);
    u8g2.drawStr(0, 43, dhtBuf);    u8g2.drawHLine(0, 45, 128);
    u8g2.drawStr(0, 54, gpsBuf);
    u8g2.drawStr(0, 63, gpsBuf2);

    {
      MutexLock lock(wireMutex, 200);
      if (lock) u8g2.sendBuffer();
    }

    vTaskDelay(OLED_REFRESH_MS / portTICK_PERIOD_MS);
  }
}
