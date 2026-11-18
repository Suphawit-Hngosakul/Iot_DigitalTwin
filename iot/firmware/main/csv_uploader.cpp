#include "csv_uploader.h"
#include "globals.h"
#include "rtc_helper.h"
#include "sd_logger.h"
#include "settings.h"

#include <HTTPClient.h>
#include <SD_MMC.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_heap_caps.h>


static const uint32_t SD_LOCK_MS    = 2000;
static const char     SKIP_LOG[]    = "/upload_skipped.csv";
static const char     CSV_HDR[]     = CSV_HEADER "\n";      // sent before every chunk
static const size_t   CSV_HDR_LEN   = sizeof(CSV_HDR) - 1;
static const size_t   PATH_LEN      = 72;
static const uint32_t DEFAULT_ROW_BYTES = 120;              // backlog estimate before first chunk
static const int      CODE_BAD_URL  = -1000;                // HTTPClient::begin() refused the URL

static void sleepMs(uint32_t ms) { vTaskDelay(pdMS_TO_TICKS(ms)); }


// =============================================================================
//  SD helpers — each call holds sdMutex only for its own open/read/close, so
//  sdProcessTask (WAV every 32 ms, pool ~1 s) is never blocked for long.
//  Files are reopened per read: survives an SD remount between reads.
// =============================================================================

// Read up to n bytes at `off`. Returns bytes read (0 = EOF) or -1 on error.
static int sdReadAt(const char *path, uint32_t off, uint8_t *buf, size_t n) {
  if (!sdAvailable) return -1;
  MutexLock lock(sdMutex, SD_LOCK_MS);
  if (!lock) return -1;
  File f = SD_MMC.open(path, FILE_READ);
  if (!f) return -1;
  int got = -1;
  if (f.seek(off)) got = (int)f.read(buf, n);
  f.close();
  return got;
}

static int32_t sdFileSize(const char *path) {
  if (!sdAvailable) return -1;
  MutexLock lock(sdMutex, SD_LOCK_MS);
  if (!lock) return -1;
  File f = SD_MMC.open(path, FILE_READ);
  if (!f) return -1;
  int32_t size = (int32_t)f.size();
  f.close();
  return size;
}

static bool sdExists(const char *path) {
  if (!sdAvailable) return false;
  MutexLock lock(sdMutex, SD_LOCK_MS);
  return lock && SD_MMC.exists(path);
}


// =============================================================================
//  Cursor "<csv>.idx" = "<byte_offset>,<last_record_id>\n"
// =============================================================================

static void idxPath(char *out, size_t len, const char *csv, bool tmp) {
  snprintf(out, len, "%s.idx%s", csv, tmp ? ".tmp" : "");
}

// Missing cursor -> 0,0 (file never sent). Falls back to ".idx.tmp" when a
// power cut hit between removing ".idx" and the rename.
static void readCursor(const char *csv, uint32_t &offset, uint32_t &lastId) {
  offset = 0;
  lastId = 0;
  char p[PATH_LEN + 8];
  char buf[32];
  for (int tmp = 0; tmp < 2; tmp++) {
    idxPath(p, sizeof(p), csv, tmp);
    if (!sdExists(p)) continue;
    int n = sdReadAt(p, 0, (uint8_t *)buf, sizeof(buf) - 1);
    if (n <= 0) continue;
    buf[n] = '\0';
    unsigned long o, r;
    if (sscanf(buf, "%lu,%lu", &o, &r) == 2) {
      offset = o;
      lastId = r;
      return;
    }
  }
}

// Write ".idx.tmp", then replace ".idx" (FAT rename can't overwrite).
static bool writeCursor(const char *csv, uint32_t offset, uint32_t lastId) {
  char tmp[PATH_LEN + 8], idx[PATH_LEN + 8];
  idxPath(tmp, sizeof(tmp), csv, true);
  idxPath(idx, sizeof(idx), csv, false);

  if (!sdAvailable) return false;
  MutexLock lock(sdMutex, SD_LOCK_MS);
  if (!lock) return false;
  File f = SD_MMC.open(tmp, FILE_WRITE);
  if (!f) return false;
  f.printf("%lu,%lu\n", (unsigned long)offset, (unsigned long)lastId);
  f.close();                                   // close = flush + fsync
  if (SD_MMC.exists(idx) && !SD_MMC.remove(idx)) return false;
  return SD_MMC.rename(tmp, idx);
}

