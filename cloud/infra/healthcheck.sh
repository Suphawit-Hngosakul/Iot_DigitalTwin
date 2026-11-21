#!/usr/bin/env bash
# ตรวจสุขภาพระบบหลัง terraform apply (รันใน cloud/infra บน CloudShell หรือเครื่องที่มี aws + terraform)
#   bash healthcheck.sh            # ตรวจทุกอย่าง
#   bash healthcheck.sh --no-gee   # ข้ามการเรียก GEE (satellite-test)
# ผล: PASS / WARN (ยังไม่พร้อมแต่ไม่ใช่ความผิดพลาด เช่น ยังไม่มีข้อมูล) / FAIL ; exit 1 ถ้ามี FAIL
set -uo pipefail
cd "$(dirname "$0")"

GEE=1; [ "${1:-}" = "--no-gee" ] && GEE=0
PASS=0; WARN=0; FAIL=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; WARN=$((WARN+1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
out()  { terraform output -raw "$1" 2>/dev/null || true; }
http() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" || echo 000; }

echo "== 1. AWS + Terraform"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  && pass "credentials ใช้ได้ (account $ACCOUNT)" || { fail "aws credentials ใช้ไม่ได้ (Learner Lab หมด session?)"; exit 1; }
BUCKET=$(out bucket)
[ -n "$BUCKET" ] && pass "terraform output อ่านได้" || { fail "ไม่มี terraform output (ยังไม่ apply หรือ state หาย)"; exit 1; }
PREFIX=iot-dt

echo "== 2. Storage"
aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1 && pass "bucket $BUCKET" || fail "bucket $BUCKET ไม่มี/เข้าไม่ได้"

echo "== 3. Ingest + relay + hub"
INGEST=$(out ingest_url)
c=$(http -X POST "$INGEST"); [ "$c" = 401 ] && pass "ingest ตอบ 401 เมื่อไม่มี token (Lambda ทำงาน)" || fail "ingest ตอบ $c (คาด 401) $INGEST"
RELAY=$(out relay_url)
if [ -n "$RELAY" ]; then
  c=$(http "${RELAY%/ingest}/health"); [ "$c" = 200 ] && pass "relay /health" || fail "relay /health ตอบ $c (EC2 หยุด หรือ service ยังไม่ขึ้น? รอ 2-3 นาทีหลัง apply)"
else warn "relay ปิดอยู่ (relay_enabled = false)"; fi
HUB=$(out hub_device_url)
if [ -n "$HUB" ]; then
  H=${HUB#ws://}; H=${H%/device}
  body=$(curl -s --max-time 10 "http://$H/healthz" || true)
  if echo "$body" | grep -q '"ok": *true'; then
    echo "$body" | grep -q 'IOTW' && pass "hub /healthz มีบอร์ดออนไลน์: $body" || warn "hub ทำงาน แต่ยังไม่มีบอร์ดต่อเข้ามา ($body)"
  else fail "hub /healthz ใช้ไม่ได้ ($H)"; fi
else warn "hub ปิดอยู่ (hub_enabled = false)"; fi

echo "== 4. API ของเว็บ"
API=$(out api_url)
body=$(curl -s --max-time 15 "$API/devices" || true)
echo "$body" | grep -q 'IOTW' && pass "API /devices" || fail "API /devices ไม่ได้รายชื่ออุปกรณ์: ${body:0:200}"

echo "== 5. Lambda"
for f in $(aws lambda list-functions --query "Functions[?starts_with(FunctionName,'$PREFIX-')].FunctionName" --output text); do
  st=$(aws lambda get-function --function-name "$f" --query 'Configuration.[State,LastUpdateStatus]' --output text)
  [ "$st" = "Active	Successful" ] && pass "$f" || fail "$f ($st)"
done

echo "== 6. Step Functions (รันล่าสุด)"
for sm in $(aws stepfunctions list-state-machines --query "stateMachines[?starts_with(name,'$PREFIX-')].stateMachineArn" --output text); do
  name=${sm##*:}
  last=$(aws stepfunctions list-executions --state-machine-arn "$sm" --max-results 1 --query 'executions[0].[status,startDate]' --output text)
  case "$last" in
    SUCCEEDED*) pass "$name ล่าสุด $last" ;;
    RUNNING*)   pass "$name กำลังรัน" ;;
    None*|"")   warn "$name ยังไม่เคยรัน" ;;
    *)          fail "$name ล่าสุด $last (ดู console ของ Step Functions)" ;;
  esac
