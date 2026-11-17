#include "sd_logger.h"
#include "audio_engine.h"
#include "globals.h"
#include "rtc_helper.h"
#include "settings.h"

#include <SD_MMC.h>
#include <esp_task_wdt.h>


static const unsigned long SD_FREE_CHECK_MS = 5000;

// Rotate WAV by audio data written, not wall-clock, so every file holds
// exactly settings.recordTimeSec seconds of samples.
static uint32_t wavTargetBytes() {
  return settings.recordTimeSec * SAMPLE_RATE * (BITS_PER_SAMPLE / 8);
}

// The logger is normally switched off by cutting power, so the open WAV file
// never reaches finalizeRecording(). Rewrite its header and flush to the card
// every WAV_SYNC_SEC: a power cut then loses at most that much audio instead
// of leaving a file with dataSize = 0 (or 0 bytes on disk).
static const uint32_t WAV_SYNC_SEC   = 5;
static const uint32_t WAV_SYNC_BYTES = WAV_SYNC_SEC * SAMPLE_RATE * (BITS_PER_SAMPLE / 8);


// =============================================================================
//  File-local State
// =============================================================================

static char          csvFileName[64];
static char          wavFileName[64];
static File          audioFile;
static File          csvFile;   // kept open; flushed after every row
static uint32_t      csvRecordId = 0;   // last Record_ID written (1, 2, 3 ...)

static bool          splInitialized       = false;
static unsigned long recordStartMs        = 0;
static uint32_t      recordStartDrops     = 0;
static uint32_t      wavBytesWritten      = 0;
static uint32_t      wavSyncedBytes       = 0;   // wavBytesWritten at last sync

static uint32_t      sdErrorCount         = 0;     // consecutive SD errors
static bool          sdHealthy            = true;  // false: stop recording, retry remount
static unsigned long lastRemountAttemptMs = 0;


// =============================================================================
//  SD Helpers
// =============================================================================

static bool sdBegin() {
  return SD_MMC.begin("/sdcard", true /* 1-bit */, false, 4000 /* kHz */);
}

// Caller must hold sdMutex.
static uint32_t querySDFreeMB() {
  return (uint32_t)((SD_MMC.totalBytes() - SD_MMC.usedBytes()) / (1024 * 1024));
}

// Build "<dateFolder>[/<sub>]/<prefix>_<timestamp>.<ext>" and create the
// folder(s). With hourly = true the file goes into a per-hour subfolder
// ("/20250417/14", or "/logs/wav" without RTC): a day of 60 s WAV files is
// ~1440 files, and FAT looks names up linearly, so one big folder gets
// slower to open/create files in as the day goes on.
static void buildDatedPath(char *out, size_t len, const char *prefix,
                           const char *ext, bool hourly = false) {
  char dateFolder[DATEFOLDER_BUF_SIZE];
  char ts[TIMESTAMP_BUF_SIZE];
  getDateFolderStr(dateFolder, sizeof(dateFolder));
  getTimestampStr(ts, sizeof(ts));

  char folder[DATEFOLDER_BUF_SIZE + 8];
  if (!hourly)
    snprintf(folder, sizeof(folder), "%s", dateFolder);
  else if (rtcAvailable && strlen(ts) >= 11)   // "YYYYMMDD_HHMMSS" -> "HH"
    snprintf(folder, sizeof(folder), "%s/%.2s", dateFolder, ts + 9);
  else
    snprintf(folder, sizeof(folder), "%s/wav", dateFolder);

  snprintf(out, len, "%s/%s_%s.%s", folder, prefix, ts, ext);

  MutexLock lock(sdMutex);
  if (!SD_MMC.exists(dateFolder)) SD_MMC.mkdir(dateFolder);
  if (hourly && !SD_MMC.exists(folder)) SD_MMC.mkdir(folder);

  // Never overwrite an existing file. Names repeat after a reboot without RTC
  // (millis-based names), or when the DS3231 lost power and was reset to the
  // build time on every boot; FILE_WRITE would then truncate old data.
  for (int n = 1; n < 100 && SD_MMC.exists(out); n++)
    snprintf(out, len, "%s/%s_%s_%d.%s", folder, prefix, ts, n, ext);
}


// =============================================================================
//  Setup
// =============================================================================

