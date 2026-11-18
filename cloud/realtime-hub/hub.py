#!/usr/bin/env python3
"""Realtime hub (spec 22, decision D22).

Boards open a plain ws:// connection (no TLS: no RAM for it while recording,
D20) to /device and send JSON text frames (spec 01 section 3). Web pages open
/live?device=<id|all> and get the latest reading of each device straight away,
then every new one. Nothing is stored: the real data goes up as CSV (#7).

Runs on the same EC2 as cloud/relay (separate process, port 8081).

Config by environment:
  DEVICE_TOKENS      {"<sha256 hex of token>": "<device_id>"} - same map as ingest
  PORT               listen port (default 8081)
  OFFLINE_AFTER_SEC  no message for this long -> status offline (default 30)
  PING_SEC           ping interval and timeout (default 20)
  LIVE_QUEUE         messages kept per slow web client (default 50)
  MAX_MSG_BYTES      largest frame accepted from a device (default 4096)
"""
from __future__ import annotations

import asyncio
import hashlib
import http
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("hub")

ALL = "all"
CLOSE_REPLACED = 4000  # same device connected again


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_token_map(raw: str) -> Dict[str, str]:
    try:
        m = json.loads(raw or "{}")
    except json.JSONDecodeError:
        log.error("DEVICE_TOKENS is not valid JSON")
        return {}
    return {str(k).lower(): str(v) for k, v in m.items()}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_response(status: http.HTTPStatus, body: dict) -> Response:
    data = json.dumps(body).encode()
    headers = Headers([("Content-Type", "application/json"),
                       ("Content-Length", str(len(data))),
                       ("Connection", "close")])
    return Response(status.value, status.phrase, headers, data)


class Listener:
    """One web client. A bounded queue so a slow browser drops its own
    oldest messages instead of blocking the devices or other listeners."""

    def __init__(self, ws: ServerConnection, device: str, maxlen: int):
        self.ws = ws
        self.device = device
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxlen)
        self.dropped = 0

    def wants(self, device_id: str) -> bool:
        return self.device == ALL or self.device == device_id

    def push(self, msg: str) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(msg)


