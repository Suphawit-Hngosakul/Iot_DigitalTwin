#include "rt_client.h"
#include "globals.h"
#include "rtc_helper.h"
#include "sd_logger.h"
#include "settings.h"

#include <WiFi.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <mbedtls/base64.h>


static const size_t   HOST_LEN       = 64;
static const size_t   PATH_LEN       = 96;
static const size_t   TX_MAX         = 640;   // one reading is ~420 bytes
static const size_t   RX_MAX         = 128;   // hub -> board messages are tiny
static const uint32_t READ_WAIT_MS   = 1000;  // rest of a frame after its header
static const uint32_t LOOP_MS        = 50;
static const int      CONNECT_UNAUTHORIZED = 401;

enum : uint8_t { OP_CONT = 0x0, OP_TEXT = 0x1, OP_BIN = 0x2, OP_CLOSE = 0x8,
                 OP_PING = 0x9, OP_PONG = 0xA };

static WiFiClient cli;
static uint8_t    txBuf[TX_MAX + 8];       // frame header + masked payload
static char       msgBuf[TX_MAX];          // JSON before framing
static uint8_t    rxBuf[RX_MAX + 1];
static uint32_t   lastRxMs = 0;

static void sleepMs(uint32_t ms) { vTaskDelay(pdMS_TO_TICKS(ms)); }


// =============================================================================
//  URL: "ws://host[:port]/path"
// =============================================================================

static bool parseUrl(const char *url, char *host, uint16_t &port, char *path) {
  if (strncmp(url, "ws://", 5) != 0) return false;
  const char *h = url + 5;
  const char *slash = strchr(h, '/');
  const char *hostEnd = slash ? slash : h + strlen(h);
  const char *colon = (const char *)memchr(h, ':', hostEnd - h);
  const char *nameEnd = colon ? colon : hostEnd;
  size_t n = nameEnd - h;
  if (n == 0 || n >= HOST_LEN) return false;
  memcpy(host, h, n);
  host[n] = '\0';
  port = 80;
  if (colon) {
    long p = strtol(colon + 1, nullptr, 10);
    if (p <= 0 || p > 65535) return false;
    port = (uint16_t)p;
  }
  if (!slash) slash = "/";
  if (strlen(slash) >= PATH_LEN) return false;
  strcpy(path, slash);
  return true;
}


// =============================================================================
//  Socket helpers
// =============================================================================

// Read exactly n bytes within READ_WAIT_MS. buf == nullptr -> discard.
static bool readExact(uint8_t *buf, size_t n) {
  uint32_t start = millis();
  size_t got = 0;
  while (got < n) {
    int avail = cli.available();
    if (avail > 0) {
      size_t want = n - got;
      if (!buf) {
        uint8_t tmp[32];
        int r = cli.read(tmp, want < sizeof(tmp) ? want : sizeof(tmp));
        if (r > 0) got += r;
      } else {
        int r = cli.read(buf + got, want);
        if (r > 0) got += r;
      }
      continue;
    }
    if (!cli.connected() || millis() - start > READ_WAIT_MS) return false;
    sleepMs(5);
  }
  return true;
}

// One header line (without CRLF) within RT_CONNECT_TIMEOUT_MS.
static bool readLine(char *out, size_t len, uint32_t start) {
  size_t n = 0;
  for (;;) {
    if (cli.available() > 0) {
      int c = cli.read();
      if (c == '\n') { out[n] = '\0'; return true; }
      if (c != '\r' && n + 1 < len) out[n++] = (char)c;
      continue;
    }
    if (!cli.connected() || millis() - start > RT_CONNECT_TIMEOUT_MS) return false;
    sleepMs(5);
  }
}

static void wsClose() {
  if (cli.connected()) cli.stop();
  if (rtConnected) LOG("RT", "disconnected");
  rtConnected = false;
}


// =============================================================================
//  WebSocket (RFC 6455) — client side: every frame we send is masked
// =============================================================================

