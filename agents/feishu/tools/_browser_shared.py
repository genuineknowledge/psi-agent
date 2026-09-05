"""Single-Chromium owner for the Playwright-MCP ``browser_*`` tools.

One headed system browser (Edge, then Chrome) with ``--remote-debugging-port`` and one
isolated profile is launched lazily and drives the whole ``browser_*`` family: the
Playwright-MCP server (see ``_browser_impl``) attaches to it through ``--cdp-endpoint``,
so a session has exactly **one** browser window no matter how many times the tools are
called — no new window per call.

**User-close contract.** The browser is a normal window the user may close at any time.
Closing it mid-task is *respected*, not fought:

- The **first** tool call that notices the browser is gone returns a clear message and
  does **not** relaunch, so the agent stops and tells the user instead of silently
  popping a new window over their head.
- Only the **next** call (typically after the user answered "yes, continue") relaunches.
- A process that never launched a browser launches one on first use, as before.

Torn down — whole child tree — at interpreter exit (``atexit``). Set ``CDP_ENDPOINT``
to skip managing a browser and attach to an already-running one instead (the async
callers handle that override, not this module).
"""

from __future__ import annotations

import atexit
import http.client
import json
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

import platformdirs
from loguru import logger

_IS_WINDOWS = sys.platform == "win32"

# Message surfaced (exactly once per closure) when the user closed the shared browser.
CLOSED_MESSAGE = (
    "浏览器窗口已被关闭(可能是被你手动关闭或浏览器崩溃)。为尊重你的操作, 我没有擅自重新打开。"
    "如果你希望我继续用浏览器, 请明确回复继续, 我再重新打开。"
)


class BrowserUnavailableError(RuntimeError):
    """Raised when the shared browser is gone and must not be silently relaunched."""


# Env knobs.
_BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL", os.environ.get("CDP_BROWSER_CHANNEL", "")).strip().lower()
_PROFILE_DIR = os.environ.get("BROWSER_PROFILE_DIR", "").strip()
_STARTUP_TIMEOUT = float(os.environ.get("CDP_STARTUP_TIMEOUT", os.environ.get("BROWSER_STARTUP_TIMEOUT", "60")))
_PROBE_TIMEOUT = float(os.environ.get("CDP_HEALTH_TIMEOUT", "2"))

_lock = threading.Lock()
_proc: subprocess.Popen[bytes] | None = None
_origin: str | None = None  # e.g. http://127.0.0.1:12345
_profile: str | None = None
_closed_reported: bool = False  # we reported a user-close once; the next call may relaunch


class BrowserServerError(RuntimeError):
    """Raised when no Chromium-family browser can be started."""


# -- locating a Chromium-family browser ------------------------------------------

_CANDIDATES: dict[str, tuple[str, ...]] = {
    "msedge": ("msedge", "microsoft-edge", "microsoft-edge-stable"),
    "chrome": ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"),
}

_WINDOWS_REL: dict[str, str] = {
    "msedge": r"Microsoft\Edge\Application\msedge.exe",
    "chrome": r"Google\Chrome\Application\chrome.exe",
}


def _windows_fallbacks(channel: str) -> list[str]:
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    rel = _WINDOWS_REL[channel]
    return [os.path.join(root, rel) for root in roots if root]


def _find_browser() -> str:
    order = ("chrome", "msedge") if _BROWSER_CHANNEL == "chrome" else ("msedge", "chrome")
    for channel in order:
        for name in _CANDIDATES[channel]:
            found = shutil.which(name)
            if found:
                return found
        if _IS_WINDOWS:
            for path in _windows_fallbacks(channel):
                if os.path.isfile(path):
                    return path
    raise BrowserServerError("No Chromium-family browser (Edge/Chrome) found. Install one or put it on PATH.")


# -- profile management (stable so logins/cookies survive restarts) ---------------


def _lock_path(directory: str) -> Path:
    return Path(directory) / ("lockfile" if _IS_WINDOWS else "SingletonLock")


def _reclaim_profile(directory: str) -> bool:
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


