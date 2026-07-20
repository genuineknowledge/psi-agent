#!/usr/bin/env bash
# Install Nginx site for Haitun default AI (reverse proxy only — no app runtime).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f config.env ]]; then
  echo "Missing config.env — cp config.env.example config.env && edit PSI_AI_API_KEY" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source config.env
set +a

HOST="${HAITUN_AI_HOST:-haitun.addchess.cn}"
UPSTREAM="${HAITUN_UPSTREAM:-https://open.bigmodel.cn/api/paas/v4}"
# normalize: no trailing slash for sed replacement of example upstream host path
UPSTREAM="${UPSTREAM%/}"

bash "$ROOT/scripts/write-secret.sh" "$ROOT/config.env"

TMP="$(mktemp)"
sed \
  -e "s|haitun.addchess.cn|${HOST}|g" \
  -e "s|https://open.bigmodel.cn/api/paas/v4|${UPSTREAM}|g" \
  "$ROOT/nginx-haitun-ai.conf.example" >"$TMP"

SITE=haitun-ai
sudo mv "$TMP" "/etc/nginx/sites-available/${SITE}"
sudo ln -sf "/etc/nginx/sites-available/${SITE}" "/etc/nginx/sites-enabled/${SITE}"
sudo nginx -t
sudo systemctl reload nginx

echo "Installed ${HOST} → ${UPSTREAM}/ (key via /etc/nginx/haitun-ai-secret.conf)"
echo "Next: sudo certbot --nginx -d ${HOST}"
