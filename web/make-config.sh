#!/usr/bin/env bash
set -euo pipefail
api=$(terraform output -raw api_url 2>/dev/null || true)
live=$(terraform output -raw hub_live_url 2>/dev/null || true)
[ "$live" = "null" ] && live=""
deploy_id=$(date -u +"%Y%m%dT%H%M%SZ")
out="${OUT:-$(dirname "$0")/public/config.json}"
mkdir -p "$(dirname "$out")"
cat > "$out" <<JSON
{
  "api_url": "${api}",
  "hub_live_url": "${live}",
  "deploy_id": "${deploy_id}"
}
JSON
echo "wrote $out (deploy_id=${deploy_id})"