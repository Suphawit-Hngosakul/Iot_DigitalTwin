#!/usr/bin/env bash
# สร้าง Lambda layer (gee-layer.zip) ไว้ข้างสคริปต์นี้ ให้ Terraform อ่าน
# (ไฟล์ .zip ถูก gitignore จึงต้องสร้างเองบนเครื่อง/CloudShell ที่จะ terraform apply)
# Python 3.12 / x86_64 — ดาวน์โหลด wheel ของ Linux โดยตรง ไม่ขึ้นกับ OS เครื่องที่รัน
set -euo pipefail
cd "$(dirname "$0")"

rm -rf build gee-layer.zip
mkdir -p build/python

pip install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target build/python \
  earthengine-api google-auth requests

find build/python -type d -name "__pycache__" -prune -exec rm -rf {} +
(cd build && zip -r9q ../gee-layer.zip python)
rm -rf build

ls -lh gee-layer.zip