class Hub:
    def __init__(self, tokens: Dict[str, str], offline_after: float = 30,
                 live_queue: int = 50):
        self.tokens = tokens
        self.offline_after = offline_after
        self.live_queue = live_queue
        self.devices: Dict[str, ServerConnection] = {}
        self.latest: Dict[str, str] = {}      # device_id -> last reading (JSON text)
        self.hello: Dict[str, dict] = {}
        self.last_seen: Dict[str, float] = {}  # monotonic
        self.online: Dict[str, bool] = {}
        self.listeners: Set[Listener] = set()

    # ── HTTP stage (before the upgrade) ─────────────────────────────────────
    def device_for(self, authorization: Optional[str]) -> Optional[str]:
        scheme, _, token = (authorization or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return None
        return self.tokens.get(hash_token(token))

    def process_request(self, conn: ServerConnection, request: Request):
        path = urlsplit(request.path).path
        if path == "/healthz":
            return _json_response(http.HTTPStatus.OK, {
                "ok": True,
                "devices": sorted(d for d, on in self.online.items() if on),
                "listeners": len(self.listeners),
            })
        if path == "/device":
            dev = self.device_for(request.headers.get("Authorization"))
            if dev is None:
                log.warning("device %s: bad or missing token", conn.remote_address)
                return _json_response(http.HTTPStatus.UNAUTHORIZED,
                                      {"error": "missing or unknown device token"})
            conn.device_id = dev  # type: ignore[attr-defined]
            return None
        if path == "/live":
            return None
        return _json_response(http.HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ── fan-out ─────────────────────────────────────────────────────────────
    def publish(self, device_id: str, msg: str) -> None:
        for lis in self.listeners:
            if lis.wants(device_id):
                lis.push(msg)

    def status_msg(self, device_id: str) -> str:
        return json.dumps({"t": "status", "device_id": device_id,
                           "online": self.online.get(device_id, False), "at": utc_now()})

    def set_online(self, device_id: str, online: bool) -> None:
        if self.online.get(device_id) == online:
            return
        self.online[device_id] = online
        log.info("%s %s", device_id, "online" if online else "offline")
        self.publish(device_id, self.status_msg(device_id))

    def check_offline(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        for dev, seen in list(self.last_seen.items()):
            if self.online.get(dev) and now - seen >= self.offline_after:
                self.set_online(dev, False)

    # ── device side ─────────────────────────────────────────────────────────
    def handle_device_message(self, device_id: str, raw) -> Optional[str]:
        """Returns the text forwarded to listeners, or None if dropped."""
        if isinstance(raw, (bytes, bytearray)):
            return None  # audio frames come with spec 90 (#23)
        try:
            msg = json.loads(raw)
        except ValueError:
            log.warning("%s: not JSON, dropped", device_id)
            return None
        if not isinstance(msg, dict):
            return None
        if msg.setdefault("device_id", device_id) != device_id:
            log.warning("%s: message for %r dropped", device_id, msg.get("device_id"))
            return None

        self.last_seen[device_id] = time.monotonic()
        self.set_online(device_id, True)

        kind = msg.get("t")
        if kind == "hello":
            self.hello[device_id] = msg
            log.info("%s hello %s", device_id, raw)
            return None
        if kind != "reading":
            return None
        msg["received_at"] = utc_now()
        out = json.dumps(msg, separators=(",", ":"))
        self.latest[device_id] = out
        self.publish(device_id, out)
        return out

    async def device_session(self, ws: ServerConnection) -> None:
        dev = ws.device_id  # type: ignore[attr-defined]
        old = self.devices.get(dev)
        self.devices[dev] = ws
        if old is not None:
            log.info("%s connected again, closing the old connection", dev)
            await old.close(CLOSE_REPLACED, "replaced")
        log.info("%s connected from %s", dev, ws.remote_address)
        try:
            async for raw in ws:
                self.handle_device_message(dev, raw)
        except ConnectionClosed:
            pass
        finally:
            if self.devices.get(dev) is ws:
                del self.devices[dev]
            log.info("%s disconnected (%s)", dev, ws.close_code)

    # ── web side ────────────────────────────────────────────────────────────
    async def live_session(self, ws: ServerConnection) -> None:
        q = parse_qs(urlsplit(ws.request.path).query)
        device = (q.get("device") or [ALL])[0] or ALL
        lis = Listener(ws, device, self.live_queue)
        for dev, text in self.latest.items():
            if lis.wants(dev):
                lis.push(self.status_msg(dev))
                lis.push(text)
        self.listeners.add(lis)
        log.info("live %s watching %s (%d listeners)", ws.remote_address, device,
                 len(self.listeners))

        async def drain_incoming():  # web -> hub unused until spec 90
            async for _ in ws:
                pass

        reader = asyncio.ensure_future(drain_incoming())
        try:
            while not reader.done():
                get = asyncio.ensure_future(lis.queue.get())
                done, _ = await asyncio.wait({get, reader},
                                             return_when=asyncio.FIRST_COMPLETED)
                if get in done:
                    await ws.send(get.result())
                else:
                    get.cancel()
        except ConnectionClosed:
            pass
        finally:
            reader.cancel()
            self.listeners.discard(lis)
            if lis.dropped:
                log.info("live %s dropped %d messages (slow)", ws.remote_address, lis.dropped)

    async def handler(self, ws: ServerConnection) -> None:
        if urlsplit(ws.request.path).path == "/device":
            await self.device_session(ws)
        else:
            await self.live_session(ws)

    async def offline_loop(self, period: float = 1.0) -> None:
        while True:
            await asyncio.sleep(period)
            self.check_offline()


async def run(hub: Hub, host: str, port: int, ping: float, max_size: int,
              ready: Optional[asyncio.Future] = None) -> None:
    async with serve(hub.handler, host, port,
                     process_request=hub.process_request,
                     ping_interval=ping, ping_timeout=ping,
                     max_size=max_size) as server:
        checker = asyncio.ensure_future(hub.offline_loop())
        if ready is not None:
            ready.set_result(server)
        try:
            await server.serve_forever()
        finally:
            checker.cancel()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    tokens = load_token_map(os.environ.get("DEVICE_TOKENS", "{}"))
    port = int(os.environ.get("PORT", "8081"))
    hub = Hub(tokens,
              offline_after=float(os.environ.get("OFFLINE_AFTER_SEC", "30")),
              live_queue=int(os.environ.get("LIVE_QUEUE", "50")))
    log.info("hub :%d, %d device tokens", port, len(tokens))
    asyncio.run(run(hub, "0.0.0.0", port,
                    ping=float(os.environ.get("PING_SEC", "20")),
                    max_size=int(os.environ.get("MAX_MSG_BYTES", "4096"))))


if __name__ == "__main__":
    main()
