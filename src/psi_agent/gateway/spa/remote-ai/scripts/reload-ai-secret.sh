#!/usr/bin/env bash
# After editing config.env PSI_AI_API_KEY, regenerate the Nginx secret and reload.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$ROOT/scripts/write-secret.sh" "$ROOT/config.env"
sudo nginx -t
sudo systemctl reload nginx
echo "Secret reloaded."
