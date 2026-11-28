# cloud/relay

EC2 relay สำหรับบอร์ด ESP32 ที่ไม่มี RAM พอทำ TLS ระหว่างอัดเสียง (ตัดสินใจ 2026-11-17, สเปค 10 §10)

```
ESP32 --HTTP :8080--> EC2 relay.py --HTTPS--> API Gateway POST /ingest --> Lambda --> S3
```

- `relay.py`: Python stdlib ล้วน รับ `POST /ingest` แล้วส่ง body + header `Authorization`, `X-Device-Id`, `X-Chunk-Id`, `Content-Type` ต่อไป `UPSTREAM_URL` คืน status/body เดิมให้บอร์ด, ติดต่อ upstream ไม่ได้ = 502, `GET /health` = 200
- `iot-dt-relay.service`: systemd unit (อ่านค่าจาก `/etc/iot-dt-relay.env`)
- สร้าง EC2 ด้วย module `relay` ใน `cloud/infra` (user data ติดตั้งให้เอง)

**ข้อควรรู้:** ช่วงบอร์ด → EC2 ไม่เข้ารหัส (token วิ่งเป็น plain text) · ถ้า EC2 ล่ม บอร์ดส่งไม่ได้จนกว่าจะขึ้นใหม่ (ข้อมูลรอใน SD แล้ว backfill เอง) · Elastic IP ทำให้ IP ไม่เปลี่ยน

ทดสอบ:

```bash
curl http://<relay-ip>:8080/health
curl -i -X POST http://<relay-ip>:8080/ingest -H "Authorization: Bearer $TOKEN" \
  -H "X-Device-Id: IOTW-A001" -H "X-Chunk-Id: 1-1-1" -H "Content-Type: text/csv" --data-binary @chunk.csv
aws ssm start-session --target <relay_instance_id>   # ไม่มี SSH (R5): เข้าผ่าน Session Manager (หรือ console EC2 > Connect > Session Manager)
sudo journalctl -u iot-dt-relay -f      # บน EC2
```

รันในเครื่อง: `UPSTREAM_URL=https://xxxx.execute-api.ap-southeast-2.amazonaws.com/ingest python3 relay.py`

เทสต์ (ไม่ต่อเน็ต: เปิด upstream ปลอมบน localhost):

```
pip install -r cloud/relay/requirements-dev.txt
python -m pytest -q cloud/relay/tests
```
