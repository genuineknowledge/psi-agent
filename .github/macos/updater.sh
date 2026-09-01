#!/bin/bash
# Background update checker + installer for HaiTun Agent.app.
#
# Counterpart to haitun.c:488-569 (the poll/prompt/download loop) plus the parts
# Inno Setup's PrepareToInstall does on Windows (stop the app, move the old tree
# to .backup, put the new one in place, record rollback state).
#
# macOS has a single component, so there is no App/MSYS/Full split: one dmg, one
# version file. `rollback-state.json` keeps the Windows schema with the `msys`
# fields empty so rollback.sh and the Windows rollback.ps1 stay comparable.
#
# Two modes:
#   --watch   poll forever (spawned by launcher.sh)
#   --apply   perform one swap from an already-mounted dmg (spawned detached by
#             the watch loop, because the swap must outlive the app it replaces)
set -uo pipefail

SUPPORT_ROOT="$HOME/Library/Application Support/Haitun"
AGENT_DIR="$SUPPORT_ROOT/agent"
LOG_DIR="$HOME/Library/Logs/Haitun"
STATE_FILE="$SUPPORT_ROOT/rollback-state.json"

mkdir -p "$SUPPORT_ROOT" "$LOG_DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# ---- config (written by build-dmg.sh, loaded by launcher.sh) ----
load_conf() {
    local file="$AGENT_DIR/haitun-update.conf" line key value
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in ''|'#'*) continue ;; esac
        case "$line" in *=*) ;; *) continue ;; esac
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            [A-Za-z_][A-Za-z0-9_]*) export "$key=$value" ;;
        esac
    done < "$file"
}
load_conf

BASE_URL="${HAITUN_UPDATE_BASE_URL:-}"
BASE_URL="${BASE_URL%/}"
INTERVAL_HOURS="${HAITUN_UPDATE_INTERVAL_HOURS:-24}"
case "$INTERVAL_HOURS" in
    ''|*[!0-9]*) INTERVAL_HOURS=24 ;;
esac
[ "$INTERVAL_HOURS" -gt 0 ] 2>/dev/null || INTERVAL_HOURS=24
INTERVAL_SECONDS=$((INTERVAL_HOURS * 3600))

APP_PATH="${HAITUN_APP_PATH:-/Applications/HaiTun Agent.app}"
DMG_NAME="HaiTun_Agent.dmg"

# ---- rollback state (same schema as the Windows rollback-state.json) ----
write_state() {
    local status="$1" from="$2" to="$3" tmp
    tmp="$STATE_FILE.tmp"
    cat >"$tmp" <<EOF
{
  "schema_version": 1,
  "last_update": "app",
  "status": "$status",
  "updated_at": "$(date '+%Y-%m-%d %H:%M:%S')",
  "app": { "from": "$from", "to": "$to" },
  "msys": { "from": "", "to": "" }
}
EOF
    mv -f "$tmp" "$STATE_FILE"
}

read_local_version() {
    if [ -f "$AGENT_DIR/haitun-version.txt" ]; then
        tr -d '[:space:]' < "$AGENT_DIR/haitun-version.txt"
    fi
}

# ---- apply mode: swap the bundle after the running app exits ----
# Runs detached from the app being replaced. Waits for the Gateway to exit
# instead of killing it outright, so an in-flight conversation is not cut off;
# falls back to a hard kill after a grace period.
apply_update() {
    local dmg_path="$1" from_version="$2" to_version="$3"
    local mount_point="" staged_app="" backup_path="" waited=0

    mount_point="$(mktemp -d /tmp/haitun-dmg.XXXXXX)"
    if ! hdiutil attach "$dmg_path" -nobrowse -readonly -mountpoint "$mount_point" >/dev/null 2>&1; then
        log "apply: failed to mount $dmg_path"
        rmdir "$mount_point" 2>/dev/null || true
        return 1
    fi
    # shellcheck disable=SC2064  # expand now: mount_point must be captured here
    trap "hdiutil detach '$mount_point' -quiet >/dev/null 2>&1 || true; rmdir '$mount_point' 2>/dev/null || true" EXIT

    staged_app="$(find "$mount_point" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)"
    if [ -z "$staged_app" ]; then
        log "apply: no .app inside dmg"
        return 1
    fi

    # Refuse to install something Gatekeeper would reject anyway. Skipped when
    # the local app is itself unsigned (pre-certificate builds) — otherwise the
    # unsigned channel could never self-update.
    if codesign -dv "$APP_PATH" >/dev/null 2>&1; then
        if ! codesign --verify --deep --strict "$staged_app" >/dev/null 2>&1; then
            log "apply: staged app failed signature verification, aborting"
            return 1
        fi
    fi

    # Wait for the app to quit. 120s then SIGTERM, matching the spirit of the
    # Windows taskkill in PrepareToInstall but giving the session a chance first.
    if [ -n "${HAITUN_GATEWAY_PID:-}" ]; then
        while kill -0 "$HAITUN_GATEWAY_PID" 2>/dev/null; do
            waited=$((waited + 2))
            if [ "$waited" -ge 120 ]; then
                kill "$HAITUN_GATEWAY_PID" 2>/dev/null || true
                sleep 3
                break
            fi
            sleep 2
        done
    fi
    pkill -x psi-agent 2>/dev/null || true
    sleep 1

    backup_path="$APP_PATH.backup"
    rm -rf "$backup_path"
    write_state pending "$from_version" "$to_version"

    if ! mv "$APP_PATH" "$backup_path" 2>/dev/null; then
        log "apply: cannot move current app aside (permissions?), aborting"
        write_state none "" ""
        return 1
    fi
    if ! cp -R "$staged_app" "$APP_PATH" 2>/dev/null; then
        log "apply: copy failed, restoring backup"
        rm -rf "$APP_PATH"
        mv "$backup_path" "$APP_PATH" 2>/dev/null || true
        write_state none "" ""
        return 1
    fi
    # Quarantine flag on the copied tree would make the freshly installed app
    # prompt on first launch even though the dmg was notarized.
    xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true

    # Quoted: bare `done` is a bash reserved word, so an unquoted one here does
    # not reach write_state as the literal status rollback.sh matches on.
    write_state "done" "$from_version" "$to_version"
    log "apply: updated $from_version -> $to_version"
    open "$APP_PATH" 2>/dev/null || true
    return 0
}

