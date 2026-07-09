"""Private helper for the ``browser`` tool — a persistent Playwright MCP server.

The ``browser`` tool exposes Playwright MCP's native ``browser_*`` tools (navigate,
snapshot, click, type, …) through the workspace's :mod:`_mcp` bridge. Those tools are
**stateful**: ``browser_navigate`` loads a page that a later ``browser_snapshot`` /
``browser_click`` must see. But :mod:`_mcp` opens a *fresh* client connection per tool
call, so the browser cannot live inside a single connection.

The fix is a long-lived **SSE/HTTP server**: one ``npx @playwright/mcp`` process,
launched once and reused, with ``--shared-browser-context`` so every short-lived client
connection drives the *same* browser. This module owns that process — it starts it on
demand, waits until it is listening, hands back the endpoint URL, and tears it down at
interpreter exit.

The browser itself is the system-installed Edge (``--browser msedge``); nothing is
bundled. ``vision`` (screenshots) and ``devtools`` (raw CDP) capabilities are enabled.
"""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress

from loguru import logger

_IS_WINDOWS = sys.platform == "win32"

# Pinned package + flags. ``--shared-browser-context`` is what makes cross-connection
# state work (verified: without it a second connection errors "Browser is already in
# use"). ``--headless`` is opt-out via BROWSER_HEADLESS=0 for local debugging.
_MCP_PACKAGE = os.environ.get("BROWSER_MCP_PACKAGE", "@playwright/mcp@latest")
_BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL", "msedge")
_CAPS = os.environ.get("BROWSER_CAPS", "vision,devtools")
_STARTUP_TIMEOUT = float(os.environ.get("BROWSER_STARTUP_TIMEOUT", "90"))

_lock = threading.Lock()
_proc: subprocess.Popen[str] | None = None
_endpoint: str | None = None


class BrowserServerError(RuntimeError):
    """Raised when the Playwright MCP server cannot be started."""


def _find_npx() -> str:
    """Locate the ``npx`` executable, accounting for Windows' ``npx.cmd``."""
    for name in ("npx", "npx.cmd", "npx.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise BrowserServerError(
        "npx (Node.js) not found on PATH. The browser tools require Node.js; "
        "install it or ensure npx is reachable, then reload tools."
    )


def _free_port() -> int:
    """Grab an OS-assigned free localhost port, then release it for the server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _headless_flag() -> bool:
    return os.environ.get("BROWSER_HEADLESS", "1").strip().lower() not in {"0", "false", "no"}


def _build_command(npx: str, port: int) -> list[str]:
    cmd = [
        npx,
        "-y",
        _MCP_PACKAGE,
        "--port",
        str(port),
        "--browser",
        _BROWSER_CHANNEL,
        "--shared-browser-context",
        # Inline snapshots/console/network into the tool response instead of writing
        # them to files the agent cannot read.
        "--output-mode",
        "stdout",
    ]
    if _CAPS.strip():
        cmd += ["--caps", _CAPS]
    if _headless_flag():
        cmd.append("--headless")
    return cmd


def _wait_until_listening(proc: subprocess.Popen[str], port: int) -> str:
    """Block until the server prints its listening banner; return the endpoint URL.

    Playwright MCP prints ``Listening on http://localhost:<port>`` on stdout once ready.
    We echo its output to the log so failures are diagnosable, and bail out early if the
    process dies during startup.
    """
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = proc.stdout.read() or ""
            raise BrowserServerError(
                f"Playwright MCP exited during startup (code {proc.returncode}). Output:\n{tail[:2000]}"
            )
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        logger.debug(f"[playwright-mcp] {line.rstrip()}")
        if "Listening on" in line:
            # The server binds to the "localhost" hostname, which on Windows may resolve
            # to IPv6 ``::1`` only — connecting to the literal ``127.0.0.1`` then fails.
            # Use "localhost" so the client follows the same resolution the server used.
            # The streamable-HTTP endpoint is served at /mcp regardless of the host shown.
            return f"http://localhost:{port}/mcp"
    raise BrowserServerError(f"Playwright MCP did not start within {_STARTUP_TIMEOUT:.0f}s on port {port}.")


def _drain_stdout(proc: subprocess.Popen[str]) -> None:
    """Keep consuming server stdout after startup so its pipe never blocks."""
    assert proc.stdout is not None
    for line in proc.stdout:
        logger.debug(f"[playwright-mcp] {line.rstrip()}")


def ensure_server() -> str:
    """Start the Playwright MCP server if needed and return its endpoint URL.

    Idempotent and thread-safe: repeated calls reuse the running process. Raises
    :class:`BrowserServerError` if Node/npx is missing or the server fails to start.
    """
    global _proc, _endpoint
    with _lock:
        if _proc is not None and _proc.poll() is None and _endpoint:
            return _endpoint

        npx = _find_npx()
        port = _free_port()
        cmd = _build_command(npx, port)
        logger.info(f"Starting Playwright MCP server: {' '.join(cmd)}")
        # npx spawns a Node child that is the real server; give it its own process
        # group / job so we can terminate the whole tree at exit rather than orphaning
        # the Node process (which would otherwise leak on every reload).
        popen_kwargs: dict[str, object] = {}
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=os.environ.get("WORKSPACE_DIR") or None,
                **popen_kwargs,  # type: ignore[arg-type]
            )
        except OSError as exc:
            raise BrowserServerError(f"Failed to launch npx: {exc}") from exc

        try:
            endpoint = _wait_until_listening(proc, port)
        except BrowserServerError:
            _terminate_tree(proc)
            raise

        threading.Thread(target=_drain_stdout, args=(proc,), daemon=True).start()
        atexit.register(_shutdown)
        _proc, _endpoint = proc, endpoint
        logger.info(f"Playwright MCP server ready at {endpoint}")
        return endpoint


def _shutdown() -> None:
    global _proc, _endpoint
    proc = _proc
    _proc, _endpoint = None, None
    if proc is None or proc.poll() is not None:
        return
    _terminate_tree(proc)


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate the server and every child it spawned (npx -> node)."""
    if _IS_WINDOWS:
        # taskkill /T walks the whole child tree; plain terminate() only hits npx and
        # leaves the Node server orphaned.
        with suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
    else:
        with suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with suppress(Exception):
        proc.wait(timeout=5)
    with suppress(Exception):
        if proc.poll() is None:
            proc.kill()
