#include "globals.h"

#include <esp_task_wdt.h>


// =============================================================================
//  Mutexes
// =============================================================================

SemaphoreHandle_t luxMutex;
SemaphoreHandle_t dhtMutex;
SemaphoreHandle_t gpsMutex;
SemaphoreHandle_t sdMutex;
SemaphoreHandle_t wireMutex;


// =============================================================================
//  Sensor State
// =============================================================================

float luxValue = 0.0f;
bool  luxValid = false;

float dhtTemp = 0.0f, dhtHumidity = 0.0f;
bool  dhtValid = false;

double  gpsLat = 0.0, gpsLng = 0.0, gpsAlt = 0.0, gpsSpeed = 0.0;
uint8_t gpsSats = 0;
float   gpsHdop = 99.9f;
bool    gpsValid = false;
volatile uint32_t gpsSentencesOk = 0;


// =============================================================================
//  Recorder Status
// =============================================================================

volatile float    currentSPL       = 0.0f;
volatile float    currentSPLA      = 0.0f;
volatile bool     splValid         = false;
volatile bool     sdAvailable      = false;
volatile bool     isRecording      = false;
volatile uint32_t recordElapsedSec = 0;
volatile uint32_t sdFreeMB         = 0;
volatile uint32_t audioDropCount   = 0;

bool rtcAvailable = false;


// =============================================================================
//  Audio Pool
// =============================================================================

AudioBlock    audioPool[AUDIO_POOL_SIZE];
QueueHandle_t freeQueue;
QueueHandle_t readyQueue;


// =============================================================================
//  Thread-safe Sensor Reads
// =============================================================================

bool readGPSSafe(GPSData &out) {
  MutexLock l(gpsMutex, 10);
  if (!l) return false;
  out.lat   = gpsLat;    out.lng  = gpsLng;    out.alt   = gpsAlt;
  out.speed = gpsSpeed;  out.sats = gpsSats;
  out.hdop  = gpsHdop;   out.valid = gpsValid;
  return true;
}

bool readLuxSafe(LuxData &out) {
  MutexLock l(luxMutex, 10);
  if (!l) return false;
  out.value = luxValue;  out.valid = luxValid;
  return true;
}

bool readDHTSafe(DHTData &out) {
  MutexLock l(dhtMutex, 10);
  if (!l) return false;
  out.temp = dhtTemp;  out.humidity = dhtHumidity;  out.valid = dhtValid;
  return true;
}


// =============================================================================
//  WDT-friendly Delay
// =============================================================================

void delayFeedWdt(uint32_t totalMs) {
  const uint32_t step = 1000;
  while (totalMs > 0) {
    esp_task_wdt_reset();
    uint32_t d = (totalMs > step) ? step : totalMs;
    vTaskDelay(d / portTICK_PERIOD_MS);
    totalMs -= d;
  }
}
