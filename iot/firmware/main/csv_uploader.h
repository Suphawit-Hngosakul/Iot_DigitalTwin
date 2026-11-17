#ifndef CSV_UPLOADER_H
#define CSV_UPLOADER_H

#include <Arduino.h>

// =============================================================================
//  CSV upload to ingest (issue #7, spec 10 §6, spec 20)
//
//  uploadTask (core 0, lowest priority, not on the watchdog) sends every
//  "/<YYYYMMDD|logs>/spl_log_*.csv" on the SD card to settings.ingestUrl,
//  oldest file first (backfill), then follows the file being written.
//
//  - Cursor "<csv>.idx" = "<byte_offset>,<last_record_id>", written as
//    ".idx.tmp" then renamed. It only moves after HTTP 200.
//  - Chunk = CSV header + up to UPLOAD_CHUNK_ROWS complete lines (cut at '\n',
//    one Session per chunk), X-Chunk-Id "<session>-<first>-<last>".
//    A retry resends exactly the same bytes (same chunk_id -> same S3 key).
//  - SD is read in small pieces; sdMutex is never held during network I/O.
//  - This board (no PSRAM) has no RAM for TLS while recording, so
//    ingestUrl is plain http:// to the EC2 relay (cloud/relay).
//
//  Ingest status (spec 20 §2):
//    200            -> advance cursor
//    400 / 403      -> log + skip the chunk (range kept in /upload_skipped.csv)
//    413            -> halve the chunk (down to UPLOAD_MIN_CHUNK_ROWS)
//    429 / 5xx / timeout / no connection -> backoff, resend the same chunk
//    401, other 3xx/4xx, bad URL -> stop uploading until reboot (config error)
//
//  Off when settings.ingestUrl or DEVICE_TOKEN is empty.
//  Status for health: upload* globals (globals.h).
// =============================================================================

void uploadTask(void *pv);

#endif // CSV_UPLOADER_H
