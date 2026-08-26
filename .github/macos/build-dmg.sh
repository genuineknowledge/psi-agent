#!/bin/bash
# Assemble, sign, notarize and package HaiTun Agent.app into a dmg.
#
# Single entry point for both CI and local runs. Signing and notarization are
# opt-in **by secret presence**, not by a flag: with no certificate configured
# the script still produces a working (unsigned) dmg, and once the Apple
# Developer certificate lands you only add secrets — no code change here.
#
# Version comes from `.github/inno-setup/haitun.iss` MyAppVersion, the same
# single source of truth as the Windows launcher (build-haitun-launcher.ps1:10)
# and the same value oss-publish.yml gates on. macOS shares
# `haitun-version.txt` with Windows, so it must not invent its own numbering.
#
# Inputs (env):
#   PSI_AGENT_BIN                 path to the PyInstaller binary (required)
#   HAITUN_DOWNLOAD_BASE_URL      updater base url; empty disables update checks
#   HAITUN_UPDATE_INTERVAL_HOURS  updater interval, default 24
#   P12_CERTIFICATE(+P12_PASSWORD) base64 p12 and its password -> enables signing
#   MACOS_KEYCHAIN_PWD            temp keychain password (optional; has a default)
#   APPLE_ID/APP_SPECIFIC_PASSWORD/APPLE_TEAM_ID -> enables notarization
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MACOS_DIR="$REPO_ROOT/.github/macos"
WORKSPACE_SRC="$REPO_ROOT/examples/haitun-workspace"
ISS_FILE="$REPO_ROOT/.github/inno-setup/haitun.iss"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/macos-dist}"
BUILD_DIR="$OUT_DIR/build"
APP_NAME="HaiTun Agent"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"
DMG_PATH="$OUT_DIR/HaiTun_Agent.dmg"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---- version (single source of truth: haitun.iss) ----
[ -f "$ISS_FILE" ] || die "cannot find $ISS_FILE"
VERSION="$(sed -n 's/^#define[[:space:]]\{1,\}MyAppVersion[[:space:]]\{1,\}"\([^"]*\)".*/\1/p' "$ISS_FILE")"
[ -n "$VERSION" ] || die "cannot parse MyAppVersion from $ISS_FILE"
log "version $VERSION"

PSI_AGENT_BIN="${PSI_AGENT_BIN:-}"
[ -n "$PSI_AGENT_BIN" ] || die "PSI_AGENT_BIN is required"
[ -f "$PSI_AGENT_BIN" ] || die "PSI_AGENT_BIN not found: $PSI_AGENT_BIN"

rm -rf "$BUILD_DIR" "$DMG_PATH"
mkdir -p "$BUILD_DIR" "$OUT_DIR"

# ---- bundle skeleton ----
log "assembling bundle"
CONTENTS="$APP_BUNDLE/Contents"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

sed "s/@HAITUN_VERSION@/$VERSION/g" "$MACOS_DIR/Info.plist.in" >"$CONTENTS/Info.plist"
plutil -lint "$CONTENTS/Info.plist" >/dev/null || die "generated Info.plist is malformed"

install -m 755 "$MACOS_DIR/launcher.sh" "$CONTENTS/MacOS/haitun"
install -m 755 "$PSI_AGENT_BIN" "$CONTENTS/MacOS/psi-agent"
install -m 755 "$MACOS_DIR/updater.sh" "$CONTENTS/Resources/updater.sh"
install -m 755 "$MACOS_DIR/rollback.sh" "$CONTENTS/Resources/rollback.sh"

# ---- icon: haitun.ico -> haitun.icns ----
# Reuses the Windows icon so both platforms stay visually identical and there is
# only one icon asset to keep in sync.
log "converting icon"
ICONSET="$BUILD_DIR/haitun.iconset"
mkdir -p "$ICONSET"
ICO_SRC="$REPO_ROOT/.github/inno-setup/haitun.ico"
PNG_TMP="$BUILD_DIR/haitun-1024.png"
if sips -s format png "$ICO_SRC" --out "$PNG_TMP" >/dev/null 2>&1; then
    for size in 16 32 64 128 256 512; do
        sips -z "$size" "$size" "$PNG_TMP" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1 || true
        double=$((size * 2))
        sips -z "$double" "$double" "$PNG_TMP" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1 || true
    done
    iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/haitun.icns" 2>/dev/null || true
fi
# Fall back to the raw .ico: the Gateway accepts ico for --icon, and a missing
# icon must not fail the build.
if [ ! -f "$CONTENTS/Resources/haitun.icns" ]; then
    log "icns conversion unavailable, shipping .ico as-is"
    cp "$ICO_SRC" "$CONTENTS/Resources/haitun.icns"
fi

# ---- agent package + runtime config ----
# Seeded into Resources and copied out to Application Support on first run: a
# signed bundle must stay read-only, and this tree is written at runtime.
log "staging agent package"
mkdir -p "$CONTENTS/Resources/haitun-workspace"
# Exclude the Windows-only agent binary and MSYS tree if a local checkout has
# them; nothing on macOS shells out to MSYS.
(cd "$WORKSPACE_SRC" && tar --exclude='msys64' --exclude='psi-agent.exe' --exclude='haitun.exe' -cf - .) \
    | (cd "$CONTENTS/Resources/haitun-workspace" && tar -xf -)

BASE_URL="${HAITUN_DOWNLOAD_BASE_URL:-}"
BASE_URL="${BASE_URL%/}"
INTERVAL="${HAITUN_UPDATE_INTERVAL_HOURS:-24}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=24 ;; esac
[ "$INTERVAL" -gt 0 ] 2>/dev/null || INTERVAL=24
[ -n "$BASE_URL" ] || log "HAITUN_DOWNLOAD_BASE_URL not set; built app will skip update checks"

printf 'HAITUN_UPDATE_BASE_URL=%s\nHAITUN_UPDATE_INTERVAL_HOURS=%s\n' \
    "$BASE_URL" "$INTERVAL" >"$CONTENTS/Resources/haitun-workspace/haitun-update.conf"
printf '%s\n' "$VERSION" >"$CONTENTS/Resources/haitun-workspace/haitun-version.txt"
# Published alongside the dmg so oss-publish.yml has the same version file
# Windows produces.
printf '%s\n' "$VERSION" >"$OUT_DIR/haitun-version.txt"

# ---- signing (only when a certificate is configured) ----
SIGNED=0
KEYCHAIN_PATH=""
cleanup_keychain() {
    [ -n "$KEYCHAIN_PATH" ] || return 0
    security delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1 || true
    KEYCHAIN_PATH=""
}
trap cleanup_keychain EXIT

if [ -n "${P12_CERTIFICATE:-}" ] && [ -n "${P12_PASSWORD:-}" ]; then
    log "signing"
    KEYCHAIN_PATH="$HOME/Library/Keychains/haitun-signing.keychain-db"
    KEYCHAIN_PWD="${MACOS_KEYCHAIN_PWD:-haitun-ci-temp}"
    P12="$BUILD_DIR/cert.p12"

    # `base64 -d` rejects embedded newlines on macOS; GitHub secrets round-trip
    # multi-line values, and a wrapped base64 blob is easy to paste. Strip
    # whitespace so both flat and wrapped values decode.
    printf '%s' "$P12_CERTIFICATE" | tr -d '\r\n \t' | base64 --decode >"$P12"
    [ -s "$P12" ] || die "P12_CERTIFICATE did not decode to anything; is it valid base64?"
    security create-keychain -p "$KEYCHAIN_PWD" "$KEYCHAIN_PATH"
    security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
    security unlock-keychain -p "$KEYCHAIN_PWD" "$KEYCHAIN_PATH"
    security import "$P12" -k "$KEYCHAIN_PATH" -P "$P12_PASSWORD" \
        -T /usr/bin/codesign >/dev/null
    security set-key-partition-list -S apple-tool:,apple:,codesign: \
        -s -k "$KEYCHAIN_PWD" "$KEYCHAIN_PATH" >/dev/null
    # Prepend our keychain to the user search list while keeping the existing
    # entries. Read into an array rather than word-splitting a command
    # substitution: keychain paths contain spaces ("/Library/Keychains/…").
    existing_keychains=()
    while IFS= read -r kc; do
        kc="${kc//\"/}"
        kc="${kc#"${kc%%[![:space:]]*}"}"
        [ -n "$kc" ] && existing_keychains+=("$kc")
    done < <(security list-keychains -d user)
    security list-keychains -d user -s "$KEYCHAIN_PATH" "${existing_keychains[@]}"
    rm -f "$P12"

    IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN_PATH" \
        | sed -n 's/.*"\(Developer ID Application[^"]*\)".*/\1/p' | head -1)"
    [ -n "$IDENTITY" ] || die "no Developer ID Application identity in the imported certificate"
    log "identity: $IDENTITY"

    # Inner Mach-O files first, bundle last: codesign requires nested code to be
    # sealed before the enclosing bundle. --deep alone is documented as
    # unreliable for this, hence the explicit inner pass.
    find "$CONTENTS" -type f \( -name '*.dylib' -o -name '*.so' \) -print0 2>/dev/null \
        | while IFS= read -r -d '' lib; do
            codesign --force --timestamp --options runtime \
                --keychain "$KEYCHAIN_PATH" --sign "$IDENTITY" "$lib" >/dev/null 2>&1 || true
        done
    codesign --force --timestamp --options runtime \
        --entitlements "$MACOS_DIR/entitlements.plist" \
        --keychain "$KEYCHAIN_PATH" --sign "$IDENTITY" "$CONTENTS/MacOS/psi-agent"
    codesign --force --timestamp --options runtime \
        --entitlements "$MACOS_DIR/entitlements.plist" \
        --keychain "$KEYCHAIN_PATH" --sign "$IDENTITY" "$APP_BUNDLE"
    codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"
    SIGNED=1
else
    log "no signing certificate configured; producing an UNSIGNED build"
    log "users will need to bypass Gatekeeper manually until a certificate is added"
fi

# ---- dmg ----
log "building dmg"
DMG_STAGE="$BUILD_DIR/dmg"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$APP_BUNDLE" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_STAGE" \
    -ov -format UDZO "$DMG_PATH" >/dev/null

if [ "$SIGNED" = "1" ]; then
    codesign --force --timestamp --keychain "$KEYCHAIN_PATH" \
        --sign "$IDENTITY" "$DMG_PATH"
fi

# ---- notarization (only with full Apple credentials) ----
# HAITUN_NOTARIZE gates the *queue wait*, not the credentials: CI sets it only on
# the release path (main / v* tags) because notarytool --wait blocks on Apple's
# queue, measured at 45+ min. Set it to 1 locally to notarize a manual build.
NOTARIZE="${HAITUN_NOTARIZE:-}"
case "$NOTARIZE" in true|1|yes) NOTARIZE=1 ;; *) NOTARIZE=0 ;; esac

if [ "$SIGNED" = "1" ] && [ "$NOTARIZE" = "1" ] \
   && [ -n "${APPLE_ID:-}" ] && [ -n "${APP_SPECIFIC_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    # 3h, not 45m: Apple's queue has no SLA and a timeout here is indistinguishable
    # from a bad package, which is the worst way to fail a release. The submission
    # keeps processing server-side regardless, so a shorter timeout buys nothing.
    log "submitting for notarization (Apple's queue can take tens of minutes)"
    SUBMIT_LOG="$BUILD_DIR/notarytool-submit.log"
    if ! xcrun notarytool submit "$DMG_PATH" \
        --apple-id "$APPLE_ID" \
        --password "$APP_SPECIFIC_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" \
        --wait \
        --timeout 3h 2>&1 | tee "$SUBMIT_LOG"; then
        # Self-diagnose instead of telling the reader to go find a Mac. A bare
        # "exit code 124" cannot distinguish "Apple's queue is slow" from "our
        # package is rejected", and the two need opposite responses: retry vs fix.
        # The submission keeps processing server-side, so info/log may already
        # have the verdict even though --wait gave up.
        SUB_ID="$(sed -n 's/^[[:space:]]*id: \([0-9a-fA-F-]\{36\}\)[[:space:]]*$/\1/p' "$SUBMIT_LOG" | head -1)"
        if [ -n "$SUB_ID" ]; then
            log "notarization did not complete in time; submission id $SUB_ID"
            # A hosted runner losing DNS mid-wait is a real failure mode (observed:
            # NSURLErrorNotConnectedToInternet at minute 58 of a 3h wait). Without
            # this the diagnosis inherits the outage and prints three copies of the
            # same network error instead of Apple's verdict.
            for _ in 1 2 3 4 5 6; do
                if nc -z -G 5 appstoreconnect.apple.com 443 2>/dev/null; then
                    break
                fi
                log "waiting for network to come back before diagnosing"
                sleep 10
            done
            log "--- notarytool info ---"
            xcrun notarytool info "$SUB_ID" \
                --apple-id "$APPLE_ID" --password "$APP_SPECIFIC_PASSWORD" \
                --team-id "$APPLE_TEAM_ID" 2>&1 | sed 's/^/    /' || true
            log "--- notarytool log (issues, if any) ---"
            xcrun notarytool log "$SUB_ID" \
                --apple-id "$APPLE_ID" --password "$APP_SPECIFIC_PASSWORD" \
                --team-id "$APPLE_TEAM_ID" 2>&1 | sed 's/^/    /' || true
            die "notarization unfinished (id $SUB_ID); see info/log above"
        fi
        die "notarization failed before a submission id was assigned; check credentials"
    fi
    # Staple the ticket so first launch works without a network round-trip.
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$DMG_PATH"
    log "notarized and stapled"
elif [ "$SIGNED" = "1" ] && [ "$NOTARIZE" != "1" ]; then
    log "signed but not notarized (not a release build); Gatekeeper will warn"
elif [ "$SIGNED" = "1" ]; then
    log "signed but NOT notarized (Apple credentials absent); Gatekeeper will still warn"
fi

# ---- Gatekeeper admission check ----
# Answers the question a signature check cannot: would Gatekeeper admit this,
# or would a user get "Apple cannot check it for malicious software"? Passing
# notarization does not by itself guarantee passing Gatekeeper -- dangling load
# command paths are a documented way to be notarized and still rejected.
#
# Commands and their scoping are per Apple DTS (developer.apple.com/forums/thread/130560):
# `-t open --context context:primary-signature` for disk images,
# `syspolicy_check distribution` for app bundles on macOS 14+.
#
# Two limits, so nobody reads a green line here as a release gate:
#  - DTS calls the command-line route "a quick, albeit less accurate test".
#    The authoritative test is downloading via Safari onto a clean machine that
#    has never seen the product, then pulling the network to prove stapling.
#  - This runs on the machine that just signed the dmg, whose keychain holds the
#    signing certificate. That state does not exist on a user's Mac.
# No quarantine attribute is stamped: spctl assesses unconditionally, so a fake
# xattr would add nothing but a false impression of fidelity.
log "--- Gatekeeper admission (indicative, not a substitute for clean-machine test) ---"
GK_OUT="$BUILD_DIR/spctl.txt"
if spctl -a -vvv -t open --context context:primary-signature "$DMG_PATH" >"$GK_OUT" 2>&1; then
    GK_VERDICT=accepted
else
    GK_VERDICT=rejected
fi
sed 's/^/    /' "$GK_OUT" || true

# syspolicy_check is the accurate one for app bundles, and it is what catches
# the dangling-load-path class of failure that spctl on the dmg can miss.
if command -v syspolicy_check >/dev/null 2>&1; then
    log "--- syspolicy_check distribution (app bundle) ---"
    syspolicy_check distribution "$APP_BUNDLE" 2>&1 | sed 's/^/    /' || true
fi

# Re-validate the ticket here as well as after stapling: this is the last touch
# before upload, so it catches a ticket lost to anything done in between.
if [ "$NOTARIZE" = "1" ] && [ "$SIGNED" = "1" ]; then
    log "--- stapler validate ---"
    xcrun stapler validate "$DMG_PATH" 2>&1 | sed 's/^/    /' || true
fi

case "$GK_VERDICT" in
accepted)
    log "Gatekeeper: accepted"
    ;;
rejected)
    # Not fatal off the release path: unnotarized *is* the expected state there,
    # and dying would block every branch build. On the release path a rejection
    # means users hit the malware dialog -- the exact outcome notarization
    # exists to prevent -- so that one stops the build.
    if [ "$NOTARIZE" = "1" ] && [ "$SIGNED" = "1" ]; then
        die "Gatekeeper rejected a notarized build; see spctl output above"
    fi
    log "Gatekeeper: rejected (expected without notarization)"
    log "  users will get: \"Apple cannot check it for malicious software\""
    log "  tester workaround: right-click the app -> Open"
    ;;
esac

# ---- launch smoke test ----
# The Gatekeeper block above answers "would the system admit this". It does not
# answer "does it then run", and those fail differently for the user:
#   admission failure -> "Apple cannot check it for malicious software"
#   launch failure    -> "The application HaiTun Agent can't be opened."
# The second dialog was reported from a real Mac while every CI check was green,
# because nothing here had ever executed the thing it just built.
#
# Installs out of the mounted dmg rather than reusing $APP_BUNDLE: the copy is
# what users get, and hdiutil round-tripping is itself part of what can break
# permissions or the signature.
log "--- launch smoke test ---"
SMOKE_LOG="$BUILD_DIR/smoke.txt"
: >"$SMOKE_LOG"
smoke() { printf '%s\n' "$*" >>"$SMOKE_LOG"; }

MNT="$BUILD_DIR/mnt"
INSTALLED="$BUILD_DIR/installed"
rm -rf "$MNT" "$INSTALLED"
mkdir -p "$MNT" "$INSTALLED"

SMOKE_OK=0
LS_OK=0
if hdiutil attach "$DMG_PATH" -mountpoint "$MNT" -nobrowse -readonly >/dev/null 2>&1; then
    cp -R "$MNT/$APP_NAME.app" "$INSTALLED/" 2>/dev/null || true
    hdiutil detach "$MNT" >/dev/null 2>&1 || true
    RUN_APP="$INSTALLED/$APP_NAME.app"
    MAIN_EXE="$RUN_APP/Contents/MacOS/haitun"

    # Structural facts first, so a failure below can be read without a Mac in
    # hand. `file` on the main executable matters: a bundle whose
    # CFBundleExecutable is a script is signed differently from a Mach-O one.
    smoke "=== structure ==="
    smoke "main executable: $(file -b "$MAIN_EXE" 2>&1)"
    smoke "psi-agent: $(file -b "$RUN_APP/Contents/MacOS/psi-agent" 2>&1)"
    smoke "arch: $(lipo -archs "$RUN_APP/Contents/MacOS/psi-agent" 2>&1)"
    smoke "host arch: $(uname -m)"
    smoke "perms: $(ls -l "$MAIN_EXE" 2>&1)"
    smoke "=== codesign -dv (installed copy) ==="
    codesign -dv --verbose=4 "$RUN_APP" >>"$SMOKE_LOG" 2>&1 || true

    # Verify the *installed copy*, not the pre-dmg bundle the earlier check ran
    # on. hdiutil round-trips through a filesystem image and `cp -R` re-creates
    # every file, either of which can invalidate a seal that verified fine at
    # signing time. LaunchServices does this check on double-click; a direct exec
    # does not, which is precisely the gap that let a broken bundle look fine.
    smoke "=== codesign --verify --deep --strict (installed copy) ==="
    if codesign --verify --deep --strict --verbose=2 "$RUN_APP" >>"$SMOKE_LOG" 2>&1; then
        smoke "verify: ok"
    else
        smoke "verify: FAILED -- this is what makes double-click say \"can't be opened\""
    fi

    # The launch path users actually take. Everything below exercises
    # LaunchServices instead of exec(2): bundle-wide seal validation, Info.plist
    # consumption, and the quarantine/policy layer all live here and nowhere in
    # a direct exec. `open` returns non-zero and prints the reason (LSOpenURLs...
    # error -10810 etc) rather than putting up a dialog, so the verdict is
    # capturable on a runner with no one to click "OK".
    smoke "=== open -a (LaunchServices path) ==="
    if open -a "$RUN_APP" >>"$SMOKE_LOG" 2>&1; then
        smoke "open: accepted the launch request"
        LS_OK=1
    else
        smoke "open: FAILED with status $? -- matches the real-machine dialog"
        LS_OK=0
    fi
    # `open` returns as soon as the request is handed off, so a process check has
    # to come after a beat. Without this an immediate-exit failure reads as success.
    sleep 20
    if pgrep -f "$APP_NAME.app/Contents/MacOS/psi-agent" >/dev/null 2>&1; then
        smoke "post-open: psi-agent is running"
    else
        smoke "post-open: no psi-agent process -- launched then died, or never started"
        LS_OK=0
    fi
    pkill -f "$APP_NAME.app/Contents/MacOS/" 2>/dev/null || true
    smoke "LS_OK=$LS_OK"

    # Direct exec, not `open -a`: it bypasses LaunchServices so the failure lands
    # on our stderr instead of in a GUI dialog, and a runtime/entitlement kill
    # shows up as a signal rather than a silent no-op.
    #
    # The launcher ends in `wait` on the Gateway and never returns, so success
    # here means "still alive after the grace period", not "exited 0". Killing
    # the whole process group: the launcher backgrounds children that would
    # otherwise outlive it and hold the runner's stdio open.
    smoke "=== direct exec ==="
    EXE_OUT="$BUILD_DIR/smoke-exec.txt"
    ( "$MAIN_EXE" >"$EXE_OUT" 2>&1 & echo $! >"$BUILD_DIR/smoke.pid" ) || true
    SMOKE_PID="$(cat "$BUILD_DIR/smoke.pid" 2>/dev/null || echo)"

    # 40s: the launcher seeds a ~600 MB agent tree on first run before the
    # Gateway even starts, and a hosted runner's disk is not fast.
    ALIVE=0
    for _ in $(seq 1 40); do
        sleep 1
        if [ -n "$SMOKE_PID" ] && kill -0 "$SMOKE_PID" 2>/dev/null; then
            ALIVE=1
        else
            ALIVE=0
            break
        fi
    done

    if [ "$ALIVE" = "1" ]; then
        smoke "process still alive after 40s (expected: launcher waits on the Gateway)"
        SMOKE_OK=1
        kill -TERM "-$SMOKE_PID" 2>/dev/null || kill -TERM "$SMOKE_PID" 2>/dev/null || true
    else
        # Exit status is the diagnosis: 126/127 point at exec (bad interpreter,
        # not executable), >128 is a signal -- 137/SIGKILL is what a hardened
        # runtime or library-validation rejection looks like from out here.
        wait "$SMOKE_PID" 2>/dev/null
        smoke "process exited early, status $?"
    fi

    smoke "=== stdout/stderr ==="
    tail -c 4000 "$EXE_OUT" >>"$SMOKE_LOG" 2>/dev/null || true
    smoke "=== gateway logs ==="
    # The launcher redirects the Gateway's own output into ~/Library/Logs/Haitun,
    # so nothing above would show a Python-level crash. An absent directory is
    # itself the finding: the launcher creates it at line 30, so missing means
    # not a single line of it ran.
    if [ -d "$HOME/Library/Logs/Haitun" ]; then
        ls -la "$HOME/Library/Logs/Haitun" >>"$SMOKE_LOG" 2>&1 || true
        for lf in "$HOME/Library/Logs/Haitun"/*.err.log; do
            [ -f "$lf" ] || continue
            smoke "--- $(basename "$lf") ---"
            tail -c 4000 "$lf" >>"$SMOKE_LOG" 2>/dev/null || true
        done
    else
        smoke "NO LOG DIR: launcher.sh never reached its own mkdir -- failure is at exec"
    fi
else
    smoke "hdiutil attach failed; cannot smoke test"
fi
sed 's/^/    /' "$SMOKE_LOG" || true

if [ "$SMOKE_OK" = "1" ] && [ "$LS_OK" = "1" ]; then
    log "launch smoke test: ok (both exec and LaunchServices)"
elif [ "$SMOKE_OK" = "1" ]; then
    # The interesting split. Direct exec bypasses seal validation and the policy
    # layer, so passing it while failing `open` narrows the fault to the bundle
    # rather than to the program inside it.
    log "launch smoke test: exec ok but LaunchServices FAILED -- see smoke.txt"
    log "  this is the \"can't be opened\" class of failure, not a Gatekeeper one"
else
    # Not fatal yet: this check is new and its own false-negative modes are not
    # yet characterised on a hosted runner (no window server, no user session).
    # Turning it into a hard gate before that is understood would block every
    # build on the probe rather than on the product.
    log "launch smoke test: FAILED -- see smoke.txt above"
    log "  this is the \"can't be opened\" class of failure, not a Gatekeeper one"
fi

log "done: $DMG_PATH"
log "version file: $OUT_DIR/haitun-version.txt"
