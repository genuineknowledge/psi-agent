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
if [ "$SIGNED" = "1" ] && [ -n "${APPLE_ID:-}" ] && [ -n "${APP_SPECIFIC_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    log "submitting for notarization (this can take several minutes)"
    xcrun notarytool submit "$DMG_PATH" \
        --apple-id "$APPLE_ID" \
        --password "$APP_SPECIFIC_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" \
        --wait \
        --timeout 45m
    # Staple the ticket so first launch works without a network round-trip.
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$DMG_PATH"
    log "notarized and stapled"
elif [ "$SIGNED" = "1" ]; then
    log "signed but NOT notarized (Apple credentials absent); Gatekeeper will still warn"
fi

log "done: $DMG_PATH"
log "version file: $OUT_DIR/haitun-version.txt"