static bool wsSend(uint8_t opcode, const uint8_t *data, size_t len) {
  if (len > TX_MAX) return false;
  size_t h = 0;
  txBuf[h++] = 0x80 | opcode;                       // FIN + opcode
  if (len < 126) {
    txBuf[h++] = 0x80 | (uint8_t)len;
  } else {
    txBuf[h++] = 0x80 | 126;
    txBuf[h++] = (uint8_t)(len >> 8);
    txBuf[h++] = (uint8_t)(len & 0xFF);
  }
  uint32_t key = esp_random();
  uint8_t *mask = txBuf + h;
  memcpy(mask, &key, 4);
  h += 4;
  for (size_t i = 0; i < len; i++) txBuf[h + i] = data[i] ^ mask[i & 3];
  size_t total = h + len;
  return cli.write(txBuf, total) == total;
}

static bool wsSendText(const char *s) {
  return wsSend(OP_TEXT, (const uint8_t *)s, strlen(s));
}

// Returns 101 on success, the HTTP status the hub answered with, or -1.
static int wsConnect(const char *host, uint16_t port, const char *path) {
  if (!cli.connect(host, port, RT_CONNECT_TIMEOUT_MS)) return -1;
  cli.setNoDelay(true);

  uint8_t rnd[16];
  for (int i = 0; i < 16; i += 4) { uint32_t r = esp_random(); memcpy(rnd + i, &r, 4); }
  unsigned char key[32];
  size_t keyLen = 0;
  mbedtls_base64_encode(key, sizeof(key), &keyLen, rnd, sizeof(rnd));
  key[keyLen] = '\0';

  int n = snprintf(msgBuf, sizeof(msgBuf),
                   "GET %s HTTP/1.1\r\n"
                   "Host: %s:%u\r\n"
                   "Upgrade: websocket\r\n"
                   "Connection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\n"
                   "Authorization: Bearer " DEVICE_TOKEN "\r\n"
                   "\r\n",
                   path, host, (unsigned)port, (const char *)key);
  if (n <= 0 || (size_t)n >= sizeof(msgBuf) ||
      cli.write((const uint8_t *)msgBuf, n) != (size_t)n) {
    cli.stop();
    return -1;
  }

  // Status line "HTTP/1.1 101 Switching Protocols", then headers up to "".
  uint32_t start = millis();
  char line[128];
  if (!readLine(line, sizeof(line), start)) { cli.stop(); return -1; }
  int status = 0;
  if (sscanf(line, "HTTP/%*s %d", &status) != 1) { cli.stop(); return -1; }
  if (status != 101) { cli.stop(); return status; }
  do {
    if (!readLine(line, sizeof(line), start)) { cli.stop(); return -1; }
  } while (line[0]);

  lastRxMs = millis();
  return 101;
}

static void handleText(const char *s) {
  if (strstr(s, "\"audio_on\""))       LOG("RT", "audio_on (live audio comes with spec 90)");
  else if (strstr(s, "\"audio_off\"")) LOG("RT", "audio_off");
}

// Read every frame waiting in the socket. false = connection is gone.
static bool wsPoll() {
  while (cli.available() >= 2) {
    uint8_t hdr[2];
    if (!readExact(hdr, 2)) return false;
    uint8_t op     = hdr[0] & 0x0F;
    bool    masked = hdr[1] & 0x80;
    uint32_t len   = hdr[1] & 0x7F;
    if (len == 126) {
      uint8_t ext[2];
      if (!readExact(ext, 2)) return false;
      len = ((uint32_t)ext[0] << 8) | ext[1];
    } else if (len == 127) {
      return false;                                 // never sent by the hub
    }
    uint8_t mask[4] = {0, 0, 0, 0};
    if (masked && !readExact(mask, 4)) return false;

    bool keep = len <= RX_MAX;
    if (!readExact(keep ? rxBuf : nullptr, len)) return false;
    lastRxMs = millis();
    if (!keep) continue;                            // too big for us: ignored
    if (masked) for (uint32_t i = 0; i < len; i++) rxBuf[i] ^= mask[i & 3];
    rxBuf[len] = '\0';

    switch (op) {
      case OP_TEXT:  handleText((const char *)rxBuf); break;
      case OP_PING:  if (!wsSend(OP_PONG, rxBuf, len)) return false; break;
      case OP_CLOSE: {
        uint16_t code = len >= 2 ? (uint16_t)((rxBuf[0] << 8) | rxBuf[1]) : 1005;
        LOG("RT", "hub closed the connection (%u)", (unsigned)code);
        wsSend(OP_CLOSE, rxBuf, len >= 2 ? 2 : 0);
        return false;
      }
      default: break;                               // pong / binary / continuation
    }
  }
  return cli.connected();
}


