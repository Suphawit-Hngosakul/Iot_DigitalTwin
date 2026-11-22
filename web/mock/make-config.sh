#!/usr/bin/env bash
# สร้าง web/mock/config.js จาก terraform output (รันในเครื่องที่มี terraform state เช่น CloudShell)
#   cd cloud/infra && bash ../../web/mock/make-config.sh
# แล้วคัดลอก config.js ไปไว้ข้าง index.html (ไฟล์นี้อยู่ใน .gitignore)
# หรือไม่ต้องใช้ไฟล์: กดปุ่ม "API จริง ⚙" บนหน้าเว็บ แล้ววางค่าจาก
#   terraform output -raw api_url
#   terraform output -raw hub_live_url
set -euo pipefail
api=$(terraform output -raw api_url 2>/dev/null || true)
live=$(terraform output -raw hub_live_url 2>/dev/null || true)
[ "$live" = "null" ] && live=""
out="${OUT:-$(dirname "$0")/config.js}"
cat > "$out" <<JS
// สร้างจาก terraform output เมื่อ $(date -u +%Y-%m-%dT%H:%M:%SZ)
window.APP_CONFIG = { API_URL: "${api}", LIVE_URL: "${live}" };
JS
echo "wrote $out"
cat "$out"
