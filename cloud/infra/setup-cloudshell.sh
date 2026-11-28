#!/usr/bin/env bash
# เตรียม AWS CloudShell ให้พร้อมรัน terraform (รันซ้ำได้ ไม่แตะ state หรือ resource ใดๆ)
#   cd ~ && git clone <repo> && bash iot_digitaltwin/cloud/infra/setup-cloudshell.sh && source ~/.bashrc
# ทำ: ติดตั้ง Terraform ลง ~/bin, ตั้ง PATH + TF_DATA_DIR ใน ~/.bashrc, เช็กเครื่องมือ/credentials/ตำแหน่ง repo/พื้นที่
set -uo pipefail
TF_VERSION=${TF_VERSION:-1.9.8}
MIN_FREE_MB=300
HERE=$(cd "$(dirname "$0")" && pwd)
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; WARNED=1; }
die()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }
WARNED=0

echo "== Terraform $TF_VERSION"
if [ -x "$HOME/bin/terraform" ] && "$HOME/bin/terraform" version | head -1 | grep -q "v$TF_VERSION\$"; then
  ok "มีอยู่แล้วที่ ~/bin/terraform"
else
  tmp=$(mktemp -d)                                  # ดาวน์โหลดใน /tmp ไม่กินพื้นที่ home
  curl -fsSL -o "$tmp/tf.zip" \
    "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" \
    || die "ดาวน์โหลด Terraform ไม่ได้"
  unzip -oq "$tmp/tf.zip" terraform -d "$tmp" || die "แตก zip ไม่ได้"
  mkdir -p "$HOME/bin" && mv "$tmp/terraform" "$HOME/bin/terraform" && chmod +x "$HOME/bin/terraform"
  rm -rf "$tmp"
  ok "ติดตั้งแล้ว: $("$HOME/bin/terraform" version | head -1)"
fi
rm -f "$HOME"/terraform_*_linux_amd64.zip           # zip ที่เคยดาวน์โหลดค้างไว้ใน home

echo "== ~/.bashrc"
add_line() { grep -qxF "$1" "$HOME/.bashrc" 2>/dev/null || { echo "$1" >> "$HOME/.bashrc"; ok "เพิ่ม: $1"; return; }; ok "มีแล้ว: $1"; }
add_line 'export PATH="$HOME/bin:$PATH"'
add_line 'export TF_DATA_DIR=/tmp/tf-data'           # provider (~650 MB) ไว้ใน /tmp เพราะ home มีแค่ 1 GB

echo "== เครื่องมือ"
for c in aws python3 git curl unzip; do
  command -v "$c" >/dev/null && ok "$c" || die "ไม่มีคำสั่ง $c"
done
python3 -m pip --version >/dev/null 2>&1 && ok "pip" || warn "ไม่มี pip (ต้องใช้ตอน build GEE layer ถ้า satellite_enabled = true)"

echo "== AWS credentials"
acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  && ok "account $acct region ${AWS_REGION:-${AWS_DEFAULT_REGION:-?}}" \
  || warn "credentials ใช้ไม่ได้ (ดูขั้น 1 ใน cloud/infra/README.md)"

echo "== ตำแหน่ง repo"
case "$HERE" in
  /tmp/*) warn "repo อยู่ใน /tmp ($HERE) จะหายพร้อม terraform.tfstate เมื่อ session เริ่มใหม่ ให้ clone ใหม่ใน home" ;;
  "$HOME"/*) ok "$HERE (อยู่ใน home)" ;;
  *) warn "repo อยู่นอก home ($HERE)" ;;
esac
[ -f "$HERE/terraform.tfvars" ] && ok "มี terraform.tfvars" || warn "ยังไม่มี terraform.tfvars: cp terraform.tfvars.example terraform.tfvars แล้วแก้ค่า"
[ -f "$HERE/terraform.tfstate" ] && ok "มี terraform.tfstate (เคย apply แล้ว)" || echo "  --   ยังไม่มี terraform.tfstate (ปกติถ้ายังไม่เคย apply จากโฟลเดอร์นี้)"

echo "== พื้นที่ home"
free=$(df -Pm "$HOME" | awk 'NR==2{print $4}')
[ "${free:-0}" -ge "$MIN_FREE_MB" ] && ok "เหลือ ${free} MB" \
  || warn "เหลือ ${free} MB (< ${MIN_FREE_MB} MB) ลบ ~/.cache, .terraform/, cloud/satellite/layer/build/ ก่อน apply"

echo
[ "$WARNED" = 0 ] && echo "พร้อมแล้ว" || echo "พร้อม แต่มี WARN ด้านบน"
echo "ต่อไป: source ~/.bashrc && cd $HERE && terraform init && terraform apply"
