#!/usr/bin/env bash
set -euo pipefail
SITE=haitun-ai
sudo rm -f "/etc/nginx/sites-enabled/${SITE}"
sudo rm -f "/etc/nginx/sites-available/${SITE}"
sudo rm -f /etc/nginx/haitun-ai-secret.conf
sudo nginx -t
sudo systemctl reload nginx
echo "Removed ${SITE}"
