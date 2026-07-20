#!/usr/bin/env bash
# Write /etc/nginx/haitun-ai-secret.conf from remote-ai/config.env (root-only).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$ROOT/config.env}"
SECRET_FILE="${2:-/etc/nginx/haitun-ai-secret.conf}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy config.env.example and set PSI_AI_API_KEY" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${PSI_AI_API_KEY:-}" ]]; then
  echo "PSI_AI_API_KEY is empty in $ENV_FILE" >&2
  exit 1
fi

# Escape characters that break nginx double-quoted strings
KEY_ESC="${PSI_AI_API_KEY//\\/\\\\}"
KEY_ESC="${KEY_ESC//\"/\\\"}"

umask 077
TMP="$(mktemp)"
cat >"$TMP" <<EOF
# Generated from haitun-ai config.env — do not commit. chmod 600.
set \$haitun_ai_authorization "Bearer ${KEY_ESC}";
EOF
sudo mv "$TMP" "$SECRET_FILE"
sudo chmod 600 "$SECRET_FILE"
sudo chown root:root "$SECRET_FILE"
echo "Wrote $SECRET_FILE"
