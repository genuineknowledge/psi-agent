#!/bin/bash
# Roll HaiTun Agent.app back to the pre-update bundle.
#
# Counterpart to the Windows rollback.ps1 / rollback.cmd pair. Users reach it the
# same way: run it manually after a bad update. macOS has a single component, so
# where the Windows script switches on last_update in {app, msys, all} this one
# only ever restores the app bundle.
#
# Shipped inside Resources/ (not next to the .app) because the bundle must stay
# read-only for its signature to hold; the state file it reads lives in
# Application Support alongside everything else the app writes at runtime.
set -uo pipefail

SUPPORT_ROOT="$HOME/Library/Application Support/Haitun"
STATE_FILE="$SUPPORT_ROOT/rollback-state.json"
APP_PATH="${HAITUN_APP_PATH:-/Applications/HaiTun Agent.app}"
BACKUP_PATH="$APP_PATH.backup"

if [ ! -f "$STATE_FILE" ]; then
    printf '没有可回滚的更新。\n'
    exit 0
fi

# Deliberately not parsing JSON with a dependency: python3 is present on macOS
# but not guaranteed to be the one the app was built against, and the two fields
# we need are flat. grep keeps this script dependency-free like rollback.ps1.
status="$(sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE_FILE" | head -1)"
last_update="$(sed -n 's/.*"last_update"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE_FILE" | head -1)"

case "$status" in
    pending|done) ;;
    *)
        printf '没有可回滚的更新。\n'
        exit 0
        ;;
esac

if [ -z "$last_update" ]; then
    printf '没有可回滚的更新记录。\n'
    exit 0
fi

if [ ! -d "$BACKUP_PATH" ]; then
    # Mirrors rollback.ps1's "no backup found" branch: clear a stranded pending
    # state so the updater is not blocked from prompting again.
    if [ "$status" = "pending" ]; then
        tmp="$STATE_FILE.tmp"
        cat >"$tmp" <<EOF
{
  "schema_version": 1,
  "last_update": "",
  "status": "none",
  "updated_at": "$(date '+%Y-%m-%d %H:%M:%S')",
  "app": { "from": "", "to": "" },
  "msys": { "from": "", "to": "" }
}
EOF
        mv -f "$tmp" "$STATE_FILE"
        printf '未发现可恢复的备份，已清除未完成的更新状态。\n'
    else
        printf '没有找到可恢复的备份，未做任何修改。\n'
    fi
    exit 0
fi

printf '正在关闭海豚进程...\n'
pkill -x psi-agent 2>/dev/null || true
osascript -e 'quit app "HaiTun Agent"' >/dev/null 2>&1 || true
sleep 2

stamp="$(date '+%Y%m%d-%H%M%S')"
broken="$APP_PATH.broken-$stamp"

if [ -d "$APP_PATH" ] && ! mv "$APP_PATH" "$broken" 2>/dev/null; then
    printf '回滚失败: 无法移开当前版本。\n'
    exit 1
fi
if ! mv "$BACKUP_PATH" "$APP_PATH" 2>/dev/null; then
    # Put things back exactly as they were rather than leaving no app installed.
    [ -d "$broken" ] && mv "$broken" "$APP_PATH" 2>/dev/null || true
    printf '回滚失败: 无法恢复备份。\n'
    exit 1
fi
rm -rf "$broken"

tmp="$STATE_FILE.tmp"
cat >"$tmp" <<EOF
{
  "schema_version": 1,
  "last_update": "",
  "status": "rolled_back",
  "updated_at": "$(date '+%Y-%m-%d %H:%M:%S')",
  "app": { "from": "", "to": "" },
  "msys": { "from": "", "to": "" }
}
EOF
mv -f "$tmp" "$STATE_FILE"

open "$APP_PATH" 2>/dev/null && printf '海豚已重新启动。\n'
printf '回滚完成。\n'