done

echo "== 7. ตั้งเวลา + แจ้งเตือน"
while read -r n s; do
  [ -z "$n" ] && continue
  [ "$s" = ENABLED ] && pass "schedule $n" || warn "schedule $n = $s"
done < <(aws scheduler list-schedules --name-prefix "$PREFIX" --query 'Schedules[].[Name,State]' --output text)
for r in $(aws events list-rules --name-prefix "$PREFIX" --query 'Rules[].Name' --output text); do
  s=$(aws events describe-rule --name "$r" --query State --output text)
  [ "$s" = ENABLED ] && pass "rule $r" || warn "rule $r = $s"
done
TOPIC=$(aws sns list-topics --query "Topics[?ends_with(TopicArn,':$PREFIX-iot-pipeline-alerts')].TopicArn" --output text)
if [ -n "$TOPIC" ]; then
  subs=$(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" --query 'Subscriptions[].SubscriptionArn' --output text)
  if echo "$subs" | grep -q PendingConfirmation; then warn "อีเมล SNS ยังไม่ได้กดยืนยัน"
  elif [ -n "$subs" ]; then pass "อีเมล SNS ยืนยันแล้ว"
  else warn "SNS ไม่มีผู้รับ (alert_email ว่าง)"; fi
else fail "ไม่พบ SNS topic $PREFIX-iot-pipeline-alerts"; fi

echo "== 8. Glue + Athena"
DB=$(out athena_database); WG=$(out athena_workgroup)
n=$(aws glue get-tables --database-name "$DB" --query 'length(TableList)' --output text 2>/dev/null || echo 0)
[ "${n:-0}" -gt 0 ] && pass "Glue $DB มี $n ตาราง" || fail "Glue $DB ไม่มีตาราง"
QID=$(aws athena start-query-execution --work-group "$WG" --query-execution-context Database="$DB" \
      --query-string "SELECT 1" --query QueryExecutionId --output text 2>/dev/null)
st=QUEUED
for _ in $(seq 1 20); do
  st=$(aws athena get-query-execution --query-execution-id "$QID" --query QueryExecution.Status.State --output text 2>/dev/null)
  case "$st" in SUCCEEDED|FAILED|CANCELLED) break ;; esac; sleep 1
done
[ "$st" = SUCCEEDED ] && pass "Athena workgroup $WG query ได้" || fail "Athena query = $st"

echo "== 9. ดาวเทียม (GEE)"
SAT=$(out satellite_function_name)
if [ -z "$SAT" ]; then warn "ดาวเทียมปิดอยู่ (satellite_enabled = false)"
elif [ "$GEE" = 0 ]; then warn "ข้ามการทดสอบ GEE (--no-gee)"
else
  aws lambda invoke --function-name "$SAT" --cli-read-timeout 90 --payload '{}' --cli-binary-format raw-in-base64-out /tmp/gee-test.json >/dev/null 2>&1
  if grep -q '"status": *"ok"' /tmp/gee-test.json 2>/dev/null; then pass "GEE ใช้ได้: $(cat /tmp/gee-test.json)"
  else fail "GEE ใช้ไม่ได้ (WIF ฝั่ง Google ยังไม่รู้จัก account $ACCOUNT?): $(head -c 300 /tmp/gee-test.json 2>/dev/null)"; fi
fi

echo "== 10. ข้อมูลใน bucket"
count() { aws s3 ls "s3://$BUCKET/$1" --recursive 2>/dev/null | wc -l | tr -d ' '; }
for p in raw/csv/ trusted/iot_readings_1s/ curated/readings_1min/ raw/satellite/ trusted/satellite_grid/ curated/cell_period/; do
  n=$(count "$p"); [ "$n" -gt 0 ] && pass "$p มี $n ไฟล์" || warn "$p ยังว่าง"
done
q=$(count quarantine/); [ "$q" -gt 0 ] && warn "quarantine/ มี $q ไฟล์ (ข้อมูลที่ไม่ผ่านการตรวจ ดูสาเหตุใน validation/)"

echo
echo "สรุป: PASS $PASS  WARN $WARN  FAIL $FAIL"
[ "$FAIL" -eq 0 ]
