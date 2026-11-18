#!/usr/bin/env python3
"""Pretend to be a board: ws:// to the hub, hello then a reading every 2 s.

  pip install "websockets>=14,<16"
  python fake_device.py ws://<eip>:8081/device <device token> --device IOTW-A001

Stops with Ctrl+C. Uses the same JSON as the firmware (spec 01 section 3).
"""
import argparse
import asyncio
import json
import random
import time

from websockets.asyncio.client import connect


async def main(a):
    headers = {"Authorization": f"Bearer {a.token}"}
    session, rec = random.randint(1, 9999), 0
    lat, lon = 13.8776, 100.5794
    async with connect(a.url, additional_headers=headers) as ws:
        await ws.send(json.dumps({"t": "hello", "device_id": a.device, "session": session,
                                  "fw": "fake_device"}))
        print("connected, sending every", a.interval, "s")
        while True:
            rec += 1
            lat += random.uniform(-1, 1) * 2e-5
            lon += random.uniform(-1, 1) * 2e-5
            spl = 60 + random.uniform(0, 15)
            msg = {"t": "reading", "device_id": a.device, "session": session, "record_id": rec,
                   "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "dba": round(spl - 6, 1), "db_spl": round(spl, 1),
                   "lux": round(random.uniform(200, 900), 1),
                   "temp_c": round(random.uniform(30, 33), 1),
                   "humidity_pct": round(random.uniform(55, 70), 1),
                   "lat": round(lat, 6), "lon": round(lon, 6), "gps_valid": 1,
                   "health": {"heap_free": 80000, "heap_max_block": 45000, "rssi": -60,
                              "backlog_rows": 0}}
            await ws.send(json.dumps(msg))
            await asyncio.sleep(a.interval)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("token")
    p.add_argument("--device", default="IOTW-A001")
    p.add_argument("--interval", type=float, default=2.0)
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        pass
