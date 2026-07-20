#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/config.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/config.env"
  set +a
fi

HOST="${HAITUN_AI_HOST:-haitun.addchess.cn}"

echo "==> DNS ${HOST}"
getent hosts "$HOST" 2>/dev/null || host "$HOST" 2>/dev/null || nslookup "$HOST" || true

echo "==> POST https://${HOST}/chat/completions"
code="$(curl -sS -o /tmp/haitun-ai-hc.json -w '%{http_code}' \
  -X POST "https://${HOST}/chat/completions" \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer haitun-default' \
  -d '{"model":"glm-4-flash","messages":[{"role":"user","content":"ping"}],"stream":false}' \
  || echo "000")"
echo "HTTP $code"
head -c 400 /tmp/haitun-ai-hc.json 2>/dev/null || true
echo
