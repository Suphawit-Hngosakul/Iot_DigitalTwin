"""Hub tests: a real server on a free port, real websocket clients."""
import asyncio
import json
import urllib.request

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

import hub as hubmod

TOK_A, TOK_B = "tok-a", "tok-b"
TOKENS = {hubmod.hash_token(TOK_A): "IOTW-A001", hubmod.hash_token(TOK_B): "IOTW-A002"}


def reading(dev=None, **kw):
    m = {"t": "reading", "session": 1, "record_id": 5, "db_spl": 70.1}
    if dev:
        m["device_id"] = dev
    m.update(kw)
    return json.dumps(m)


def run(coro_fn, offline_after=30.0, live_queue=50):
    async def main():
        h = hubmod.Hub(TOKENS, offline_after=offline_after, live_queue=live_queue)
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(hubmod.run(h, "127.0.0.1", 0, ping=20, max_size=4096, ready=ready))
        server = await ready
        port = next(iter(server.sockets)).getsockname()[1]
        try:
            await asyncio.wait_for(coro_fn(h, port), 10)
        finally:
            task.cancel()
    asyncio.run(main())


def dev_conn(port, token):
    return connect(f"ws://127.0.0.1:{port}/device",
                   additional_headers={"Authorization": f"Bearer {token}"})


async def recv_json(ws, timeout=3):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def recv_t(ws, t, timeout=3):
    while True:
        m = await recv_json(ws, timeout)
        if m.get("t") == t:
            return m


def test_unit_token_and_message_rules():
    h = hubmod.Hub(TOKENS)
    assert h.device_for(f"Bearer {TOK_A}") == "IOTW-A001"
    assert h.device_for(f"bearer {TOK_B}") == "IOTW-A002"
    for bad in (None, "", "Bearer", "Bearer nope", f"Basic {TOK_A}"):
        assert h.device_for(bad) is None
    assert h.handle_device_message("IOTW-A001", "not json") is None
    assert h.handle_device_message("IOTW-A001", b"\x00\x01") is None
    assert h.handle_device_message("IOTW-A001", reading("IOTW-A002")) is None
    assert h.handle_device_message("IOTW-A001", '{"t":"hello","fw":"x"}') is None
    assert h.hello["IOTW-A001"]["fw"] == "x"
    out = json.loads(h.handle_device_message("IOTW-A001", reading()))
    assert out["device_id"] == "IOTW-A001" and "received_at" in out
    assert h.online["IOTW-A001"] is True


def test_unit_offline_after_timeout():
    h = hubmod.Hub(TOKENS, offline_after=30)
    h.handle_device_message("IOTW-A001", reading())
    seen = h.last_seen["IOTW-A001"]
    h.check_offline(seen + 29)
    assert h.online["IOTW-A001"] is True
    h.check_offline(seen + 30)
    assert h.online["IOTW-A001"] is False


def test_unit_slow_listener_drops_oldest():
    lis = hubmod.Listener(ws=None, device="all", maxlen=3)
    for i in range(5):
        lis.push(str(i))
    assert lis.dropped == 2
    assert [lis.queue.get_nowait() for _ in range(3)] == ["2", "3", "4"]


def test_bad_token_rejected_401():
    async def body(h, port):
        with pytest.raises(InvalidStatus) as e:
            async with dev_conn(port, "wrong"):
                pass
        assert e.value.response.status_code == 401
        with pytest.raises(InvalidStatus) as e:
            async with connect(f"ws://127.0.0.1:{port}/device"):
                pass
        assert e.value.response.status_code == 401
        with pytest.raises(InvalidStatus) as e:
            async with connect(f"ws://127.0.0.1:{port}/nope"):
                pass
        assert e.value.response.status_code == 404
    run(body)


def test_healthz():
    async def body(h, port):
        def get():
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:
                return r.status, json.loads(r.read())
        status, data = await asyncio.get_running_loop().run_in_executor(None, get)
        assert status == 200 and data["ok"] is True
    run(body)


def test_live_gets_latest_then_new_and_filters_by_device():
    async def body(h, port):
        async with dev_conn(port, TOK_A) as a, dev_conn(port, TOK_B) as b:
            await a.send('{"t":"hello","fw":"t"}')
            await a.send(reading(record_id=1))
            await b.send(reading(record_id=1))
            await asyncio.sleep(0.2)
            async with connect(f"ws://127.0.0.1:{port}/live?device=IOTW-A001") as web, \
                       connect(f"ws://127.0.0.1:{port}/live?device=all") as web_all:
                st = await recv_json(web)
                assert st == {**st, "t": "status", "device_id": "IOTW-A001", "online": True}
                first = await recv_t(web, "reading")
                assert first["record_id"] == 1 and first["device_id"] == "IOTW-A001"

                await b.send(reading(record_id=2))      # not for `web`
                await a.send(reading(record_id=2))
                nxt = await recv_t(web, "reading")
                assert (nxt["device_id"], nxt["record_id"]) == ("IOTW-A001", 2)

                seen = set()
                while len(seen) < 4:
                    m = await recv_t(web_all, "reading")
                    seen.add((m["device_id"], m["record_id"]))
                assert seen == {("IOTW-A001", 1), ("IOTW-A002", 1), ("IOTW-A001", 2), ("IOTW-A002", 2)}
    run(body)


def test_spoofed_device_id_not_forwarded():
    async def body(h, port):
        async with connect(f"ws://127.0.0.1:{port}/live") as web, dev_conn(port, TOK_A) as a:
            await a.send(reading("IOTW-A002", record_id=9))
            await a.send(reading(record_id=10))
            m = await recv_t(web, "reading")
            assert (m["device_id"], m["record_id"]) == ("IOTW-A001", 10)
    run(body)


def test_reconnect_replaces_old_connection():
    async def body(h, port):
        old = await dev_conn(port, TOK_A)
        await old.send(reading())
        async with dev_conn(port, TOK_A) as new:
            await asyncio.wait_for(old.wait_closed(), 3)
            assert old.close_code == hubmod.CLOSE_REPLACED
            await new.send(reading(record_id=7))
            await asyncio.sleep(0.2)
            assert json.loads(h.latest["IOTW-A001"])["record_id"] == 7
            assert h.devices["IOTW-A001"] is not None
    run(body)


def test_status_offline_when_device_goes_quiet():
    async def body(h, port):
        async with connect(f"ws://127.0.0.1:{port}/live") as web:
            async with dev_conn(port, TOK_A) as a:
                await a.send(reading())
                assert (await recv_t(web, "status"))["online"] is True
                await recv_t(web, "reading")
            st = await recv_t(web, "status", timeout=4)
            assert st["online"] is False and st["device_id"] == "IOTW-A001"
    run(body, offline_after=1.0)
