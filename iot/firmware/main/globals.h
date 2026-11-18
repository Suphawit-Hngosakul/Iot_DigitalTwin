#ifndef GLOBALS_H
#define GLOBALS_H

#include "config.h"
#include <Arduino.h>


// =============================================================================
//  Audio Block (SD pipeline pool)
// =============================================================================

typedef struct {
  int16_t samples[BUFFER_LEN];
  size_t  byteCount;
} AudioBlock;


// =============================================================================
//  Sensor Snapshots (filled by readXxxSafe)
// =============================================================================

struct GPSData {
  double  lat = 0, lng = 0, alt = 0, speed = 0;
  uint8_t sats  = 0;
  float   hdop  = 99.9f;
  bool    valid = false;
};

struct LuxData {
  float value = 0.0f;
  bool  valid = false;
};

struct DHTData {
  float temp     = 0.0f;
  float humidity = 0.0f;
  bool  valid    = false;
};


// =============================================================================
//  WDT-friendly Delay
//  Splits a long delay into 1-second chunks and feeds the task watchdog
//  between chunks. Caller must already be registered with esp_task_wdt_add().
// =============================================================================

void delayFeedWdt(uint32_t totalMs);


// =============================================================================
//  Mutex RAII Lock — releases automatically at end of scope
//    { MutexLock l(m, 100); if (l) { ... } }   // 100 ms timeout
//    { MutexLock l(m);             ...     }   // wait forever
// =============================================================================

class MutexLock {
public:
  MutexLock(SemaphoreHandle_t m, uint32_t timeoutMs)
    : m_(m), held_(xSemaphoreTake(m, timeoutMs / portTICK_PERIOD_MS) == pdTRUE) {}

  explicit MutexLock(SemaphoreHandle_t m)
    : m_(m), held_(xSemaphoreTake(m, portMAX_DELAY) == pdTRUE) {}

  ~MutexLock() { if (held_) xSemaphoreGive(m_); }

  explicit operator bool() const { return held_; }

  MutexLock(const MutexLock &) = delete;
  MutexLock &operator=(const MutexLock &) = delete;

private:
  SemaphoreHandle_t m_;
  bool              held_;
};


// =============================================================================
//  Mutexes  (created first thing in setup())
// =============================================================================

extern SemaphoreHandle_t luxMutex;
extern SemaphoreHandle_t dhtMutex;
extern SemaphoreHandle_t gpsMutex;
extern SemaphoreHandle_t sdMutex;
extern SemaphoreHandle_t wireMutex;   // I2C bus: RTC + OLED + BH1750



// =============================================================================
//  Sensor State  (written by each sensor task under its own mutex)
// =============================================================================

extern float   luxValue;
extern bool    luxValid;

extern float   dhtTemp, dhtHumidity;
extern bool    dhtValid;

extern double  gpsLat, gpsLng, gpsAlt, gpsSpeed;
extern uint8_t gpsSats;
extern float   gpsHdop;
extern bool    gpsValid;
extern volatile uint32_t gpsSentencesOk;   // NMEA sentences with good checksum (gpsTask)


// =============================================================================
//  Recorder Status  (single writer: sdProcessTask; 32-bit values read atomically)
// =============================================================================

extern volatile float    currentSPL;    // dB SPL, flat (Z, unweighted)
extern volatile float    currentSPLA;   // dB(A), A-weighted
extern volatile bool     splValid;      // false until first value / mic stalled
extern volatile bool     isRecording;
extern volatile bool     sdAvailable;   // false: no card / SD failing (OLED shows NO SD CARD)
extern volatile uint32_t recordElapsedSec;
extern volatile uint32_t sdFreeMB;
extern volatile uint32_t audioDropCount;

extern bool rtcAvailable;


// =============================================================================
//  Network Status  (single writer: netTask; read by loop() health line)
// =============================================================================

extern volatile bool     netConnected;
extern volatile int8_t   netRssi;        // dBm, 0 while disconnected
extern volatile uint32_t netReconnects;  // successful reconnects since boot


// =============================================================================
//  CSV Upload Status  (single writer: uploadTask, issue #7)
// =============================================================================

extern volatile uint32_t uploadBacklogRows;   // rows on SD not sent yet (estimate)
extern volatile uint32_t uploadChunksOk;      // chunks answered 200 since boot
extern volatile uint32_t uploadRowsSkipped;   // rows given up on (see /upload_skipped.csv)
extern volatile int16_t  uploadLastCode;      // last HTTP status (<0 = HTTPClient error)
extern volatile bool     uploadStopped;       // 401 / bad URL: stopped until reboot


// =============================================================================
//  Realtime Status  (single writer: rtTask, issue #6)
// =============================================================================

extern volatile bool     rtConnected;    // WebSocket to the hub is open
extern volatile uint32_t rtSent;         // readings sent since boot
extern volatile uint32_t rtReconnects;   // reconnects after the first connect
extern volatile bool     rtStopped;      // 401 / wss:// / bad URL: stopped until reboot


// =============================================================================
//  Audio Pool
//  micTask -> readyQueue -> sdProcessTask -> freeQueue
// =============================================================================

extern AudioBlock    audioPool[AUDIO_POOL_SIZE];
extern QueueHandle_t freeQueue;
extern QueueHandle_t readyQueue;


// =============================================================================
//  Thread-safe Sensor Reads
//  Returns false on mutex timeout; `out` then keeps its default (zero/invalid).
// =============================================================================

bool readGPSSafe(GPSData &out);
bool readLuxSafe(LuxData &out);
bool readDHTSafe(DHTData &out);

#endif // GLOBALS_H
