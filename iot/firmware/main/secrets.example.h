// Copy this file to secrets.h (same folder) and fill in your WiFi + token.
// secrets.h is git-ignored, so passwords never get committed.
// WiFi values are only defaults: values saved from the Setup Mode page win.
#pragma once

#define WIFI_SSID_1 "your-wifi-1"
#define WIFI_PASS_1 "password-1"
#define WIFI_SSID_2 "your-phone-hotspot"
#define WIFI_PASS_2 "password-2"

// Token the device sends to ingest / presign / realtime hub (issue #5-#7)
#define DEVICE_TOKEN "change-me"

// Server URLs (optional here; the Setup page can set them too).
// `terraform output relay_url` in cloud/infra: plain HTTP to the EC2 relay,
// because this board has no RAM for TLS while recording (cloud/relay).
// `terraform output ingest_url` (https) only works on a board with PSRAM.
#define INGEST_URL "http://x.x.x.x:8080/ingest"
// Realtime hub on the same EC2 (#6, D22): `terraform output hub_device_url`
#define WSS_URL "ws://x.x.x.x:8081/device"

// Optional: verify the ingest server certificate (issue #7). Without it the
// upload is still encrypted but the server is not checked (setInsecure).
// API Gateway (execute-api) chains to Amazon Root CA 1:
// https://www.amazontrust.com/repository/AmazonRootCA1.pem
/*
#define INGEST_ROOT_CA \
  "-----BEGIN CERTIFICATE-----\n" \
  "MIID...\n" \
  "... every PEM line in quotes, ending with \\n ...\n" \
  "-----END CERTIFICATE-----\n"
*/