bool mountSDCard() {
  pinMode(SDMMC_DAT0_PIN, INPUT_PULLUP);
  pinMode(SDMMC_CMD_PIN,  INPUT_PULLUP);
  pinMode(SDMMC_CLK_PIN,  INPUT_PULLUP);
  delay(100);

  if (!sdBegin()) {
    sdHealthy   = false;          // sdProcessTask retries the mount
    sdAvailable = false;
    sdFreeMB    = 0;
    return false;
  }

  MutexLock lock(sdMutex);
  sdFreeMB    = querySDFreeMB();
  sdHealthy   = true;
  sdAvailable = true;
  return true;
}

// The CSV stays open for the whole session instead of open/close per row
// (each open searches the folder). Rows are flushed (fflush + fsync) one by
// one, so a power cut still loses at most the row being written.
bool createCSVLog() {
  buildDatedPath(csvFileName, sizeof(csvFileName), "spl_log", "csv");

  MutexLock lock(sdMutex);
  csvFile = SD_MMC.open(csvFileName, FILE_WRITE);
  if (!csvFile) {
    LOG("ERROR", "Cannot create log file %s", csvFileName);
    return false;
  }
  csvFile.println(CSV_HEADER);
  csvFile.flush();
  LOG("INFO", "Log file: %s", csvFileName);
  return true;
}

void csvActivePath(char *out, size_t len) {
  MutexLock lock(sdMutex);
  snprintf(out, len, "%s", csvFileName);
}

// Reopen the CSV for append after an SD remount. Caller must hold sdMutex.
static void reopenCSVLog() {
  if (csvFile) csvFile.close();
  csvFile = SD_MMC.open(csvFileName, FILE_APPEND);
  if (!csvFile) LOG("ERROR", "Cannot reopen log file %s", csvFileName);
}


// =============================================================================
//  CSV Row
// =============================================================================

// "%.Nf" of v, or "" when the reading is not valid (no stale numbers).
static void fmtOpt(char *buf, size_t len, bool ok, int decimals, double v) {
  if (ok) snprintf(buf, len, "%.*f", decimals, v);
  else    buf[0] = '\0';
}

// Append "CODE" to a ';'-separated list.
static void addFlag(char *flags, size_t len, const char *code) {
  size_t n = strlen(flags);
  snprintf(flags + n, len - n, "%s%s", n ? ";" : "", code);
}

// Highest lux the BH1750 can report after calibration (sensor saturated).
static float luxMaxCalibrated() {
  return LUX_RAW_MAX * settings.luxCalSlope + settings.luxCalOffset;
}

static void appendCSVRow(float splA, float splZ, bool splOk) {
  GPSData g; LuxData l; DHTData d;
  readGPSSafe(g); readLuxSafe(l); readDHTSafe(d);

  char dtBuf[DATETIME_BUF_SIZE];
  bool timeOk = getDateTimeStrChecked(dtBuf, sizeof(dtBuf));

  // ── Quality flags (Acceptance: Sensor Error / GPS Missing / Time / OOR)
  char flags[96] = "";
  if (!timeOk) addFlag(flags, sizeof(flags), "TIME_INVALID");
  if (!splOk)  addFlag(flags, sizeof(flags), "SPL_ERR");
  else if (splZ < SPL_MIN_DB || splZ > SPL_MAX_DB) addFlag(flags, sizeof(flags), "SPL_OOR");
  if (!l.valid) addFlag(flags, sizeof(flags), "LUX_ERR");
  else if (l.value < 0.0f || l.value >= luxMaxCalibrated() - 1.0f) addFlag(flags, sizeof(flags), "LUX_OOR");
  if (!d.valid) addFlag(flags, sizeof(flags), "DHT_ERR");
  else {
    if (d.temp < TEMP_MIN_C || d.temp > TEMP_MAX_C) addFlag(flags, sizeof(flags), "TEMP_OOR");
    if (d.humidity < HUMID_MIN_PCT || d.humidity > HUMID_MAX_PCT) addFlag(flags, sizeof(flags), "HUMID_OOR");
  }
  if (!g.valid) addFlag(flags, sizeof(flags), "GPS_MISSING");
  else if (g.hdop > GPS_MAX_HDOP) addFlag(flags, sizeof(flags), "GPS_POOR");
  if (!flags[0]) strcpy(flags, "OK");

  // ── Values: blank when the sensor reading is not valid
  char sA[12], sZ[12], sLux[14], sT[10], sH[10], sLat[16], sLng[16], sAlt[12], sSpd[12];
  fmtOpt(sA,   sizeof(sA),   splOk,   2, splA);
  fmtOpt(sZ,   sizeof(sZ),   splOk,   2, splZ);
  fmtOpt(sLux, sizeof(sLux), l.valid, 2, l.value);
  fmtOpt(sT,   sizeof(sT),   d.valid, 1, d.temp);
  fmtOpt(sH,   sizeof(sH),   d.valid, 1, d.humidity);
  fmtOpt(sLat, sizeof(sLat), g.valid, 7, g.lat);
  fmtOpt(sLng, sizeof(sLng), g.valid, 7, g.lng);
  fmtOpt(sAlt, sizeof(sAlt), g.valid, 1, g.alt);
  fmtOpt(sSpd, sizeof(sSpd), g.valid, 2, g.speed);

  MutexLock lock(sdMutex);
  if (!csvFile) {
    sdErrorCount++;
    return;
  }
  // Record_ID goes up even if the write fails, so a gap in the CSV shows a
  // lost row and an ID is never reused within a session.
  csvRecordId++;
  size_t n = csvFile.printf(
      "%s,%s,%lu,%lu,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%.2f,%d,%d,%d,%d,%d,%s\n",
      settings.deviceId, deviceTypeName(settings.deviceType),
      (unsigned long)sessionId(), (unsigned long)csvRecordId,
      dtBuf, sA, sZ, sLux, sT, sH, sLat, sLng, sAlt, sSpd, g.sats, g.hdop,
      g.valid, l.valid, d.valid, timeOk, splOk, flags);
  csvFile.flush();
  if (n == 0) sdErrorCount++;
  else        sdErrorCount = 0;
}