// Rows the device will never send, so they can be found and resent by hand:
// "/upload_skipped.csv" = csv_path,start_byte,end_byte,lines,http_code,reason
static void logSkipped(const char *csv, uint32_t start, uint32_t end,
                       uint32_t lines, int code, const char *why) {
  LOG("UPL", "SKIP %s bytes %lu-%lu (%lu lines) code=%d: %s", csv,
      (unsigned long)start, (unsigned long)end, (unsigned long)lines, code, why);
  uploadRowsSkipped += lines;

  if (!sdAvailable) return;
  MutexLock lock(sdMutex, SD_LOCK_MS);
  if (!lock) return;
  bool isNew = !SD_MMC.exists(SKIP_LOG);
  File f = SD_MMC.open(SKIP_LOG, FILE_APPEND);
  if (!f) return;
  if (isNew) f.print("csv_path,start_byte,end_byte,lines,http_code,reason\n");
  f.printf("%s,%lu,%lu,%lu,%d,%s\n", csv, (unsigned long)start,
           (unsigned long)end, (unsigned long)lines, code, why);
  f.close();
}


// =============================================================================
//  Upload target = one CSV file
// =============================================================================

struct Target {
  char     path[PATH_LEN] = "";
  uint32_t offset  = 0;     // next byte to send (cursor)
  uint32_t lastId  = 0;     // Record_ID of the last row sent
  uint32_t hdrLen  = 0;     // 0 = header line not checked yet
};

// Folders whose name sorts below this had nothing left to send at the last
// scan. New CSV files only appear in the folder being written, so they can be
// skipped next time (RAM only: a reboot rescans everything once).
static char doneBefore[16] = "";

static bool isDataFolder(const char *name) {
  if (strcmp(name, "logs") == 0) return true;          // no RTC
  if (strlen(name) != 8) return false;                  // YYYYMMDD
  for (int i = 0; i < 8; i++)
    if (!isdigit((unsigned char)name[i])) return false;
  return true;
}

static bool isCsvName(const char *name) {
  size_t n = strlen(name);
  return strncmp(name, "spl_log_", 8) == 0 && n > 12 &&
         strcmp(name + n - 4, ".csv") == 0;
}

// openNextFile / close under sdMutex (directory handles stay open between).
static bool nextEntry(File &dir, File &out) {
  if (!sdAvailable) return false;
  MutexLock lock(sdMutex, SD_LOCK_MS);
  if (!lock) return false;
  out = dir.openNextFile();
  return (bool)out;
}

static File openDir(const char *path) {
  MutexLock lock(sdMutex, SD_LOCK_MS);
  if (!lock || !sdAvailable) return File();
  return SD_MMC.open(path);
}

static void closeEntry(File &f) {
  MutexLock lock(sdMutex);
  f.close();
}

// Find the oldest CSV (smallest path: names are dates/times) whose cursor is
// behind its size. `otherBytes` = unsent bytes in all the other files.
static bool pickNextFile(Target &t, const char *active, uint32_t &otherBytes) {
  char     best[PATH_LEN] = "";
  uint32_t bestOff = 0, bestLast = 0, bestPending = 0, total = 0;
  char     minFolder[16] = "";

  File root = openDir("/");
  if (!root) return false;

  File dir;
  while (nextEntry(root, dir)) {
    char folder[16] = "";
    bool use = dir.isDirectory() && strlen(dir.name()) < sizeof(folder);
    if (use) {
      strlcpy(folder, dir.name(), sizeof(folder));
      use = isDataFolder(folder) && strcmp(folder, doneBefore) >= 0;
    }
    if (!use) { closeEntry(dir); continue; }

    bool  folderPending = false;
    File  e;
    while (nextEntry(dir, e)) {
      char     path[PATH_LEN] = "";
      uint32_t size = 0;
      bool     csv  = !e.isDirectory() && isCsvName(e.name()) &&
                      strlen(e.path()) < sizeof(path);
      if (csv) {
        strlcpy(path, e.path(), sizeof(path));
        size = e.size();
      }
      closeEntry(e);
      if (!csv) continue;

      uint32_t off, last;
      readCursor(path, off, last);
      if (off >= size) continue;                        // fully sent

      folderPending = true;
      total += size - off;
      if (!best[0] || strcmp(path, best) < 0) {
        strlcpy(best, path, sizeof(best));
        bestOff = off; bestLast = last; bestPending = size - off;
      }
    }
    closeEntry(dir);
    if (folderPending && (!minFolder[0] || strcmp(folder, minFolder) < 0))
      strlcpy(minFolder, folder, sizeof(minFolder));
  }
  closeEntry(root);

  // Move the "done" mark up to the oldest folder with work left, but never
  // past the folder that is being written now.
  char activeFolder[16] = "";
  if (active[0] == '/') {
    const char *slash = strchr(active + 1, '/');
    if (slash && (size_t)(slash - active - 1) < sizeof(activeFolder))
      strlcpy(activeFolder, active + 1, slash - active);
  }
  const char *mark = minFolder[0] ? minFolder : activeFolder;
  if (activeFolder[0] && strcmp(activeFolder, mark) < 0) mark = activeFolder;
  if (mark[0]) strlcpy(doneBefore, mark, sizeof(doneBefore));

  otherBytes = total - bestPending;
  if (!best[0]) return false;

  strlcpy(t.path, best, sizeof(t.path));
  t.offset = bestOff;
  t.lastId = bestLast;
  t.hdrLen = 0;
  return true;
}

