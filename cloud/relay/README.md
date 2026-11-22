# cloud/relay

EC2 relay สำหรับบอร์ด ESP32 ที่ไม่มี RAM พอทำ TLS ระหว่างอัดเสียง (ตัดสินใจ 2026-11-17, สเปค 10 §10)

```
ESP32 --HTTP :8080--> EC2 relay.py --HTTPS--> API Gateway POST /ingest --> Lambda --> S3
```

- `relay.py`: Python stdlib ล้วน รับ `POST /ingest` แล้วส่ง body + header `Authorization`, `X-Device-Id`, `X-Chunk-Id`, `Content-Type` ต่อไป `UPSTREAM_URL` คืน status/body เดิมให้บอร์ด, ติดต่อ upstream ไม่ได้ = 502, `GET /health` = 200
- `iot-dt-relay.service`: systemd unit (อ่านค่าจาก `/etc/iot-dt-relay.env`)
- สร้าง EC2 ด้วย module `relay` ใน `cloud/infra` (user data ติดตั้งให้เอง)

**ข้อควรรู้:** ช่วงบอร์ด → EC2 ไม่เข้ารหัส (token วิ่งเป็น plain text) · Learner Lab หยุด EC2 เมื่อหมด session ต้องเปิด lab ใหม่ก่อนบอร์ดจะส่งได้ (ข้อมูลรอใน SD) · Elastic IP ทำให้ IP ไม่เปลี่ยน

ทดสอบ:

```bash
curl http://<relay-ip>:8080/health
curl -i -X POST http://<relay-ip>:8080/ingest -H "Authorization: Bearer $TOKEN" \
  -H "X-Device-Id: IOTW-A001" -H "X-Chunk-Id: 1-1-1" -H "Content-Type: text/csv" --data-binary @chunk.csv
sudo journalctl -u iot-dt-relay -f      # บน EC2: ตั้ง relay_ssh_cidrs = ["18.206.107.24/29"] ใน tfvars ก่อน แล้วใช้ EC2 Instance Connect ในหน้า console
```

รันในเครื่อง: `UPSTREAM_URL=https://xxxx.execute-api.us-east-1.amazonaws.com/ingest python3 relay.py`

เทสต์ (ไม่ต่อเน็ต: เปิด upstream ปลอมบน localhost):

```
pip install -r cloud/relay/requirements-dev.txt
python -m pytest -q cloud/relay/tests
```
