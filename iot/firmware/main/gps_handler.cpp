#include "gps_handler.h"
#include "globals.h"
#include "rtc_helper.h"

#include <HardwareSerial.h>
#include <TinyGPSPlus.h>
#include <esp_task_wdt.h>


static HardwareSerial gpsSerial(2);
static TinyGPSPlus    gps;


void initGPS() {
  // Default RX buffer is 256 bytes; NEO-M8N emits ~600+ bytes/epoch (GSV for
  // GPS+GLONASS). Any stall of gpsTask overflows it and corrupts sentences.
  gpsSerial.setRxBufferSize(1024);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  LOG("INFO", "GPS UART2 started (RX=%d TX=%d @ %d baud)",
      GPS_RX_PIN, GPS_TX_PIN, GPS_BAUD);
}


// Boot-time check (call from setup(), before gpsTask starts): listen for up
// to timeoutMs and return true once a valid NMEA sentence has been parsed.
bool gpsProbe(uint32_t timeoutMs) {
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (gpsSerial.available())
      gps.encode(gpsSerial.read());
    if (gps.passedChecksum() > 0) {
      gpsSentencesOk = gps.passedChecksum();
      return true;
    }
    delay(10);
  }
  LOG("WARN", "GPS probe: no valid NMEA in %lums (chars=%lu) - check wiring/baud",
      (unsigned long)timeoutMs, (unsigned long)gps.charsProcessed());
  return false;
}


// =============================================================================
//  gpsTask  (Core 0) — parse NMEA from UART2, 50 ms cycle
// =============================================================================

void gpsTask(void *parameter) {
  esp_task_wdt_add(NULL);
  LOG("INFO", "GPS Task started");

  unsigned long lastFixMs = 0;

  while (1) {
    esp_task_wdt_reset();

    while (gpsSerial.available())
      gps.encode(gpsSerial.read());

    // Link status for the OLED: any NMEA sentence with a good checksum
    gpsSentencesOk = gps.passedChecksum();

    // Satellites/HDOP come from every GGA, even without a fix.
    // value()/hdop() are always called so the "updated" flag is cleared,
    // otherwise an invalid field would keep this block firing every cycle.
    if (gps.satellites.isUpdated() || gps.hdop.isUpdated()) {
      uint32_t sats = gps.satellites.value();
      double   hdop = gps.hdop.hdop();
      MutexLock l(gpsMutex, 10);
      if (l) {
        gpsSats = gps.satellites.isValid() ? (uint8_t)sats : 0;
        gpsHdop = gps.hdop.isValid()       ? (float)hdop   : 99.9f;
      }
    }

    if (gps.location.isUpdated()) {
      double lat = gps.location.lat();   // clears the "updated" flag
      double lng = gps.location.lng();
      if (gps.location.isValid()) {
        MutexLock l(gpsMutex, 10);
        if (l) {
          gpsValid  = true;
          lastFixMs = millis();
          gpsLat   = lat;
          gpsLng   = lng;
          gpsAlt   = gps.altitude.isValid() ? gps.altitude.meters() : 0.0;
          gpsSpeed = gps.speed.isValid()    ? gps.speed.kmph()      : 0.0;
        }
      }
    }

    // RTC lost power and no NTP/phone time yet: take UTC from GPS once a fix
    // is valid (before a fix the receiver's time can be off by leap seconds).
    if (rtcAvailable && !rtcTimeTrusted() && gpsValid &&
        gps.date.isValid() && gps.time.isValid() &&
        gps.date.year() >= TIME_MIN_YEAR && gps.time.age() < 500) {
      rtcSetFromUtc(gps.date.year(), gps.date.month(), gps.date.day(),
                    gps.time.hour(), gps.time.minute(), gps.time.second());
      if (rtcTimeTrusted()) LOG("INFO", "RTC set from GPS time");
    }

    // Diagnostics: a rising failCRC means lost UART bytes
    static unsigned long lastStatMs = 0;
    if (millis() - lastStatMs >= 30000) {
      lastStatMs = millis();
      LOG("INFO", "GPS stats: chars=%lu ok=%lu failCRC=%lu sats=%u valid=%d",
          (unsigned long)gps.charsProcessed(), (unsigned long)gps.passedChecksum(),
          (unsigned long)gps.failedChecksum(), (unsigned)gpsSats, (int)gpsValid);

      // No new bytes in 30 s = the module stopped talking (power / TX wire /
      // module reset). A soft reboot of the ESP32 does not power-cycle it.
      static uint32_t lastChars = 0;
      uint32_t chars = gps.charsProcessed();
      if (chars == lastChars)
        LOG("WARN", "GPS UART silent for 30s (chars=%lu) - check GPS power / TX->GPIO%d",
            (unsigned long)chars, GPS_RX_PIN);
      lastChars = chars;
    }

    // Fix timeout. This task is the only writer of gpsValid, so reading it
    // here without the mutex is safe.
    if (gpsValid && lastFixMs > 0 && millis() - lastFixMs >= GPS_VALID_TIMEOUT_MS) {
      {
        MutexLock l(gpsMutex, 10);
        if (l) gpsValid = false;
      }
      LOG("WARN", "GPS fix timeout (%dms) - marking invalid", GPS_VALID_TIMEOUT_MS);
    }

    vTaskDelay(50 / portTICK_PERIOD_MS);
  }
}