// =============================================================================
//  A-weighting filter (IEC 61672) for fs = 16 kHz
//
//  3 biquads (Direct Form II Transposed), gain normalized to 0 dB at 1 kHz.
//  Start point: bilinear transform of the analog A-weighting curve, then the
//  poles/zeros were least-squares fitted at 16 kHz because the plain bilinear
//  design droops -5.7 dB at 6.3 kHz (the analog 12.2 kHz pole is above Nyquist).
//  Error vs. the ideal curve: within +/-0.4 dB from 20 Hz to 6.3 kHz,
//  -0.5 dB at 7 kHz (IEC 61672 Class 1 allows +1.5/-2 dB at 6.3 kHz).
//  Coefficients are only valid for SAMPLE_RATE == 16000.
// =============================================================================

static_assert(SAMPLE_RATE == 16000, "A-weighting coefficients are for 16 kHz");

struct Biquad { float b0, b1, b2, a1, a2; };

static const Biquad A_WEIGHT_SOS[3] = {
    {0.936332052f,  0.999544701f, 0.266756223f,  0.901509118f, 0.203179669f},
    {1.0f,         -2.0f,         1.0f,         -1.714635148f, 0.725128559f},
    {1.0f,         -2.0f,         1.0f,         -1.984322300f, 0.984383437f},
};

static float aState[3][2] = {{0}};

static inline float aWeight(float x) {
  for (int i = 0; i < 3; i++) {
    const Biquad &c = A_WEIGHT_SOS[i];
    float y      = c.b0 * x + aState[i][0];
    aState[i][0] = c.b1 * x - c.a1 * y + aState[i][1];
    aState[i][1] = c.b2 * x - c.a2 * y;
    x = y;
  }
  return x;
}


// =============================================================================
//  SPL  - Leq over RMS_WINDOW blocks (~0.96 s), then EMA.
//  Computes both flat (Z, unweighted) and A-weighted levels from the same
//  samples. Updates currentSPL (dB Z) and currentSPLA (dB(A)).
//  The WAV file keeps the raw, unweighted audio.
// =============================================================================

static float meanSquareToDb(float meanSquare) {
  float rms = sqrtf(meanSquare);
  if (rms < 1e-5f) rms = 1e-5f;
  return 20.0f * log10f(rms) + settings.micOffsetDb;
}

