#!/bin/bash
# CFBundleExecutable for HaiTun Agent.app — the macOS counterpart to haitun.c.
#
# Mirrors haitun.c steps 1-8, minus the parts Windows only needs:
#   - no MSYS on PATH: nothing in the product shells out to node/uv/bash. The
#     only subprocess call in the whole tree is `open -R` in
#     _workspace_manager.py, which macOS provides natively.
#   - no .env-in-install-dir reads for PATH fixup, only for env injection.
#
# The one structural difference from Windows: a signed .app must stay read-only
# (writing inside the bundle breaks the signature and Gatekeeper then refuses to
# launch it). The agent package is written at runtime — .env, logs/, .private/ —
# so it is seeded out of Resources/ into Application Support on first run.
set -uo pipefail

BUNDLE_MACOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_CONTENTS_DIR="$(dirname "$BUNDLE_MACOS_DIR")"
BUNDLE_RESOURCES_DIR="$BUNDLE_CONTENTS_DIR/Resources"
# The .app itself (…/HaiTun Agent.app), not its parent directory — this is what
# the updater replaces, so an extra dirname here would point it at /Applications.
BUNDLE_APP_DIR="$(dirname "$BUNDLE_CONTENTS_DIR")"

# Matches _appdata.py `_APPDATA_APPNAME = "Haitun"` via platformdirs, so the
# launcher and the Python side agree on one support root without either
# hardcoding the other's layout.
SUPPORT_ROOT="$HOME/Library/Application Support/Haitun"
AGENT_DIR="$SUPPORT_ROOT/agent"
LOG_DIR="$HOME/Library/Logs/Haitun"

mkdir -p "$SUPPORT_ROOT" "$LOG_DIR"

# ---- 1. seed the agent package out of the read-only bundle ----
# First run copies wholesale. Later runs keep user-modified tools/skills intact
# and refresh only what the build owns: .env (CI-injected secrets) and the
# version/update config. Without this an update would silently keep serving the
# old config out of a stale copy.
if [ ! -d "$AGENT_DIR" ]; then
    mkdir -p "$AGENT_DIR"
    # -R rather than rsync: rsync is not guaranteed present on stock macOS 15+.
    cp -R "$BUNDLE_RESOURCES_DIR/haitun-workspace/." "$AGENT_DIR/" 2>/dev/null || true
fi
for owned in .env haitun-update.conf haitun-version.txt; do
    if [ -f "$BUNDLE_RESOURCES_DIR/haitun-workspace/$owned" ]; then
        cp -f "$BUNDLE_RESOURCES_DIR/haitun-workspace/$owned" "$AGENT_DIR/$owned" 2>/dev/null || true
    fi
done

# ---- 2. load .env, then update config ----
# Both are KEY=VALUE files written by CI (build-dmg.sh). Parsed rather than
# sourced so a stray line cannot execute code from a file that ships secrets.
load_env_file() {
    local file="$1" line key value
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in ''|'#'*) continue ;; esac
        case "$line" in *=*) ;; *) continue ;; esac
        key="${line%%=*}"
        value="${line#*=}"
        # Only accept shell-safe identifiers; ignore anything else silently.
        case "$key" in
            [A-Za-z_][A-Za-z0-9_]*) export "$key=$value" ;;
        esac
    done < "$file"
}

load_env_file "$AGENT_DIR/.env"
load_env_file "$AGENT_DIR/haitun-update.conf"

# ---- 3. resolve version + icon ----
LOCAL_VERSION=""
if [ -f "$AGENT_DIR/haitun-version.txt" ]; then
    LOCAL_VERSION="$(tr -d '[:space:]' < "$AGENT_DIR/haitun-version.txt")"
fi
ICON_PATH="$BUNDLE_RESOURCES_DIR/haitun.icns"

# ---- 4. launch the Gateway ----
# --browser, not --tray: _tray.py runs pystray's icon.run() on a background
# thread (line 63), but NSStatusItem requires the Cocoa event loop on the main
# thread. On macOS the tray silently fails and is swallowed by the except at
# _tray.py:48, leaving only a warning — so the browser tab is the only reliable
# entry point. Revisit if the tray threading model is fixed.
#
# --default-agent must be explicit: _defaults.py resolve_default_agent() only
# soft-detects a repo checkout or a cwd that looks like an agent package, and
# the seeded Application Support dir is neither from the process's point of view.
# --default-workspace matches DEFAULT_USER_WORKSPACE_NAME ("haitun交付").
#
# --gateway is likewise explicit because it is **required** (gateway/__init__.py
# declares it `tyro.MISSING`): which HTTP surfaces to mount is the deployer's
# call, since mounting one too few fails silently -- some frontend 404s and
# nothing logs. Omitting it is not a soft default but a tyro exit **2** before
# the Gateway starts, which is what this launcher hit: the dmg shipped, was
# signed and notarized, and died the instant it was launched. `desktop` alone
# matches haitun.c:698 -- the dmg, like the installer, ships only the ToC
# surface, and adding `feishu` would mount ToB routes no installed user reaches.
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_LOG="$LOG_DIR/$STAMP.out.log"
ERR_LOG="$LOG_DIR/$STAMP.err.log"

cd "$AGENT_DIR" || exit 1

"$BUNDLE_MACOS_DIR/psi-agent" gateway \
    --gateway desktop \
    --browser \
    --icon "$ICON_PATH" \
    --verbose \
    --default-agent "$AGENT_DIR" \
    --default-workspace "$HOME/Desktop/haitun交付" \
    >"$OUT_LOG" 2>"$ERR_LOG" &
GATEWAY_PID=$!

# ---- 5. background update checker ----
# Detached with setsid-less nohup: it must outlive nothing in particular, but it
# must not hold the launcher's stdio open or `open -a` would appear to hang.
if [ -n "${HAITUN_UPDATE_BASE_URL:-}" ] && [ -x "$BUNDLE_RESOURCES_DIR/updater.sh" ]; then
    HAITUN_APP_PATH="$BUNDLE_APP_DIR" \
    HAITUN_LOCAL_VERSION="$LOCAL_VERSION" \
    HAITUN_GATEWAY_PID="$GATEWAY_PID" \
    nohup "$BUNDLE_RESOURCES_DIR/updater.sh" --watch \
        >>"$LOG_DIR/updater.log" 2>&1 &
fi

# Stay alive as long as the Gateway does: the Dock icon and app lifecycle are
# tied to this process. Exiting here would make macOS consider the app quit
# while the Gateway kept running headless.
wait "$GATEWAY_PID"