enum HdrResult : uint8_t { HDR_OK, HDR_WAIT, HDR_MISMATCH, HDR_SDERR };

// The file's first line must be today's CSV_HEADER: older files (other
// columns) would be rejected chunk by chunk with 400, so skip them whole.
static HdrResult checkHeader(const char *path, uint32_t &hdrLen) {
  char buf[sizeof(CSV_HDR) + 8];
  int n = sdReadAt(path, 0, (uint8_t *)buf, sizeof(buf) - 1);
  if (n < 0) return HDR_SDERR;
  char *nl = (char *)memchr(buf, '\n', n);
  if (!nl) return (n >= (int)sizeof(buf) - 1) ? HDR_MISMATCH : HDR_WAIT;

  size_t len = nl - buf;
  hdrLen = len + 1;
  if (len && buf[len - 1] == '\r') len--;
  if (len != CSV_HDR_LEN - 1 || memcmp(buf, CSV_HDR, len) != 0) return HDR_MISMATCH;
  return HDR_OK;
}


// =============================================================================
//  Chunk = complete lines [start, end) of one Session
// =============================================================================

struct Chunk {
  uint32_t start = 0, end = 0;
  uint32_t session = 0, first = 0, last = 0;   // X-Chunk-Id
  uint32_t lines = 0;                          // data rows (or bad lines if bad)
  bool     bad = false;                        // lines ingest would reject: skip locally
};

// Parses just enough of a row to build X-Chunk-Id and avoid a sure 400/403:
// Device_ID (field 0) == this device, Session (2) and Record_ID (3) integers.
struct LineParse {
  uint8_t  field = 0, devPos = 0;
  bool     devOk = true, numBad = false, sessSeen = false, recSeen = false;
  bool     content = false;                    // anything but '\r'
  uint32_t session = 0, record = 0;

  void feed(char c, const char *devId) {
    if (c == '\r') return;
    content = true;
    if (c == ',') {
      if (field == 0 && devId[devPos] != '\0') devOk = false;
      if (field < 255) field++;
      return;
    }
    switch (field) {
      case 0:
        if (devPos < SETTINGS_DEVID_LEN && devId[devPos] == c) devPos++;
        else devOk = false;
        break;
      case 2:
      case 3: {
        if (c < '0' || c > '9') { numBad = true; break; }
        uint32_t &v = (field == 2) ? session : record;
        if (v > 400000000UL) numBad = true;    // overflow guard
        v = v * 10 + (c - '0');
        (field == 2 ? sessSeen : recSeen) = true;
        break;
      }
      default: break;
    }
  }

  bool good() const { return field >= 4 && devOk && !numBad && sessSeen && recSeen; }
};

static uint8_t scanBuf[1024];