def _profile_dir() -> str:
    if _PROFILE_DIR:
        return _PROFILE_DIR
    primary = str(Path(platformdirs.user_cache_dir("psi-agent")) / "browser-profile")
    with suppress(OSError):
        Path(primary).mkdir(parents=True, exist_ok=True)
    if _reclaim_profile(primary):
        return primary
    fallback = f"{primary}-{os.getpid()}"
    with suppress(OSError):
        Path(fallback).mkdir(parents=True, exist_ok=True)
    _reclaim_profile(fallback)
    logger.warning(f"Browser profile {primary} is in use; using {fallback} instead.")
    return fallback


# -- launching / probing / tearing down -------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _headless() -> bool:
    for name in ("BROWSER_HEADLESS", "CDP_HEADLESS"):
        if os.environ.get(name, "0").strip().lower() in {"1", "true", "yes"}:
            return True
    return False


def _launch_command(browser: str, port: int, profile: str) -> list[str]:
    cmd = [
        browser,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-fre",
    ]
    if _headless():
        cmd.append("--headless=new")
    return cmd


def _split_origin(origin: str) -> tuple[str, int]:
    rest = origin.split("://", 1)[-1]
    authority = rest.split("/", 1)[0]
    host, _, port = authority.partition(":")
    return host or "127.0.0.1", int(port or 0)


def _http_alive(origin: str) -> bool:
    """True if the browser's CDP HTTP listener answers (i.e. the browser is running)."""
    host, port = _split_origin(origin)
    conn = http.client.HTTPConnection(host, port, timeout=_PROBE_TIMEOUT)
    try:
        conn.request("GET", "/json/version")
        resp = conn.getresponse()
        resp.read(0)
        return resp.status == 200
    except OSError, http.client.HTTPException:
        return False
    finally:
        with suppress(Exception):
            conn.close()


def _launch() -> str:
    """Launch the shared browser, wait for its CDP endpoint, and return the origin."""
    global _proc, _origin, _profile, _closed_reported
    browser = _find_browser()
    port = _free_port()
    profile = _profile_dir()
    cmd = _launch_command(browser, port, profile)
    logger.info(f"Starting shared browser: {' '.join(cmd)}")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=not _IS_WINDOWS,
        )
    except OSError as exc:
        raise BrowserServerError(f"Failed to launch browser: {exc}") from exc

    origin = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _http_alive(origin):
            _proc, _profile, _origin, _closed_reported = proc, profile, origin, False
            atexit.register(_shutdown)
            logger.info(f"Shared browser ready at {origin}")
            return origin
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    _terminate_tree(proc)
    raise BrowserServerError(f"Browser did not open its debugging port within {_STARTUP_TIMEOUT:.0f}s.")


def ensure_origin() -> str:
    """Return the ``http://host:port`` origin of the shared browser, launching/reusing it.

    Thread-safe (plain lock; callers run this in a worker thread or ``anyio.to_thread``).

    User-close handling: when the browser we launched is gone, the **first** call raises
    :class:`BrowserUnavailableError` (with :data:`CLOSED_MESSAGE`) instead of relaunching,
    so the tool can tell the agent to inform the user. The next call relaunches.
    """
    global _proc, _closed_reported
    with _lock:
        if _proc is not None and _origin and _http_alive(_origin):
            return _origin
        if _proc is not None:
            if not _closed_reported:
                _closed_reported = True
                logger.info("Shared browser is gone; reporting once and not relaunching.")
                raise BrowserUnavailableError(CLOSED_MESSAGE)
            logger.info("User confirmed; relaunching the shared browser.")
        return _launch()


def current_origin() -> str | None:
    """The origin of the browser this process currently manages, if any (no probe)."""
    return _origin


def endpoint_ws() -> str:
    """Fetch the browser-level WebSocket debugger URL from the shared browser's origin."""
    if _origin is None:
        raise BrowserUnavailableError(CLOSED_MESSAGE)
    host, port = _split_origin(_origin)
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/json/version")
        resp = conn.getresponse()
        data = resp.read()
        ws = json.loads(data).get("webSocketDebuggerUrl")
        return str(ws) if ws else ""
    finally:
        with suppress(Exception):
            conn.close()


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
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


def _shutdown() -> None:
    global _proc, _origin, _profile
    proc, profile = _proc, _profile
    _proc, _origin, _profile = None, None, None
    if proc is not None and proc.poll() is None:
        _terminate_tree(proc)
    if profile:
        with suppress(Exception):
            shutil.rmtree(profile, ignore_errors=True)
