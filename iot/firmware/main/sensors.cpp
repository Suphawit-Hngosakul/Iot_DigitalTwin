#include "sensors.h"
#include "globals.h"
#include "rtc_helper.h"
#include "settings.h"

#include <BH1750.h>
#include <driver/gpio.h>
#include <esp_rom_sys.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>


static BH1750 lightMeter(BH1750_ADDR);

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

// =============================================================================
//  DHT22 reader (replaces Adafruit DHT library)
//
//  Adafruit DHT 1.4.x reads with interrupts off and times out each pulse after
//  _maxcycles loop turns (= 1 ms worth of CPU cycles, 160000 at 160 MHz). Each
//  turn calls digitalRead(), which is much slower than one cycle on core 3.x,
//  so a missed/noisy pulse keeps interrupts off on Core 0 for hundreds of ms
//  and trips the Interrupt WDT (300 ms):
//    "Core 0 panic'ed (Interrupt wdt timeout on CPU0)" in dhtTask.
//  Here every pulse is timed in microseconds and capped, so interrupts are off
//  for ~4 ms per read normally and at most ~83 x DHT_PULSE_TIMEOUT_US.
// =============================================================================

static const uint32_t DHT_START_LOW_US     = 1100;  // host start signal (>= 1 ms)
static const uint32_t DHT_PULSE_TIMEOUT_US = 200;   // longest pulse is ~80 us

static portMUX_TYPE dhtMux = portMUX_INITIALIZER_UNLOCKED;

// Microseconds the line stays at `level`, or -1 after DHT_PULSE_TIMEOUT_US.
static inline int32_t dhtPulse(int level) {
  const int64_t start = esp_timer_get_time();
  while (gpio_get_level((gpio_num_t)DHT_PIN) == level) {
    if (esp_timer_get_time() - start > DHT_PULSE_TIMEOUT_US) return -1;
  }
  return (int32_t)(esp_timer_get_time() - start);
}

// One DHT22 transaction. Call no more often than every 2 s.
static bool dhtRead(float &temp, float &humi) {
  int32_t lowUs[40], highUs[40];
  bool    ok = true;

  // Start signal: pull low >= 1 ms with interrupts on.
  gpio_set_level((gpio_num_t)DHT_PIN, 0);
  esp_rom_delay_us(DHT_START_LOW_US);

  portENTER_CRITICAL(&dhtMux);
  gpio_set_level((gpio_num_t)DHT_PIN, 1);          // release (open drain)
  esp_rom_delay_us(10);                            // let the pull-up raise it
  // Sensor answers 20-40 us later with 80 us LOW + 80 us HIGH, then 40 bits.
  if (dhtPulse(1) < 0 || dhtPulse(0) < 0 || dhtPulse(1) < 0) {
    ok = false;
  } else {
    for (int i = 0; i < 40; i++) {
      lowUs[i]  = dhtPulse(0);
      highUs[i] = dhtPulse(1);
      if (lowUs[i] < 0 || (highUs[i] < 0 && i < 39)) { ok = false; break; }
    }
  }
  portEXIT_CRITICAL(&dhtMux);
  if (!ok) return false;

  // Bit = 1 when the HIGH part is longer than the LOW part (~70 vs ~50 us).
  // The last HIGH may "time out" because the line idles high afterwards.
  uint8_t d[5] = {0};
  for (int i = 0; i < 40; i++) {
    const bool one = highUs[i] < 0 || highUs[i] > lowUs[i];
    d[i / 8] = (uint8_t)((d[i / 8] << 1) | (one ? 1 : 0));
  }
  if ((uint8_t)(d[0] + d[1] + d[2] + d[3]) != d[4]) return false;

  humi = ((d[0] << 8) | d[1]) * 0.1f;
  temp = (((d[2] & 0x7F) << 8) | d[3]) * 0.1f;
  if (d[2] & 0x80) temp = -temp;
  return true;
}

void initDHT() {
  // Open drain + pull-up: write 0 to pull the line low, 1 to release it,
  // and read the level in the same mode (no direction switching).
  gpio_reset_pin((gpio_num_t)DHT_PIN);
  gpio_set_direction((gpio_num_t)DHT_PIN, GPIO_MODE_INPUT_OUTPUT_OD);
  gpio_set_pull_mode((gpio_num_t)DHT_PIN, GPIO_PULLUP_ONLY);
  gpio_set_level((gpio_num_t)DHT_PIN, 1);
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

    float temp = NAN, humi = NAN;
    bool  ok   = dhtRead(temp, humi);

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
