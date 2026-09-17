"""Private helper for the ``browser`` tool — a persistent Playwright MCP server.

The ``browser`` tool exposes Playwright MCP's native ``browser_*`` tools (navigate,
snapshot, click, type, ...) through the workspace's :mod:`_mcp` bridge. Those tools are
**stateful**: ``browser_navigate`` loads a page that a later ``browser_snapshot`` /
``browser_click`` must see. But :mod:`_mcp` opens a *fresh* client connection per tool
call, so the browser cannot live inside a single connection.

The fix is a long-lived **SSE/HTTP server**: one ``npx @playwright/mcp`` process,
launched once and reused, driving the *shared* single browser (owned by
:mod:`_browser_shared`) through ``--cdp-endpoint``, so every short-lived client
connection sees the same pages.

Because the server attaches to the one browser from :mod:`_browser_shared`, a session has
exactly one browser window no matter how many times the tools are called. The user may
close that window at any time; the shared owner then reports the closure (once) instead
of silently relaunching, and this server is restarted against the next browser on the
following call.
"""

from __future__ import annotations

import atexit
import http.client
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from urllib.parse import urlparse

import _browser_shared as _shared
import _runtime_paths as _paths
from loguru import logger

_IS_WINDOWS = sys.platform == "win32"

_MCP_PACKAGE = os.environ.get("BROWSER_MCP_PACKAGE", "@playwright/mcp@latest")
_CAPS = os.environ.get("BROWSER_CAPS", "vision,devtools")
_STARTUP_TIMEOUT = float(os.environ.get("BROWSER_STARTUP_TIMEOUT", "90"))
_HEALTH_TIMEOUT = float(os.environ.get("BROWSER_HEALTH_TIMEOUT", "5"))

_lock = threading.Lock()
_proc: subprocess.Popen[str] | None = None
_endpoint: str | None = None
_cdp_origin: str | None = None  # the shared browser origin this server is attached to


class BrowserServerError(RuntimeError):
    """Raised when the Playwright MCP server cannot be started."""


def _find_npx() -> str:
    """Locate the ``npx`` executable, accounting for Windows' ``npx.cmd``."""
    for name in ("npx", "npx.cmd", "npx.exe"):
        found = __import__("shutil").which(name)
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


def _build_command(npx: str, port: int, cdp_origin: str) -> list[str]:
    """Command line for the MCP server, attached to the *shared* browser via CDP.

    The browser itself (profile, headless, remote-debugging port) is owned by
    :mod:`_browser_shared`; the MCP server only connects to it. ``--shared-browser-context``
    keeps pages alive across this server's per-call HTTP sessions.
    """
    cmd = [
        npx,
        "-y",
        _MCP_PACKAGE,
        "--port",
        str(port),
        "--cdp-endpoint",
        cdp_origin,
        "--shared-browser-context",
        # NOTE: deliberately no ``--output-mode stdout`` here. Recent @playwright/mcp
        # releases reject that flag, and it only changes where console/network output
        # is written (files vs. inline) — never how navigate/snapshot/click return.
    ]
    if _CAPS.strip():
        cmd += ["--caps", _CAPS]
    return cmd


def _build_env() -> dict[str, str]:
    """Child env for the MCP server, with the browser-reaping heartbeat disabled.

    Playwright MCP runs a server-side heartbeat that closes the browser when the shared
    client count drops to zero. Our :mod:`_mcp` bridge opens a fresh connection per tool
    call, so between calls the heartbeat would tear the page down. Setting
    ``PLAYWRIGHT_MCP_PING_TIMEOUT_MS=0`` disables that; an idle page is kept open until we
    explicitly tear the server down at interpreter exit (:func:`_shutdown`).
    """
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_MCP_PING_TIMEOUT_MS", "0")
    return env


def _wait_until_listening(proc: subprocess.Popen[str], port: int) -> str:
    """Block until the server prints its listening banner; return the endpoint URL."""
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
            return f"http://localhost:{port}/mcp"
    raise BrowserServerError(f"Playwright MCP did not start within {_STARTUP_TIMEOUT:.0f}s on port {port}.")


def _drain_stdout(proc: subprocess.Popen[str]) -> None:
    """Keep consuming server stdout after startup so its pipe never blocks."""
    assert proc.stdout is not None
    for line in proc.stdout:
        logger.debug(f"[playwright-mcp] {line.rstrip()}")


def _is_endpoint_alive(endpoint: str) -> bool:
    """True if the MCP endpoint's HTTP listener still answers requests."""
    parsed = urlparse(endpoint)
    host, port, path = parsed.hostname, parsed.port, parsed.path or "/"
    if not host or not port:
        return False
    conn = http.client.HTTPConnection(host, port, timeout=_HEALTH_TIMEOUT)
    try:
        conn.request("POST", path, body=b"", headers={"Content-Type": "application/json"})
        conn.getresponse().read(0)
        return True
    except OSError, http.client.HTTPException:
        return False
    finally:
        with suppress(Exception):
            conn.close()


def _start_server(cdp_origin: str) -> str:
    """Launch an MCP server attached to *cdp_origin*; return its endpoint URL."""
    global _proc, _endpoint, _cdp_origin
    npx = _find_npx()
    port = _free_port()
    cmd = _build_command(npx, port, cdp_origin)
    logger.info(f"Starting Playwright MCP server: {' '.join(cmd)}")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_build_env(),
            cwd=_paths.workspace_dir() or None,
            creationflags=creationflags,
            start_new_session=not _IS_WINDOWS,
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
    _proc, _endpoint, _cdp_origin = proc, endpoint, cdp_origin
    logger.info(f"Playwright MCP server ready at {endpoint} (browser {cdp_origin})")
    return endpoint


def ensure_server() -> str:
    """Start the Playwright MCP server if needed and return its endpoint URL.

    Idempotent and thread-safe: repeated calls reuse a **healthy** running server
    attached to the *current* shared browser origin. If the user closed the shared
    browser, the first call here surfaces the closure message (via
    :mod:`_browser_shared`) instead of relaunching; the next call relaunches both the
    browser and this server.
    """
    try:
        cdp_origin = _shared.ensure_origin()
    except _shared.BrowserUnavailableError as exc:
        raise BrowserServerError(str(exc)) from exc

    global _proc, _endpoint, _cdp_origin
    with _lock:
        proc, endpoint, used_origin = _proc, _endpoint, _cdp_origin
        if proc is not None and endpoint and proc.poll() is None and _is_endpoint_alive(endpoint):
            if used_origin == cdp_origin:
                return endpoint
            logger.warning(f"Playwright MCP server was attached to {used_origin}; browser is now {cdp_origin}.")
            _proc, _endpoint, _cdp_origin = None, None, None
            _terminate_tree(proc)
        elif proc is not None:
            logger.warning(f"Playwright MCP server at {endpoint} is not healthy (exit={proc.poll()}); replacing it.")
            _proc, _endpoint, _cdp_origin = None, None, None
            _terminate_tree(proc)
        return _start_server(cdp_origin)


def _shutdown() -> None:
    global _proc, _endpoint, _cdp_origin
    proc = _proc
    _proc, _endpoint, _cdp_origin = None, None, None
    if proc is None or proc.poll() is not None:
        return
    _terminate_tree(proc)


def _terminate_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate the server and every child it spawned (npx -> node)."""
    if _IS_WINDOWS:
        with suppress(Exception):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
    else:
        with suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with suppress(Exception):
        proc.wait(timeout=5)
    with suppress(Exception):
        if proc.poll() is None:
            proc.kill()