# ---- watch mode ----
watch_loop() {
    local local_version remote_version tmp_dmg answer helper http_code curl_status
    [ -n "$BASE_URL" ] || { log "watch: no base url, updater idle"; return 0; }

    while :; do
        sleep "$INTERVAL_SECONDS"

        local_version="${HAITUN_LOCAL_VERSION:-}"
        [ -n "$local_version" ] || local_version="$(read_local_version)"
        [ -n "$local_version" ] || continue

        remote_version="$(curl -fsS --max-time 30 "$BASE_URL/haitun-version.txt" 2>/dev/null | tr -d '[:space:]')"
        [ -n "$remote_version" ] || continue
        [ "$remote_version" != "$local_version" ] || continue

        # Do not stack prompts on top of a half-finished update.
        if [ -f "$STATE_FILE" ] && grep -q '"status": "pending"' "$STATE_FILE" 2>/dev/null; then
            continue
        fi

        log "watch: remote $remote_version != local $local_version"
        answer="$(osascript -e 'display dialog "HaiTun Agent 发现新版本。\n\n是否现在下载并更新？" with title "发现新版本" buttons {"稍后", "立即更新"} default button "立即更新" with icon note' 2>/dev/null || true)"
        case "$answer" in
            *"立即更新"*) ;;
            *) continue ;;
        esac

        tmp_dmg="$(mktemp -d /tmp/haitun-update.XXXXXX)/$DMG_NAME"
        # Record the HTTP status. Without it a 404 (object never published), a dead
        # network and a timeout all log the same "download failed" line, and the
        # only way to tell them apart is to probe the bucket by hand -- which is
        # exactly what happened on 2026-08-28.
        http_code="$(curl -fsSL --max-time 1800 -o "$tmp_dmg" \
            -w '%{http_code}' "$BASE_URL/$DMG_NAME" 2>/dev/null)"
        curl_status=$?
        if [ "$curl_status" -ne 0 ]; then
            log "watch: download failed (curl exit $curl_status, http ${http_code:-none}) $BASE_URL/$DMG_NAME"
            # Open nothing. There is no download page to send anyone to: the dmg url
            # answers NoSuchKey when the object is missing, and the bucket root
            # answers AccessDenied (both measured 2026-08-28) -- either way the user
            # lands on raw XML, which is worse than no browser at all. Show the url
            # in the dialog instead, so it can be copied or reported as-is.
            osascript -e "display dialog \"自动下载失败。\n\n下载地址：$BASE_URL/$DMG_NAME\n\n请稍后重试，或联系管理员确认该版本是否已发布。\" with title \"更新失败\" buttons {\"好\"} default button \"好\" with icon caution" >/dev/null 2>&1 || true
            continue
        fi

        # Run the swap from a copy outside the bundle: this script lives in
        # Contents/Resources of the very .app that --apply moves aside, and
        # executing a script out of a tree that is being replaced is asking for
        # a half-read script. The copy is disposable.
        helper="$(mktemp -d /tmp/haitun-apply.XXXXXX)/updater.sh"
        if ! cp "$0" "$helper"; then
            log "watch: cannot stage updater helper, skipping this round"
            continue
        fi
        chmod +x "$helper" || continue

        # Detach the swap: it has to outlive this process and the app itself.
        HAITUN_APP_PATH="$APP_PATH" \
        HAITUN_GATEWAY_PID="${HAITUN_GATEWAY_PID:-}" \
        nohup "$helper" --apply "$tmp_dmg" "$local_version" "$remote_version" \
            >>"$LOG_DIR/updater.log" 2>&1 &
        return 0
    done
}

case "${1:-}" in
    --watch)
        watch_loop
        ;;
    --apply)
        apply_update "${2:-}" "${3:-}" "${4:-}"
        ;;
    *)
        printf 'usage: %s --watch | --apply <dmg> <from> <to>\n' "$(basename "$0")" >&2
        exit 2
        ;;
esac
