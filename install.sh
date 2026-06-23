#!/usr/bin/env sh
# psi-agent remote installer (macOS / Linux / WSL2).
#
# One-line install:
#   curl -fsSL https://raw.githubusercontent.com/genuineknowledge/psi-agent/feat/web-channel-setup-flow/install.sh | sh
#
# What it does:
#   1. installs uv if missing (from https://astral.sh/uv/install.sh)
#   2. clones psi-agent into ~/.psi-agent/psi-agent (pulls if already there)
#   3. runs `uv sync` to install dependencies
#   4. launches the interactive `psi-agent setup` wizard
#
# Environment overrides:
#   PSI_AGENT_REPO     git URL to clone (default: GitHub)
#   PSI_AGENT_BRANCH   branch to check out (default: main)
#   PSI_AGENT_HOME     install root (default: ~/.psi-agent)
#   PSI_AGENT_SKIP_SETUP=1   clone + sync only, skip the wizard
set -eu

REPO=${PSI_AGENT_REPO:-https://github.com/genuineknowledge/psi-agent.git}
BRANCH=${PSI_AGENT_BRANCH:-feat/web-channel-setup-flow}
HOME_DIR=${PSI_AGENT_HOME:-"$HOME/.psi-agent"}
SRC_DIR="$HOME_DIR/psi-agent"

say() { printf '%s\n' "$*"; }

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        return
    fi
    say "Installing uv from https://astral.sh/uv/install.sh ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if ! command -v uv >/dev/null 2>&1; then
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    fi
    if ! command -v uv >/dev/null 2>&1; then
        say "uv installed but not on PATH. Open a new terminal and re-run the installer." >&2
        exit 1
    fi
}

clone_or_update() {
    if [ -d "$SRC_DIR/.git" ]; then
        say "Updating existing checkout at $SRC_DIR ..."
        git -C "$SRC_DIR" fetch --depth 1 origin "$BRANCH"
        git -C "$SRC_DIR" checkout "$BRANCH"
        git -C "$SRC_DIR" reset --hard "origin/$BRANCH"
    else
        say "Cloning $REPO into $SRC_DIR ..."
        mkdir -p "$HOME_DIR"
        git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC_DIR"
    fi
}

if ! command -v git >/dev/null 2>&1; then
    say "git is required but not found. Install git, then re-run the installer." >&2
    exit 1
fi

ensure_uv
clone_or_update

cd "$SRC_DIR"
say "Syncing dependencies with uv..."
uv sync

if [ "${PSI_AGENT_SKIP_SETUP:-0}" = "1" ]; then
    say ""
    say "Installed at $SRC_DIR. Run the wizard later with:"
    say "  cd $SRC_DIR && uv run psi-agent setup"
    exit 0
fi

say "Launching the psi-agent setup wizard..."
if [ -e /dev/tty ]; then
    uv run psi-agent setup < /dev/tty
else
    uv run psi-agent setup
fi
