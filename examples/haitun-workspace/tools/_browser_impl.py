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
# use"). The browser runs **headed** by default so the user can watch the agent drive
# it; ``--headless`` is opt-IN via BROWSER_HEADLESS=1 for headless servers / CI that
# have no display.
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
    # Headed by default (empty/unset -> visible window); opt in to headless with
    # BROWSER_HEADLESS=1/true/yes on displayless hosts.
    return os.environ.get("BROWSER_HEADLESS", "0").strip().lower() in {"1", "true", "yes"}


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


def _build_env() -> dict[str, str]:
    """Child env for the MCP server, with the browser-reaping heartbeat disabled.

    Playwright MCP runs a server-side heartbeat: each HTTP session pings its client
    every 3s and, on a ~5s ping timeout, calls ``server.close()`` -> disposes the
    session -> decrements the shared client count. When the count hits zero the server
    **closes the whole browser** (verified against playwright-core's browserFactory:
    ``disposed`` -> ``close browser``).

    Our :mod:`_mcp` bridge opens a *fresh* HTTP connection per tool call and drops it
    right after, so between calls — and after a task finishes — there is no connected
    client. The heartbeat then reaps the last session within ~5-15s and tears the
    browser down, even though nobody called ``browser_close``. The user sees a page
    they were mid-way through (e.g. a login/QR screen) vanish on its own.

    Setting ``PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0`` disables the heartbeat entirely
    (``if (timeout <= 0) return`` in startHeartbeat), so an idle browser is kept open
    until we explicitly tear the server down at interpreter exit (:func:`_shutdown`).
    Verified with a connect-per-call probe: with the heartbeat on the page became
    ``about:blank`` after ~15s idle; with it off the page survived 15/30/45s idles.
    Overridable via the same env var if a deployment needs the old reaping behaviour.
    """
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_MCP_PING_TIMEOUT_MS", "0")
    return env


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
        # the Node process (which would otherwise leak on every reload). Pass both
        # platform knobs explicitly (rather than **-unpacking an object dict) so the
        # type checker can resolve the ``Popen[str]`` overload from ``text=True``.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
        start_new_session = not _IS_WINDOWS
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=_build_env(),
                cwd=os.environ.get("WORKSPACE_DIR") or None,
                creationflags=creationflags,
                start_new_session=start_new_session,
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
