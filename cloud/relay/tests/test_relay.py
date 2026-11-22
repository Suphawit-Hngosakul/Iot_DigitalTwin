"""relay.py: HTTP -> upstream forwarder (stdlib only, no network beyond localhost)."""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import relay


class Upstream(BaseHTTPRequestHandler):
    """Fake ingest: records the request, answers what the test asked for."""
    seen: list = []
    answer = (200, b'{"rows": 1}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Upstream.seen.append((self.path, dict(self.headers), body))
        code, out = Upstream.answer
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def servers(monkeypatch):
    up = _serve(Upstream)
    monkeypatch.setattr(relay, "UPSTREAM", f"http://127.0.0.1:{up.server_port}/ingest")
    Upstream.seen, Upstream.answer = [], (200, b'{"rows": 1}')
    rl = _serve(relay.Handler)
    yield f"http://127.0.0.1:{rl.server_port}"
    rl.shutdown()
    up.shutdown()


def request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


HEADERS = {"Authorization": "Bearer tok", "X-Device-Id": "IOTW-A001", "X-Chunk-Id": "1-1-2",
           "Content-Type": "text/csv", "X-Other": "dropped"}


def test_health(servers):
    assert request(servers + "/health") == (200, b'{"ok": true}')
    assert request(servers + "/nope")[0] == 404


def test_forwards_body_and_only_whitelisted_headers(servers):
    code, out = request(servers + "/ingest", b"a,b\n1,2\n", HEADERS)
    assert (code, json.loads(out)) == (200, {"rows": 1})
    (path, headers, body), = Upstream.seen
    assert path == "/ingest" and body == b"a,b\n1,2\n"
    assert headers["Authorization"] == "Bearer tok" and headers["X-Chunk-Id"] == "1-1-2"
    assert "X-Other" not in headers


def test_upstream_status_is_passed_back(servers):
    Upstream.answer = (403, b'{"error": "X-Device-Id does not match token"}')
    code, out = request(servers + "/ingest", b"x", HEADERS)
    assert code == 403 and b"does not match" in out


def test_too_large_and_wrong_path(servers, monkeypatch):
    monkeypatch.setattr(relay, "MAX_BODY", 4)
    assert request(servers + "/ingest", b"12345", HEADERS)[0] == 413
    assert request(servers + "/other", b"1", HEADERS)[0] == 404
    assert Upstream.seen == []


def test_unreachable_upstream_is_502(servers, monkeypatch):
    monkeypatch.setattr(relay, "UPSTREAM", "http://127.0.0.1:9/ingest")
    assert request(servers + "/ingest", b"x", HEADERS)[0] == 502
