"""Private helper for the ``canvas`` toolset — a live Excalidraw canvas over MCP.

The ``canvas`` tool exposes ``mcp-excalidraw-server``'s native tools (element
CRUD, layout, screenshots, export/import, mermaid, snapshots …) through the
workspace's :mod:`_mcp` bridge so the agent can draw on and inspect a real,
interactive visual canvas.

Unlike the ``browser`` tool, the Excalidraw MCP server is **stdio-only**, and
the *stateful* part lives in a **separate canvas web server** it auto-starts on
``http://127.0.0.1:3000``. That canvas server is detached and long-lived, so the
drawing survives across the short-lived stdio connections :mod:`_mcp` opens per
call — we do not have to manage a persistent process ourselves the way the
browser tool does.

This module only builds the launch command and environment (and resolves ``npx``
across platforms). Node.js / ``npx`` must be on PATH; the first call downloads
``mcp-excalidraw-server`` and boots the canvas server. Screenshots, image
export, and mermaid rendering additionally need an **open browser tab** at the
canvas URL (they render in the frontend) — the ``browser`` tool can open it.

Env knobs (all optional): ``CANVAS_MCP_PACKAGE`` (default
``mcp-excalidraw-server@latest``), ``EXPRESS_SERVER_URL`` (default
``http://127.0.0.1:3000``), ``PORT`` / ``HOST``, ``ENABLE_CANVAS_SYNC`` (default
``true``), ``EXCALIDRAW_NO_AUTOSTART``, ``EXCALIDRAW_EXPORT_DIR`` (defaults to the
workspace dir so exported ``.excalidraw`` files land in the repo).
"""

from __future__ import annotations

import os
import shutil

# Pinned npx package for the Excalidraw MCP server. Launching it auto-starts the
# local canvas web server (127.0.0.1:3000) that actually holds the drawing state.
_MCP_PACKAGE = os.environ.get("CANVAS_MCP_PACKAGE", "mcp-excalidraw-server@latest")

# Default canvas web server URL — where the user opens a browser tab to watch the
# agent draw and to enable screenshot / image-export / mermaid rendering.
DEFAULT_CANVAS_URL = "http://127.0.0.1:3000"


class CanvasServerError(RuntimeError):
    """Raised when the Excalidraw MCP server cannot be launched (e.g. npx missing)."""


def _find_npx() -> str:
    """Locate the ``npx`` executable, accounting for Windows' ``npx.cmd``."""
    for name in ("npx", "npx.cmd", "npx.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise CanvasServerError(
        "npx (Node.js) not found on PATH. The canvas tools require Node.js; "
        "install it or ensure npx is reachable, then reload tools."
    )


def build_command(npx: str) -> list[str]:
    """Return the argv that launches the Excalidraw MCP server over stdio.

    ``-y`` lets ``npx`` install the package non-interactively on first use. The
    server needs no extra flags — it auto-starts the canvas web server unless
    ``EXCALIDRAW_NO_AUTOSTART`` is set.
    """
    return [npx, "-y", _MCP_PACKAGE]


def build_env() -> dict[str, str]:
    """Build the child environment for the stdio server.

    The MCP SDK replaces (does not merge) the child environment when one is
    provided, so we start from the full parent environment — otherwise ``npx`` /
    ``node`` would lose ``PATH`` and npm/proxy config on Windows. We only fill in
    the Excalidraw knobs when the user has not already set them (user env wins),
    and default the export directory to the workspace so ``.excalidraw`` exports
    land next to the user's files.
    """
    env = dict(os.environ)
    env.setdefault("EXPRESS_SERVER_URL", DEFAULT_CANVAS_URL)
    env.setdefault("ENABLE_CANVAS_SYNC", "true")
    export_dir = env.get("EXCALIDRAW_EXPORT_DIR") or env.get("WORKSPACE_DIR")
    if export_dir:
        env["EXCALIDRAW_EXPORT_DIR"] = export_dir
    return env


def canvas_url() -> str:
    """The canvas web server URL the user should open in a browser."""
    return os.environ.get("EXPRESS_SERVER_URL", DEFAULT_CANVAS_URL).strip() or DEFAULT_CANVAS_URL