// Scan from `offset` up to `size` (only complete lines). Stops at maxRows,
// UPLOAD_CHUNK_MAX_BYTES, a new Session, or the first bad line after good
// ones. Lines that can't be sent at the start come back as one bad chunk.
static bool buildChunk(const char *path, uint32_t offset, uint32_t size,
                       uint32_t maxRows, Chunk &c) {
  c = Chunk();
  c.start = c.end = offset;

  LineParse lp;
  uint32_t  lineStart = offset;
  uint32_t  pos = offset;

  while (pos < size) {
    size_t want = size - pos;
    if (want > sizeof(scanBuf)) want = sizeof(scanBuf);
    int n = sdReadAt(path, pos, scanBuf, want);
    if (n <= 0) return false;

    for (int i = 0; i < n; i++) {
      char ch = (char)scanBuf[i];
      if (ch != '\n') { lp.feed(ch, settings.deviceId); continue; }

      uint32_t lineEnd = pos + i + 1;
      uint32_t lineLen = lineEnd - lineStart;

      if (!lp.content) {                       // blank line: just step over it
        c.end = lineEnd;
      } else if (!lp.good() || lineLen + CSV_HDR_LEN > UPLOAD_CHUNK_MAX_BYTES) {
        if (c.lines && !c.bad) return true;    // send the good rows first
        c.bad = true;
        c.lines++;
        c.end = lineEnd;
      } else {
        if (c.bad) return true;                // end of the bad run
        if (c.lines && lp.session != c.session) return true;
        if (c.lines && c.end - c.start + lineLen + CSV_HDR_LEN > UPLOAD_CHUNK_MAX_BYTES)
          return true;
        if (!c.lines) {
          c.session = lp.session;
          c.first = c.last = lp.record;
        }
        if (lp.record < c.first) c.first = lp.record;
        if (lp.record > c.last)  c.last  = lp.record;
        c.lines++;
        c.end = lineEnd;
        if (c.lines >= maxRows) return true;
      }
      lp = LineParse();
      lineStart = lineEnd;
    }
    pos += n;
  }
  return true;
}


// =============================================================================
//  HTTP body: CSV header, then the chunk's bytes read from SD in the pieces
//  HTTPClient asks for (~1.4 KB). No extra buffer (spike #4: a 16 KB buffer
//  can't be allocated after the TLS handshake).
// =============================================================================

class ChunkStream : public Stream {
public:
  ChunkStream(const char *path, uint32_t start, uint32_t end)
    : path_(path), start_(start), end_(end) {}

  size_t total() const { return CSV_HDR_LEN + (end_ - start_); }
  bool   sdFailed() const { return sdFailed_; }

  // Must return all bytes left: HTTPClient reads as much as available() says
  // (spike #4 bug: returning 1 sent one byte per TLS record).
  int available() override { return (int)(total() - served_); }
  int read() override {
    char b;
    return readBytes(&b, 1) == 1 ? (uint8_t)b : -1;
  }
  int peek() override { return -1; }
  size_t write(uint8_t) override { return 0; }

  size_t readBytes(char *out, size_t n) {
    size_t got = 0;
    while (got < n && served_ < total()) {
      if (served_ < CSV_HDR_LEN) {
        size_t k = min(n - got, CSV_HDR_LEN - served_);
        memcpy(out + got, CSV_HDR + served_, k);
        got += k; served_ += k;
        continue;
      }
      uint32_t off = start_ + (served_ - CSV_HDR_LEN);
      size_t   k   = min(n - got, (size_t)(end_ - off));
      int      r   = sdReadAt(path_, off, (uint8_t *)out + got, k);
      if (r <= 0) { sdFailed_ = true; break; }   // short body -> POST fails -> retry
      got += r; served_ += r;
    }
    return got;
  }

private:
  const char *path_;
  uint32_t    start_, end_;
  size_t      served_   = 0;
  bool        sdFailed_ = false;
};