static void computeSPLBlock(const AudioBlock *blk) {
  static float sumZ     = 0.0f;   // accumulated per-block mean-square
  static float sumA     = 0.0f;
  static int   rmsCount = 0;

  int numSamples = blk->byteCount / sizeof(int16_t);
  if (numSamples == 0) return;

  float sz = 0.0f, sa = 0.0f;
  for (int i = 0; i < numSamples; i++) {
    float s = blk->samples[i] / 32768.0f;
    float a = aWeight(s);
    sz += s * s;
    sa += a * a;
  }
  sumZ += sz / numSamples;
  sumA += sa / numSamples;
  if (++rmsCount < RMS_WINDOW) return;

  float rawZ = meanSquareToDb(sumZ / rmsCount);
  float rawA = meanSquareToDb(sumA / rmsCount);
  sumZ = sumA = 0.0f;
  rmsCount = 0;

  if (splInitialized) {
    currentSPL  = EMA_ALPHA * rawZ + (1.0f - EMA_ALPHA) * currentSPL;
    currentSPLA = EMA_ALPHA * rawA + (1.0f - EMA_ALPHA) * currentSPLA;
  } else {
    currentSPL  = rawZ;
    currentSPLA = rawA;
  }
  splInitialized = true;
}


// =============================================================================
//  WAV Recording
// =============================================================================

static bool tryStartRecording() {
  uint32_t freeMB;
  {
    MutexLock lock(sdMutex, 50);
    if (!lock) {
      LOG("WARN", "sdMutex timeout - skipping recording start");
      return false;
    }
    freeMB = querySDFreeMB();
  }
  sdFreeMB = freeMB;

  if (freeMB < settings.sdMinFreeMb) {
    LOG("WARN", "SD free < %luMB, skipping new recording",
        (unsigned long)settings.sdMinFreeMb);
    return false;
  }

  buildDatedPath(wavFileName, sizeof(wavFileName), "audio", "wav", true);

  MutexLock lock(sdMutex);
  audioFile = SD_MMC.open(wavFileName, FILE_WRITE);
  if (!audioFile) {
    sdErrorCount++;
    return false;
  }
  writeWAVHeader(audioFile, 0);   // placeholder, rewritten on sync/finalize

  wavBytesWritten  = 0;
  wavSyncedBytes   = 0;
  recordStartMs    = millis();
  recordStartDrops = audioDropCount;
  recordElapsedSec = 0;
  sdErrorCount     = 0;
  isRecording      = true;
  LOG("INFO", "[Core 0] Recording: %s", wavFileName);
  return true;
}

static void writeAudioBlock(const AudioBlock *blk) {
  MutexLock lock(sdMutex);
  if (!audioFile) return;

  size_t written = audioFile.write((const uint8_t *)blk->samples, blk->byteCount);
  if (written == blk->byteCount) {
    wavBytesWritten += written;
    sdErrorCount = 0;
  } else {
    sdErrorCount++;
  }

  // Periodic sync (see WAV_SYNC_SEC): header with current size, seek back to
  // the end of the data, then flush() = fflush + fsync (updates FAT/dir entry).
  if (wavBytesWritten - wavSyncedBytes >= WAV_SYNC_BYTES) {
    writeWAVHeader(audioFile, wavBytesWritten);
    audioFile.seek(WAV_HEADER_SIZE + wavBytesWritten);
    audioFile.flush();
    wavSyncedBytes = wavBytesWritten;
  }
}

static void finalizeRecording() {
  {
    MutexLock lock(sdMutex);
    if (audioFile) {
      writeWAVHeader(audioFile, wavBytesWritten);
      audioFile.close();
    }
  }
  isRecording      = false;
  recordElapsedSec = 0;

  float audioSec = wavBytesWritten / (float)(SAMPLE_RATE * (BITS_PER_SAMPLE / 8));
  float wallSec  = (millis() - recordStartMs) / 1000.0f;
  uint32_t drops = audioDropCount - recordStartDrops;
  // LOG tag must be a string literal (concatenated at compile time)
  if (drops)
    LOG("WARN", "[Core 0] Recording complete (%.2f MB, audio %.1fs, wall %.1fs, dropped blocks %lu)",
        wavBytesWritten / 1048576.0f, audioSec, wallSec, (unsigned long)drops);
  else
    LOG("INFO", "[Core 0] Recording complete (%.2f MB, audio %.1fs, wall %.1fs)",
        wavBytesWritten / 1048576.0f, audioSec, wallSec);
}


// =============================================================================
//  SD Health
// =============================================================================

// Too many consecutive errors: mark unhealthy and stop recording.
static void checkSDHealth() {
  if (!sdHealthy || sdErrorCount < SD_ERROR_THRESHOLD) return;

  sdHealthy   = false;
  sdAvailable = false;
  lastRemountAttemptMs = millis();   // first remount after SD_REMOUNT_INTERVAL_MS
  LOG("ERROR", "SD card: %lu consecutive errors - stopping recording",
      (unsigned long)sdErrorCount);
  if (isRecording) finalizeRecording();

  MutexLock lock(sdMutex);
  if (csvFile) csvFile.close();
}

