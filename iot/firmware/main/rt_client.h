#ifndef RT_CLIENT_H
#define RT_CLIENT_H

#include <Arduino.h>

// =============================================================================
//  Realtime to the hub (issue #6, spec 10 §5, spec 01 §3, decision D22)
//
//  rtTask (core 0, priority 0, not on the watchdog) keeps one plain WebSocket
//  open to settings.wssUrl = "ws://<eip>:8081/device" (the hub runs on the
//  same EC2 as the CSV relay). No TLS: this board has no RAM for it while
//  recording (D20). Small client written on WiFiClient: no extra library.
//
//  - Upgrade with "Authorization: Bearer DEVICE_TOKEN" (same token as #7).
//  - On connect: {"t":"hello",...}; then {"t":"reading",...} every
//    RT_INTERVAL_MS with sensor values + health. Best-effort: a message that
//    cannot be sent is dropped, nothing is queued.
//  - Answers ping with pong; logs audio_on / audio_off (spec 90 later).
//  - No WiFi / connect failed / dropped / nothing heard for RT_IDLE_TIMEOUT_MS
//    -> reconnect with backoff RT_BACKOFF_MIN_MS doubling up to
//    NET_BACKOFF_MAX_MS.
//  - HTTP 401 (bad token), wss:// or a bad URL -> realtime stops until reboot.
//  - Never touches the SD card or sdMutex.
//
//  Off when settings.wssUrl or DEVICE_TOKEN is empty.
//  Status: rtConnected, rtSent, rtReconnects, rtStopped (globals.h).
// =============================================================================

void rtTask(void *pv);

#endif // RT_CLIENT_H
