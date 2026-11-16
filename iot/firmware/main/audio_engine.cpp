#include "audio_engine.h"
#include "globals.h"
#include "rtc_helper.h"

#include <driver/i2s_std.h>
#include <esp_task_wdt.h>


static i2s_chan_handle_t rx_handle = NULL;


// =============================================================================
//  I2S Driver
// =============================================================================

static bool i2sStart() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num  = BUFFER_CNT;
  chan_cfg.dma_frame_num = BUFFER_LEN;

  esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &rx_handle);
  if (err != ESP_OK) {
    LOG("ERROR", "i2s_new_channel failed: %d", err);
    return false;
  }

  i2s_std_config_t std_cfg = {
      .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                      I2S_SLOT_MODE_MONO),
      .gpio_cfg = {
          .mclk = I2S_GPIO_UNUSED,
          .bclk = (gpio_num_t)I2S_SCK,
          .ws   = (gpio_num_t)I2S_WS,
          .dout = I2S_GPIO_UNUSED,
          .din  = (gpio_num_t)I2S_SD,
          .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
      },
  };
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

  err = i2s_channel_init_std_mode(rx_handle, &std_cfg);
  if (err != ESP_OK) {
    LOG("ERROR", "i2s_channel_init_std_mode failed: %d", err);
    return false;
  }

  err = i2s_channel_enable(rx_handle);
  if (err != ESP_OK) {
    LOG("ERROR", "i2s_channel_enable failed: %d", err);
    return false;
  }
  return true;
}

static void i2sStop() {
  if (!rx_handle) return;
  i2s_channel_disable(rx_handle);
  i2s_del_channel(rx_handle);
  rx_handle = NULL;
}


// =============================================================================
//  WAV Header
// =============================================================================

// 44-byte PCM WAV header (mono)
struct WAVHeader {
  char     riff[4]       = {'R', 'I', 'F', 'F'};
  uint32_t fileSize      = 0;
  char     wave[4]       = {'W', 'A', 'V', 'E'};
  char     fmt[4]        = {'f', 'm', 't', ' '};
  uint32_t fmtSize       = 16;
  uint16_t audioFormat   = 1;   // PCM
  uint16_t numChannels   = 1;
  uint32_t sampleRate    = SAMPLE_RATE;
  uint32_t byteRate      = SAMPLE_RATE * (BITS_PER_SAMPLE / 8);
  uint16_t blockAlign    = BITS_PER_SAMPLE / 8;
  uint16_t bitsPerSample = BITS_PER_SAMPLE;
  char     data[4]       = {'d', 'a', 't', 'a'};
  uint32_t dataSize      = 0;
};

static_assert(sizeof(WAVHeader) == WAV_HEADER_SIZE, "WAV header must be 44 bytes");

void writeWAVHeader(File &file, uint32_t dataSize) {
  WAVHeader h;
  h.dataSize = dataSize;
  h.fileSize = dataSize + sizeof(WAVHeader) - 8;
  file.seek(0);
  file.write((uint8_t *)&h, sizeof(h));
}


// =============================================================================
//  Helpers
// =============================================================================

static void abortMicTask(const char *reason) {
  LOG("ERROR", "[Core 1] %s - micTask abort", reason);
  esp_task_wdt_delete(NULL);
  vTaskDelete(NULL);
}


// =============================================================================
//  micTask  (Core 1, Priority 2)
// =============================================================================

void micTask(void *parameter) {
  esp_task_wdt_add(NULL);

  LOG("INFO", "[Core 1] Starting I2S...");
  if (!i2sStart()) {
    abortMicTask("I2S start failed");
    return;
  }

  int32_t *rawBuffer = (int32_t *)malloc(BUFFER_LEN * sizeof(int32_t));
  if (!rawBuffer) {
    abortMicTask("malloc failed");
    return;
  }

  LOG("INFO", "[Core 1] I2S Ready, reading audio...");

  uint32_t i2sErrorCount = 0;
  uint32_t dropCount     = 0;

  while (1) {
    esp_task_wdt_reset();

    size_t bytesIn = 0;
    esp_err_t result = i2s_channel_read(rx_handle, rawBuffer,
                                        BUFFER_LEN * sizeof(int32_t),
                                        &bytesIn, portMAX_DELAY);
    const int numSamples = bytesIn / sizeof(int32_t);

    // ── I2S error recovery ───────────────────────────────────────────────
    if (result != ESP_OK || numSamples == 0) {
      i2sErrorCount++;
      if (i2sErrorCount % 50 == 1)
        LOG("WARN", "[Core 1] I2S read error #%lu (err=%d)",
            (unsigned long)i2sErrorCount, result);

      if (i2sErrorCount >= I2S_ERROR_THRESHOLD) {
        LOG("WARN", "[Core 1] Re-initializing I2S driver...");
        i2sStop();
        if (i2sStart()) {
          LOG("INFO", "[Core 1] I2S re-initialized");
          i2sErrorCount = 0;
        } else {
          LOG("ERROR", "[Core 1] I2S re-init failed - waiting 5s");
          delayFeedWdt(5000);
        }
      }
      vTaskDelay(1);
      continue;
    }
    i2sErrorCount = 0;

    // ── Take a free block (drop audio if SD pipeline is behind) ─────────
    AudioBlock *blk = NULL;
    if (xQueueReceive(freeQueue, &blk, 0) != pdTRUE) {
      audioDropCount++;
      if (dropCount++ % 100 == 0)
        LOG("WARN", "Audio drop #%lu (pool full)", (unsigned long)dropCount);
      vTaskDelay(1);
      continue;
    }

    // INMP441 delivers 24-bit data left-aligned in 32 bits -> keep top 16 bits
    for (int i = 0; i < numSamples; i++)
      blk->samples[i] = (int16_t)(rawBuffer[i] >> 16);
    blk->byteCount = numSamples * sizeof(int16_t);

    xQueueSend(readyQueue, &blk, portMAX_DELAY);
    vTaskDelay(1);
  }
}
