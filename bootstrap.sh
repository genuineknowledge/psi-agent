#!/usr/bin/env sh
# psi-agent one-line bootstrap (POSIX shells: bash, zsh, sh).
#
# Usage, from a cloned checkout:
#   ./bootstrap.sh
#
# This installs uv if it is missing, syncs dependencies, then launches the
# interactive setup wizard. Installing uv downloads and runs the official
# installer from https://astral.sh/uv/install.sh -- review it if you prefer.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Installing uv from https://astral.sh/uv/install.sh ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer adds uv to ~/.local/bin; make it usable in this session.
    if ! command -v uv >/dev/null 2>&1; then
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    fi
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is still not on PATH. Open a new terminal and re-run ./bootstrap.sh" >&2
    exit 1
fi

echo "Syncing dependencies with uv..."
uv sync

echo "Launching the psi-agent setup wizard..."
uv run psi-agent setup