// One POST. Returns the HTTP status, a negative HTTPClient error, or
// CODE_BAD_URL.
static int postChunk(const char *path, const Chunk &c) {
  char chunkId[40];
  snprintf(chunkId, sizeof(chunkId), "%lu-%lu-%lu", (unsigned long)c.session,
           (unsigned long)c.first, (unsigned long)c.last);

  bool https = strncmp(settings.ingestUrl, "https://", 8) == 0;
  WiFiClientSecure tls;
  WiFiClient       plain;
  if (https) {
#ifdef INGEST_ROOT_CA
    tls.setCACert(INGEST_ROOT_CA);
#else
    tls.setInsecure();   // no CA in secrets.h: encrypted, server not verified
#endif
    tls.setHandshakeTimeout(HTTP_TIMEOUT_MS / 1000);
  }

  HTTPClient http;
  http.setReuse(false);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.setTimeout(HTTP_TIMEOUT_MS);
  bool ok = https ? http.begin(tls, settings.ingestUrl)
                  : http.begin(plain, settings.ingestUrl);
  if (!ok) return CODE_BAD_URL;

  http.addHeader("Authorization", "Bearer " DEVICE_TOKEN);
  http.addHeader("X-Device-Id", settings.deviceId);
  http.addHeader("X-Chunk-Id", chunkId);
  http.addHeader("Content-Type", "text/csv");

  ChunkStream body(path, c.start, c.end);
  uint32_t t0   = millis();
  int      code = http.sendRequest("POST", &body, body.total());
  uint32_t ms   = millis() - t0;

  // Why the connection failed (DNS / TLS alloc / handshake), from mbedTLS
  char tlsErr[100] = "";
  if (code < 0 && https) {
    int e = tls.lastError(tlsErr, sizeof(tlsErr));
    if (!e) tlsErr[0] = '\0';
  }

  char reply[96] = "";
  if (code >= 300) {          // ingest answers {"error": "..."}: keep the start
    String s = http.getString();
    strlcpy(reply, s.c_str(), sizeof(reply));
  }
  http.end();

  LOG("UPL", "POST %s rows=%lu %luB code=%d%s%s %lums | heap %u maxBlk %u%s",
      chunkId, (unsigned long)c.lines, (unsigned long)body.total(), code,
      code < 0 ? " " : "", code < 0 ? HTTPClient::errorToString(code).c_str() : "",
      (unsigned long)ms, ESP.getFreeHeap(),
      (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
      body.sdFailed() ? " | SD read failed" : "");
  if (reply[0]) LOG("UPL", "reply: %s", reply);
  if (tlsErr[0]) LOG("UPL", "TLS error: %s", tlsErr);
  return code;
}


// =============================================================================
//  uploadTask
// =============================================================================

static void advance(Target &t, uint32_t end, uint32_t lastId) {
  t.offset = end;
  if (lastId) t.lastId = lastId;
  // Not saved = this range is sent again after a reboot: same chunk_id, so
  // ingest overwrites the same S3 objects (no duplicates).
  if (!writeCursor(t.path, t.offset, t.lastId))
    LOG("WARN", "UPL cursor not saved: %s", t.path);
}

static void stopUpload(const char *why, int code) {
  uploadStopped = true;
  LOG("ERROR", "UPL upload stopped until reboot (code=%d): %s", code, why);
}

void uploadTask(void *pv) {
  static Target t;
  static Chunk  pending;              // kept across retries: resend the same bytes
  bool     haveTarget  = false;
  bool     havePending = false;
  uint32_t chunkRows   = UPLOAD_CHUNK_ROWS;
  uint32_t backoffMs   = UPLOAD_BACKOFF_MIN_MS;
  uint32_t otherBytes  = 0;
  uint32_t rowBytes    = DEFAULT_ROW_BYTES;
  uint32_t lastOkMs    = millis();
  bool     offLogged   = false;

  for (;;) {
    if (uploadStopped) { sleepMs(60000); continue; }

    if (!settings.ingestUrl[0] || !DEVICE_TOKEN[0]) {
      if (!offLogged) LOG("UPL", "CSV upload off (ingest_url or DEVICE_TOKEN empty)");
      offLogged = true;
      sleepMs(60000);
      continue;
    }
    if (!netConnected || !sdAvailable) { sleepMs(UPLOAD_POLL_MS); continue; }

    char active[PATH_LEN];
    csvActivePath(active, sizeof(active));

    // ── Next file (oldest first) ───────────────────────────────────────────
    if (!haveTarget) {
      if (!pickNextFile(t, active, otherBytes)) {
        uploadBacklogRows = 0;
        sleepMs(UPLOAD_ACTIVE_POLL_MS);
        continue;
      }
      haveTarget  = true;
      havePending = false;
      LOG("UPL", "File %s from byte %lu (other files: %lu B left)", t.path,
          (unsigned long)t.offset, (unsigned long)otherBytes);
    }
    bool isActive = strcmp(t.path, active) == 0;

    int32_t size = sdFileSize(t.path);
    if (size < 0) {                   // SD busy / removed / file gone
      haveTarget = false;
      sleepMs(UPLOAD_POLL_MS);
      continue;
    }

    // ── Header line, once per file ─────────────────────────────────────────
    if (!t.hdrLen) {
      HdrResult h = checkHeader(t.path, t.hdrLen);
      if (h == HDR_SDERR) { sleepMs(UPLOAD_POLL_MS); continue; }
      if (h == HDR_WAIT && isActive) { t.hdrLen = 0; sleepMs(UPLOAD_POLL_MS); continue; }
      if (h != HDR_OK) {
        if (h == HDR_MISMATCH)
          logSkipped(t.path, 0, size, 0, 0, "CSV header differs from CSV_HEADER");
        advance(t, size, t.lastId);   // nothing to send from this file
        haveTarget = false;
        continue;
      }
      if (t.offset < t.hdrLen) t.offset = t.hdrLen;
    }

    uploadBacklogRows = (otherBytes + ((uint32_t)size > t.offset ? size - t.offset : 0)) / rowBytes;

    // ── Build the chunk (or reuse the one being retried) ──────────────────
    if (!havePending) {
      if (!buildChunk(t.path, t.offset, size, chunkRows, pending)) {
        sleepMs(UPLOAD_POLL_MS);
        continue;
      }
      if (pending.bad) {
        logSkipped(t.path, pending.start, pending.end, pending.lines, 0,
                   "bad row (Device_ID/Session/Record_ID) or line too long");
        advance(t, pending.end, t.lastId);
        continue;
      }
      if (!pending.lines) {
        if (pending.end > pending.start) { advance(t, pending.end, t.lastId); continue; }
        if (isActive) { sleepMs(UPLOAD_ACTIVE_POLL_MS); continue; }
        // Closed file and no complete line left: done. A cut-off last line
        // (power lost mid-row) is never sent.
        if (t.offset < (uint32_t)size)
          LOG("UPL", "%s: drop %lu B unfinished last line", t.path,
              (unsigned long)(size - t.offset));
        advance(t, size, t.lastId);
        LOG("UPL", "File done: %s", t.path);
        haveTarget = false;
        continue;
      }
      // The file being written: wait for a full chunk, unless nothing went
      // up for UPLOAD_PARTIAL_MS (slow CSV interval / end of a walk).
      if (isActive && pending.lines < chunkRows && millis() - lastOkMs < UPLOAD_PARTIAL_MS) {
        sleepMs(UPLOAD_ACTIVE_POLL_MS);
        continue;
      }
      havePending = true;
    }

    // ── POST ─────────────────────────────────────────────────────────────
    int code = postChunk(t.path, pending);
    uploadLastCode = code;

    if (code == 200) {
      advance(t, pending.end, pending.last);
      rowBytes = max<uint32_t>(1, (pending.end - pending.start) / pending.lines);
      uploadChunksOk++;
      havePending = false;
      backoffMs   = UPLOAD_BACKOFF_MIN_MS;
      lastOkMs    = millis();
      sleepMs(UPLOAD_CHUNK_GAP_MS);             // let realtime reconnect between chunks
    } else if (code == 413 && chunkRows > UPLOAD_MIN_CHUNK_ROWS) {
      chunkRows = max<uint32_t>(UPLOAD_MIN_CHUNK_ROWS, chunkRows / 2);
      havePending = false;
      LOG("UPL", "413 - chunk size now %lu rows", (unsigned long)chunkRows);
    } else if (code == 400 || code == 403 || code == 413) {
      logSkipped(t.path, pending.start, pending.end, pending.lines, code,
                 "rejected by ingest");
      advance(t, pending.end, pending.last);
      havePending = false;
    } else if (code == CODE_BAD_URL) {
      stopUpload("ingest_url not usable", code);
    } else if (code == 401) {
      stopUpload("token rejected - check DEVICE_TOKEN", code);
    } else if (code >= 300 && code < 500 && code != 429) {
      stopUpload("unexpected status - check ingest_url", code);
    } else {
      // 429 / 5xx / timeout / no connection: same chunk again after backoff
      LOG("UPL", "code=%d - resend in %lus", code, (unsigned long)(backoffMs / 1000));
      sleepMs(backoffMs);
      backoffMs = min<uint32_t>(backoffMs * 2, NET_BACKOFF_MAX_MS);
    }
  }
}
