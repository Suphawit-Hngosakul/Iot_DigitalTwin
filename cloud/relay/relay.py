#!/usr/bin/env python3
"""HTTP -> HTTPS relay for the ESP32 uploader (decision 2026-11-17).

The board (ESP32-D0WD-V3, no PSRAM) has no RAM left for a TLS handshake
while it records audio, so it POSTs plain HTTP here and this relay forwards
the same body and headers to the ingest API over HTTPS. Status codes and
replies are passed back unchanged, so the firmware status table still holds.

Stdlib only. Config by environment:
  UPSTREAM_URL      https://xxxx.execute-api.ap-southeast-2.amazonaws.com/ingest (required)
  PORT              listen port (default 8080)
  MAX_BODY          largest body accepted in bytes (default 65536)
  UPSTREAM_TIMEOUT  seconds (default 20)
"""
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["UPSTREAM_URL"]
PORT = int(os.environ.get("PORT", "8080"))
MAX_BODY = int(os.environ.get("MAX_BODY", "65536"))
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "20"))

# Only these headers go upstream (no Host / hop-by-hop headers).
FORWARD = ("Authorization", "X-Device-Id", "X-Chunk-Id", "Content-Type")

JSON = "application/json"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "iot-dt-relay"

    def _send(self, code, body, ctype=JSON):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b'{"ok": true}')
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/ingest":
            return self._send(404, b'{"error": "not found"}')
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._send(411, b'{"error": "content-length required"}')
        if n < 0 or n > MAX_BODY:
            return self._send(413, b'{"error": "body too large"}')
        body = self.rfile.read(n)
        if len(body) != n:
            return self._send(400, b'{"error": "body shorter than content-length"}')

        headers = {k: self.headers[k] for k in FORWARD if self.headers.get(k)}
        req = urllib.request.Request(UPSTREAM, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                code, out, ctype = r.status, r.read(), r.headers.get("Content-Type", JSON)
        except urllib.error.HTTPError as e:
            code, out, ctype = e.code, e.read(), e.headers.get("Content-Type", JSON)
        except Exception as e:  # DNS, timeout, TLS: the board backs off on 5xx
            logging.warning("upstream failed: %s", e)
            code, out, ctype = 502, b'{"error": "upstream unreachable"}', JSON

        logging.info("%s device=%s chunk=%s %dB -> %d", self.client_address[0],
                     self.headers.get("X-Device-Id", "-"),
                     self.headers.get("X-Chunk-Id", "-"), n, code)
        self._send(code, out, ctype)

    def log_message(self, fmt, *args):  # one line per POST is logged above
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("relay :%d -> %s", PORT, UPSTREAM)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