static void tryRemountSD() {
  if (sdHealthy) return;

  unsigned long now = millis();
  if (now - lastRemountAttemptMs < SD_REMOUNT_INTERVAL_MS) return;
  lastRemountAttemptMs = now;

  LOG("SD", "Attempting remount...");
  {
    MutexLock lock(sdMutex, 100);
    if (!lock) return;
    SD_MMC.end();
    vTaskDelay(100 / portTICK_PERIOD_MS);
    if (!sdBegin()) {
      LOG("SD", "Remount failed - retry in %ds", SD_REMOUNT_INTERVAL_MS / 1000);
      return;
    }
    sdFreeMB = querySDFreeMB();
    if (csvFileName[0]) reopenCSVLog();
  }
  // No card at boot: the CSV was never created (createCSVLog locks sdMutex
  // itself, so call it after the lock above is released).
  if (!csvFileName[0]) createCSVLog();

  sdErrorCount = 0;
  sdHealthy    = true;
  sdAvailable  = true;
  LOG("SD", "Mount OK - recording resumed");
}

static void refreshSDFree() {
  static unsigned long lastCheckMs = 0;
  // f_getfree can stall the SD bus; while recording, tryStartRecording()
  // already refreshes sdFreeMB once per file.
  if (!sdHealthy || isRecording || millis() - lastCheckMs < SD_FREE_CHECK_MS) return;
  lastCheckMs = millis();

  MutexLock lock(sdMutex, 50);
  if (lock) sdFreeMB = querySDFreeMB();
}


// =============================================================================
//  sdProcessTask  (Core 0, Priority 1)
// =============================================================================

void sdProcessTask(void *parameter) {
  esp_task_wdt_add(NULL);
  LOG("INFO", "[Core 0] SD Process Task started");

  unsigned long lastCsvSaveMs   = 0;
  unsigned long lastStartFailMs = 0;
  unsigned long lastSplMs       = 0;   // last audio block turned into SPL
  bool          startFailed     = false;

  while (1) {
    esp_task_wdt_reset();

    tryRemountSD();
    refreshSDFree();

    // No audio for 100 ms (mic stalled or micTask gone) still falls through,
    // so CSV rows for lux / DHT / GPS keep coming with SPL_Valid = 0.
    AudioBlock *blk = NULL;
    bool haveBlock = xQueueReceive(readyQueue, &blk, 100 / portTICK_PERIOD_MS) == pdTRUE;

    unsigned long now = millis();

    if (haveBlock) {
      // ── WAV: start (retry failures every SD_FREE_CHECK_MS) / write / rotate
      if (!isRecording && sdHealthy &&
          (!startFailed || now - lastStartFailMs >= SD_FREE_CHECK_MS)) {
        startFailed = !tryStartRecording();
        if (startFailed) lastStartFailMs = now;
      }

      if (isRecording) {
        writeAudioBlock(blk);
        recordElapsedSec = wavBytesWritten / (SAMPLE_RATE * (BITS_PER_SAMPLE / 8));
        if (wavBytesWritten >= wavTargetBytes())
          finalizeRecording();
      }

      computeSPLBlock(blk);
      lastSplMs = now;
    } else if (isRecording && now - lastSplMs >= SPL_STALE_MS) {
      LOG("WARN", "[Core 0] No audio for %lums - closing WAV", (unsigned long)(now - lastSplMs));
      finalizeRecording();
    }

    splValid = splInitialized && lastSplMs && now - lastSplMs < SPL_STALE_MS;

    // ── CSV ────────────────────────────────────────────────────────────
    if (sdHealthy && now - lastCsvSaveMs >= settings.csvIntervalMs) {
      appendCSVRow(currentSPLA, currentSPL, splValid);
      // Step by the interval instead of jumping to `now`: audio blocks arrive
      // every 32 ms, so `= now` made each row ~1.024 s apart and the CSV
      // skipped one second about every 40 rows. Resync after a long gap.
      lastCsvSaveMs += settings.csvIntervalMs;
      if (now - lastCsvSaveMs >= settings.csvIntervalMs) lastCsvSaveMs = now;
    }

    checkSDHealth();

    if (haveBlock) xQueueSend(freeQueue, &blk, portMAX_DELAY);
    vTaskDelay(1);
  }
}