// =============================================================================
//  Messages (spec 01 §3)
// =============================================================================

// Append ,"key":value with `dec` decimals, or null when not valid.
static size_t addNum(char *b, size_t n, size_t cap, const char *key, bool ok,
                     double v, int dec) {
  if (n >= cap) return n;
  int w = ok ? snprintf(b + n, cap - n, ",\"%s\":%.*f", key, dec, v)
             : snprintf(b + n, cap - n, ",\"%s\":null", key);
  return w > 0 ? n + (size_t)w : cap;
}

static bool sendHello() {
  int n = snprintf(msgBuf, sizeof(msgBuf),
                   "{\"t\":\"hello\",\"device_id\":\"%s\",\"device_type\":\"%s\","
                   "\"session\":%lu,\"fw\":\"%s\"}",
                   settings.deviceId, deviceTypeName(settings.deviceType),
                   (unsigned long)sessionId(), FW_VERSION);
  return n > 0 && (size_t)n < sizeof(msgBuf) && wsSendText(msgBuf);
}

static bool sendReading() {
  GPSData g; LuxData l; DHTData d;
  readGPSSafe(g); readLuxSafe(l); readDHTSafe(d);
  bool splOk = splValid;
  uint32_t recordId = 0, sdErrors = 0;
  csvStatus(recordId, sdErrors);
  char ts[DATETIME_BUF_SIZE];
  bool tsOk = getDateTimeStrChecked(ts, sizeof(ts));

  const size_t cap = sizeof(msgBuf);
  int w = snprintf(msgBuf, cap,
                   "{\"t\":\"reading\",\"device_id\":\"%s\",\"session\":%lu,"
                   "\"record_id\":%lu",
                   settings.deviceId, (unsigned long)sessionId(), (unsigned long)recordId);
  if (w <= 0) return false;
  size_t n = (size_t)w;
  if (n < cap) {
    w = tsOk ? snprintf(msgBuf + n, cap - n, ",\"ts\":\"%s\"", ts)
             : snprintf(msgBuf + n, cap - n, ",\"ts\":null");
    n = w > 0 ? n + (size_t)w : cap;
  }
  n = addNum(msgBuf, n, cap, "dba",          splOk,   currentSPLA, 1);
  n = addNum(msgBuf, n, cap, "db_spl",       splOk,   currentSPL,  1);
  n = addNum(msgBuf, n, cap, "lux",          l.valid, l.value,     1);
  n = addNum(msgBuf, n, cap, "temp_c",       d.valid, d.temp,      1);
  n = addNum(msgBuf, n, cap, "humidity_pct", d.valid, d.humidity,  1);
  n = addNum(msgBuf, n, cap, "lat",          g.valid, g.lat,       6);
  n = addNum(msgBuf, n, cap, "lon",          g.valid, g.lng,       6);
  if (n < cap) {
    w = snprintf(msgBuf + n, cap - n,
                 ",\"gps_valid\":%d,\"health\":{\"heap_free\":%u,\"heap_max_block\":%u,"
                 "\"rssi\":%d,\"sd_errors\":%lu,\"backlog_rows\":%lu,\"backlog_wav\":0,"
                 "\"audio_drops\":%lu,\"rt_reconnects\":%lu}}",
                 g.valid ? 1 : 0, (unsigned)ESP.getFreeHeap(),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
                 (int)netRssi, (unsigned long)sdErrors,
                 (unsigned long)uploadBacklogRows, (unsigned long)audioDropCount,
                 (unsigned long)rtReconnects);
    n = w > 0 ? n + (size_t)w : cap;
  }
  if (n >= cap) {
    LOG("WARN", "RT reading too long, dropped");
    return true;   // keep the connection, just skip this one
  }
  return wsSendText(msgBuf);
}


