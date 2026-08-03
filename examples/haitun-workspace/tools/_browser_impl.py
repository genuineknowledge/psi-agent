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
import http.client
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import _runtime_paths as _paths
import platformdirs
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
# Our own profile directory, so a *previous* run's orphaned browser cannot hold the lock
# on the profile this run needs (see _reclaim_profile). Left to itself, Playwright MCP
# derives the profile path from a hash of the server's cwd, which means every gateway
# restart lands on the same directory as whatever leaked last time and then fails every
# tool call with "Browser is already in use for <dir>".
_PROFILE_DIR = os.environ.get("BROWSER_PROFILE_DIR", "").strip()
_STARTUP_TIMEOUT = float(os.environ.get("BROWSER_STARTUP_TIMEOUT", "90"))
# How long the liveness probe waits for the server's HTTP listener to answer. Short: this
# runs on the tool-call path against a loopback socket.
_HEALTH_TIMEOUT = float(os.environ.get("BROWSER_HEALTH_TIMEOUT", "5"))

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


def profile_dir() -> str:
    """Path of the browser profile this server should use.

    A *stable* directory (so cookies/logins survive a gateway restart, which is the point
    of using a persistent profile) that we also control — letting us clear a stale lock
    left behind by an orphaned browser from a previous run.
    """
    if _PROFILE_DIR:
        return _PROFILE_DIR
    return str(Path(platformdirs.user_cache_dir("psi-agent")) / "browser-profile")


def _lock_path(directory: str) -> Path:
    return Path(directory) / ("lockfile" if _IS_WINDOWS else "SingletonLock")


def _reclaim_profile(directory: str) -> bool:
    """Try to make *directory* usable as a browser profile; return True if it is.

    Chromium refuses to launch on a profile another process already holds — Playwright MCP
    turns that into ``Browser is already in use for <dir>``. Because our server itself
    starts up *fine* in that case, the failure surfaced on every single tool call with
    nothing in the server log to explain it.

    Two situations, distinguished by whether the lock file can be removed:

    - **Stale lock** (previous browser is gone): unlink succeeds, the profile is ours.
    - **Live holder** (an orphaned browser from an earlier run is still running): the OS
      keeps the file busy. We report False and leave that browser strictly alone — it may
      be showing the user a login/QR page, so killing it is exactly the behaviour the tool
      contract forbids.
    """
    lock = _lock_path(directory)
    if not lock.exists() and not lock.is_symlink():
        return True
    try:
        lock.unlink()
    except OSError as exc:
        logger.debug(f"Browser profile {directory} is still held (lock busy): {exc}")
        return False
    logger.info(f"Cleared a stale browser profile lock at {lock}")
    return True


def _launch_profile_dir() -> str:
    """Pick the profile directory to launch on, avoiding a profile still in use.

    Prefers the stable profile (so cookies/logins persist across restarts). If an orphaned
    browser from an earlier run still holds it, fall back to a sibling directory instead of
    failing every tool call — a fresh profile is a far better outcome than a browser tool
    that is permanently broken until the host is rebooted.
    """
    primary = profile_dir()
    with suppress(OSError):
        Path(primary).mkdir(parents=True, exist_ok=True)
    if _reclaim_profile(primary):
        return primary
    fallback = f"{primary}-{os.getpid()}"
    with suppress(OSError):
        Path(fallback).mkdir(parents=True, exist_ok=True)
    _reclaim_profile(fallback)
    logger.warning(f"Browser profile {primary} is in use by another browser; using {fallback} instead.")
    return fallback


def _build_command(npx: str, port: int, user_data_dir: str | None = None) -> list[str]:
    cmd = [
        npx,
        "-y",
        _MCP_PACKAGE,
        "--port",
        str(port),
        "--browser",
        _BROWSER_CHANNEL,
        "--shared-browser-context",
        # Pin the profile instead of letting Playwright MCP hash it from the cwd, so we can
        # clear a stale lock before launch (see _reclaim_profile).
        "--user-data-dir",
        user_data_dir or profile_dir(),
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


def _is_endpoint_alive(endpoint: str) -> bool:
    """True if the MCP endpoint's HTTP listener still answers requests.

    ``proc.poll() is None`` only proves the *process* exists — a Playwright MCP server
    can go half-dead: the port stays bound (so a TCP connect succeeds) while the server
    no longer serves MCP. A liveness check therefore has to send a real request and read
    a real status line.

    We POST an empty body to the endpoint. A healthy server rejects it with a 4xx
    (``400 Bad Request`` — it wanted a JSON-RPC body), which is exactly the signal we
    want: *something is listening and speaking HTTP*. Any status at all counts as alive;
    only a refused/hung/socket-level failure counts as dead.
    """
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


def ensure_server() -> str:
    """Start the Playwright MCP server if needed and return its endpoint URL.

    Idempotent and thread-safe: repeated calls reuse a **healthy** running process.

    Reuse used to be decided by ``proc.poll() is None`` alone, which made a broken server
    permanent: once Playwright MCP went half-dead (port still bound, requests failing) or
    was killed out from under us, every later call was handed back the same stale endpoint
    and the browser tools stayed broken for the life of the gateway. Now an existing server
    must also pass :func:`_is_endpoint_alive`; an unhealthy one is torn down (freeing its
    browser profile lock) and replaced.

    Raises :class:`BrowserServerError` if Node/npx is missing or the server fails to start.
    """
    global _proc, _endpoint
    with _lock:
        proc, endpoint = _proc, _endpoint
        if proc is not None and endpoint:
            if proc.poll() is None and _is_endpoint_alive(endpoint):
                return endpoint
            logger.warning(
                f"Playwright MCP server at {endpoint} is not healthy "
                f"(exit={proc.poll()}); replacing it with a fresh server."
            )
            _proc, _endpoint = None, None
            _terminate_tree(proc)

        npx = _find_npx()
        port = _free_port()
        cmd = _build_command(npx, port, _launch_profile_dir())
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
                cwd=_paths.workspace_dir() or None,
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
