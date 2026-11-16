#include "sensors.h"
#include "globals.h"
#include "rtc_helper.h"
#include "settings.h"

#include <BH1750.h>
#include <DHT.h>
#include <esp_task_wdt.h>


static BH1750 lightMeter(BH1750_ADDR);
static DHT    dht(DHT_PIN, DHT_TYPE);

static const int   LUX_FAIL_REINIT_THRESHOLD = 10;
static const float LUX_DEADBAND              = 1.0f;


// =============================================================================
//  Sensor Init
// =============================================================================

void initBH1750() {
  const int MAX_ATTEMPTS = 3;
  for (int attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE)) {
      LOG("INFO", "BH1750 initialized (attempt %d/%d).", attempt, MAX_ATTEMPTS);
      return;
    }
    LOG("WARN", "BH1750 init attempt %d/%d failed", attempt, MAX_ATTEMPTS);
    delay(200);
  }
  LOG("WARN", "BH1750 not found! Check wiring & address.");
}

void initDHT() {
  dht.begin();
  LOG("INFO", "DHT22 initialized (GPIO %d).", DHT_PIN);
}


// =============================================================================
//  Lux Filter  — median (cuts I2C glitch spikes) then EMA (smooths noise)
// =============================================================================

static float medianOf(const float *buf) {
  float sorted[LUX_MEDIAN_WINDOW];
  memcpy(sorted, buf, sizeof(sorted));
  for (int i = 1; i < LUX_MEDIAN_WINDOW; i++) {
    float key = sorted[i];
    int j = i - 1;
    while (j >= 0 && sorted[j] > key) {
      sorted[j + 1] = sorted[j];
      j--;
    }
    sorted[j + 1] = key;
  }
  return sorted[LUX_MEDIAN_WINDOW / 2];
}

static float filterLux(float sample) {
  static float buf[LUX_MEDIAN_WINDOW] = {0};
  static int   idx     = 0;
  static int   count   = 0;
  static float ema     = 0.0f;
  static bool  emaInit = false;

  buf[idx] = sample;
  idx = (idx + 1) % LUX_MEDIAN_WINDOW;
  if (count < LUX_MEDIAN_WINDOW) count++;

  float filtered = (count == LUX_MEDIAN_WINDOW) ? medianOf(buf) : sample;

  ema = emaInit ? LUX_EMA_ALPHA * filtered + (1.0f - LUX_EMA_ALPHA) * ema
                : filtered;
  emaInit = true;
  return ema;
}


// =============================================================================
//  luxTask  (Core 0) — BH1750, 200 ms cycle
// =============================================================================

void luxTask(void *parameter) {
  esp_task_wdt_add(NULL);
  LOG("INFO", "Lux Task started (slope=%.3f offset=%.1f median=%d)",
      settings.luxCalSlope, settings.luxCalOffset, LUX_MEDIAN_WINDOW);

  int failCount = 0;

  while (1) {
    esp_task_wdt_reset();

    float raw = -1.0f;
    {
      MutexLock l(wireMutex, 200);
      if (l && lightMeter.measurementReady())
        raw = lightMeter.readLightLevel();
    }

    if (raw >= 0.0f) {
      failCount = 0;

      float calibrated = raw * settings.luxCalSlope + settings.luxCalOffset;
      if (calibrated < 0.0f) calibrated = 0.0f;
      float lux = filterLux(calibrated);

      MutexLock l(luxMutex, 20);
      if (l) {
        if (!luxValid || fabsf(lux - luxValue) >= LUX_DEADBAND)
          luxValue = lux;
        luxValid = true;
      }
    } else {
      if (++failCount >= LUX_FAIL_REINIT_THRESHOLD) {
        LOG("WARN", "BH1750 %d consecutive fails - re-initializing", failCount);
        {
          MutexLock l(wireMutex, 200);
          if (l) lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
        }
        failCount = 0;
        vTaskDelay(200 / portTICK_PERIOD_MS);
      }

      MutexLock l(luxMutex, 20);
      if (l) luxValid = false;
    }

    vTaskDelay(200 / portTICK_PERIOD_MS);
  }
}


// =============================================================================
//  dhtTask  (Core 0) — DHT22, 2000 ms cycle
// =============================================================================

void dhtTask(void *parameter) {
  esp_task_wdt_add(NULL);
  LOG("INFO", "DHT22 Task started (GPIO %d)", DHT_PIN);

  vTaskDelay(500 / portTICK_PERIOD_MS);

  while (1) {
    esp_task_wdt_reset();

    float temp = dht.readTemperature();
    float humi = dht.readHumidity();
    bool  ok   = !isnan(temp) && !isnan(humi);

    {
      MutexLock l(dhtMutex, 20);
      if (l) {
        if (ok) {
          dhtTemp     = temp + settings.dhtTempOffset;
          dhtHumidity = constrain(humi + settings.dhtHumidOffset, 0.0f, 100.0f);
        }
        dhtValid = ok;
      }
    }

    if (!ok) LOG("WARN", "DHT22 read failed!");

    vTaskDelay(2000 / portTICK_PERIOD_MS);
  }
}