// =============================================================================
//  Task
// =============================================================================

static void stopRt(const char *why) {
  rtStopped = true;
  wsClose();
  LOG("ERROR", "RT realtime stopped until reboot: %s", why);
}

void rtTask(void *pv) {
  char     host[HOST_LEN];
  char     path[PATH_LEN];
  uint16_t port      = 0;
  bool     offLogged = false;
  bool     parsed    = false;
  uint32_t backoffMs = RT_BACKOFF_MIN_MS;
  uint32_t nextTryMs = 0;
  uint32_t lastSendMs = 0;
  bool     everConnected = false;

  for (;;) {
    if (rtStopped) { sleepMs(60000); continue; }

    if (!settings.wssUrl[0] || !DEVICE_TOKEN[0]) {
      if (!offLogged) LOG("RT", "realtime off (wss_url or DEVICE_TOKEN empty)");
      offLogged = true;
      sleepMs(60000);
      continue;
    }
    if (!parsed) {
      if (!strncmp(settings.wssUrl, "wss://", 6)) {
        stopRt("wss:// needs TLS (no RAM on this board) - use ws://<eip>:8081/device");
        continue;
      }
      if (!parseUrl(settings.wssUrl, host, port, path)) {
        stopRt("bad wss_url (expected ws://host:port/path)");
        continue;
      }
      parsed = true;
    }

    if (!netConnected) {
      if (cli.connected() || rtConnected) wsClose();
      sleepMs(1000);
      continue;
    }

    // ── Connect (backoff between tries) ─────────────────────────────────────
    if (!rtConnected) {
      if ((int32_t)(millis() - nextTryMs) < 0) { sleepMs(200); continue; }
      uint32_t t0 = millis();
      int code = wsConnect(host, port, path);
      if (code == CONNECT_UNAUTHORIZED) { stopRt("token rejected (401) - check DEVICE_TOKEN"); continue; }
      if (code != 101 || !sendHello()) {
        if (code == 101) cli.stop();
        LOG("RT", "connect %s:%u failed (%d), retry in %lus", host, (unsigned)port, code,
            (unsigned long)(backoffMs / 1000));
        nextTryMs = millis() + backoffMs;
        backoffMs = backoffMs * 2 > NET_BACKOFF_MAX_MS ? NET_BACKOFF_MAX_MS : backoffMs * 2;
        continue;
      }
      rtConnected = true;
      if (everConnected) rtReconnects++;
      everConnected = true;
      backoffMs = RT_BACKOFF_MIN_MS;
      lastSendMs = millis() - RT_INTERVAL_MS;        // first reading right away
      LOG("RT", "connected to %s:%u%s in %lums | Heap:%u MaxBlk:%u", host, (unsigned)port,
          path, (unsigned long)(millis() - t0), ESP.getFreeHeap(),
          (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    }

    // ── Connected: read, send, watch for a silent hub ───────────────────────
    bool ok = wsPoll();
    if (ok && millis() - lastSendMs >= RT_INTERVAL_MS) {
      lastSendMs = millis();
      if (sendReading()) rtSent++;
      else ok = false;
    }
    if (ok && millis() - lastRxMs > RT_IDLE_TIMEOUT_MS) {
      LOG("RT", "nothing from the hub for %lus", (unsigned long)(RT_IDLE_TIMEOUT_MS / 1000));
      ok = false;
    }
    if (!ok) {
      wsClose();
      nextTryMs = millis() + backoffMs;
      continue;
    }
    sleepMs(LOOP_MS);
  }
}
