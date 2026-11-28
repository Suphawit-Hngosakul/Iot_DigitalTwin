# cloud/realtime-hub (issue #10, #23)

WebSocket hub รับค่าสดจากบอร์ดแล้วกระจายให้หน้าเว็บ (สเปค 22, D22) รันบน **EC2 ตัวเดียวกับ relay** คนละ process

```
ESP32 ── ws://<eip>:8081/device  (Authorization: Bearer <device token>) ──► hub.py ──► ws://<eip>:8081/live?device=<id|all> ── เว็บ
ESP32 ── http://<eip>:8080/ingest ──► relay.py ── HTTPS ──► API Gateway /ingest   (ของ #7 เหมือนเดิม)
```

- ไม่มี TLS เพราะบอร์ดไม่มี RAM พอทำ TLS ระหว่างอัด WAV (D20) — token วิ่ง plain text เหมือน relay
- ตรวจ token ด้วย sha256 map ชุดเดียวกับ ingest (`DEVICE_TOKENS` = `{"<sha256>": "<device_id>"}`), token ผิด → HTTP 401 ตอน upgrade
- ไม่เก็บข้อมูลถาวร เก็บแค่ค่าล่าสุดต่อเครื่องในหน่วยความจำ (ข้อมูลจริงขึ้นทาง CSV)
- เว็บต่อแล้วได้ `status` + `reading` ล่าสุดทันที ตามด้วยข้อความใหม่; ไม่มีข้อความ 30 วินาที → `{"t":"status","online":false}`

| ไฟล์ | อะไร |
|---|---|
| `hub.py` | ตัว hub (Python `websockets`, asyncio) |
| `iot-dt-hub.service` | systemd unit (อ่านค่าจาก `/etc/iot-dt-hub.env`) |
| `live.html` | หน้าทดสอบ เปิดจากเครื่องได้เลย: `live.html?hub=ws://<eip>:8081/live` |
| `fake_device.py` | ตัวส่งจำลองแทนบอร์ด |
| `tests/` | pytest (เปิด server จริงบน port ว่าง) |

สร้าง EC2 ด้วย module `relay` ใน `cloud/infra` (`hub_enabled = true` เป็นค่าเริ่ม) — user data ติดตั้ง venv + `websockets` ให้เอง

## ทดสอบ

```bash
# ในเครื่อง
pip install -r requirements-dev.txt
python -m pytest -q tests

DEVICE_TOKENS="{\"$(python -c 'import hashlib;print(hashlib.sha256(b"tok-a").hexdigest())')\": \"IOTW-A001\"}" python hub.py
python fake_device.py ws://127.0.0.1:8081/device tok-a --device IOTW-A001
# เปิด live.html?hub=ws://127.0.0.1:8081/live

# บน AWS
curl http://<eip>:8081/healthz
python fake_device.py ws://<eip>:8081/device <token ของ IOTW-A001>
sudo journalctl -u iot-dt-hub -f      # เข้า EC2 ผ่าน SSM: aws ssm start-session --target <relay_instance_id>
```

**เว็บ HTTPS ทีหลัง (#44):** หน้า HTTPS ต่อ `ws://` ไม่ได้ (mixed content) → ใส่ Caddy + โดเมน (DuckDNS) หน้า `/live` เป็น `wss://` ส่วนบอร์ดยังใช้ `ws://` ที่ 8081
