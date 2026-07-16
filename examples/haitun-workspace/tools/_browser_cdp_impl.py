"""Private helper for the ``browser_cdp`` tool — raw Chrome DevTools Protocol.

The ``browser_cdp`` tool sends a *raw* CDP command (``{"method": ..., "params": ...}``)
to a Chromium browser and returns its reply. CDP is a JSON request/response protocol
carried over a WebSocket, so this module needs two things:

1. **A browser with the debugging port open.** By default we launch a *dedicated* system
   browser (Edge, then Chrome) with ``--remote-debugging-port`` and an isolated profile,
   kept separate from the Playwright MCP browser the ``browser_*`` tools drive so the two
   never fight over one process. The launch is lazy (first call), reused across calls, and
   torn down — whole child tree — at interpreter exit. Set ``CDP_ENDPOINT`` to point at an
   already-running browser instead (e.g. ``http://localhost:9222``); then nothing is
   launched or managed.
2. **The WebSocket debugger URL.** Chromium exposes it over HTTP: ``/json/version`` gives
   the browser-level endpoint, ``/json`` lists page targets. We fetch it, open the
   WebSocket with :mod:`aiohttp` (already a core dependency), send the command, and read
   replies until the one matching our request id comes back.

All IO is async (``aiohttp`` for HTTP/WebSocket, ``anyio`` for sleeps/timeouts). The
subprocess launch and teardown mirror :mod:`_browser_impl`.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from itertools import count
from typing import Any

import aiohttp
import anyio
from loguru import logger

_IS_WINDOWS = sys.platform == "win32"

# Env knobs (all optional).
_CDP_ENDPOINT_ENV = "CDP_ENDPOINT"  # if set, connect here; do not launch/manage a browser
_BROWSER_CHANNEL = os.environ.get("CDP_BROWSER_CHANNEL", "").strip().lower()  # "msedge"/"chrome"/""
_STARTUP_TIMEOUT = float(os.environ.get("CDP_STARTUP_TIMEOUT", "30"))
_DEFAULT_COMMAND_TIMEOUT = float(os.environ.get("CDP_COMMAND_TIMEOUT", "30"))

_lock = threading.Lock()
_proc: subprocess.Popen[bytes] | None = None
_endpoint: str | None = None
_profile_dir: str | None = None
_ids = count(1)


class CDPError(RuntimeError):
    """Raised when a debug browser cannot be started or a CDP command fails."""


# ── locating a Chromium-family browser ───────────────────────────────────────

# PATH names to try, in order. Edge first (matches the browser tool's default channel),
# then Chrome/Chromium. ``CDP_BROWSER_CHANNEL`` can pin one.
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "msedge": ("msedge", "microsoft-edge", "microsoft-edge-stable"),
    "chrome": ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"),
}


# Well-known Windows install locations (not on PATH by default), per channel.
_WINDOWS_REL: dict[str, str] = {
    "msedge": r"Microsoft\Edge\Application\msedge.exe",
    "chrome": r"Google\Chrome\Application\chrome.exe",
}


def _windows_fallbacks(channel: str) -> list[str]:
    """Candidate absolute paths for *channel* under the common install roots."""
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    rel = _WINDOWS_REL[channel]
    return [os.path.join(root, rel) for root in roots if root]


def _find_browser() -> str:
    """Return the path to a Chromium-family browser executable, or raise ``CDPError``.

    Preference is Edge, then Chrome (``CDP_BROWSER_CHANNEL`` flips it). For each channel
    we try PATH first, then the well-known Windows install locations — kept browser-major
    so the preferred channel wins even when only the other is on PATH / a standard root.
    """
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
    raise CDPError(
        "No Chromium-family browser (Edge/Chrome) found. Install one, put it on PATH, "
        "or set CDP_ENDPOINT to an already-running browser started with "
        "--remote-debugging-port."
    )


# ── launching / reusing the debug browser ────────────────────────────────────


def _free_port() -> int:
    """Grab an OS-assigned free localhost port, then release it for the browser."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _headless_flag() -> bool:
    return os.environ.get("CDP_HEADLESS", "0").strip().lower() in {"1", "true", "yes"}


def _build_command(browser: str, port: int, profile_dir: str) -> list[str]:
    """Command line for a dedicated, isolated debug browser instance."""
    cmd = [
        browser,
        f"--remote-debugging-port={port}",
        # Bind the debug endpoint to loopback only.
        "--remote-debugging-address=127.0.0.1",
        # Isolate from the user's real profile and any Playwright-driven instance so the
        # two browsers never share a process / lock.
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-fre",
    ]
    if _headless_flag():
        cmd.append("--headless=new")
    return cmd


async def _endpoint_from(port: int) -> str | None:
    """Return the browser-level WebSocket debugger URL if the HTTP endpoint is up."""
    url = f"http://127.0.0.1:{port}/json/version"
    timeout = aiohttp.ClientTimeout(total=2.0)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url) as resp,
        ):
            if resp.status != 200:
                return None
            data = await resp.json()
    except aiohttp.ClientError, TimeoutError, ValueError:
        return None
    ws = data.get("webSocketDebuggerUrl")
    return str(ws) if ws else None


async def _wait_for_endpoint(port: int) -> str:
    """Poll ``/json/version`` until the browser answers; return its WS debugger URL."""
    try:
        with anyio.fail_after(_STARTUP_TIMEOUT):
            while True:
                ws = await _endpoint_from(port)
                if ws:
                    return ws
                if _proc is not None and _proc.poll() is not None:
                    raise CDPError(f"Debug browser exited during startup (code {_proc.returncode}).")
                await anyio.sleep(0.1)
    except TimeoutError:
        raise CDPError(f"Debug browser did not open its debugging port within {_STARTUP_TIMEOUT:.0f}s.") from None


# The async lock guards the lazy single-launch. It lives on the running loop; the tool
# always runs on one gateway loop, so a plain module-level lock is correct here.
_alock = anyio.Lock()


async def ensure_endpoint() -> str:
    """Return a browser-level WS debugger URL, launching a debug browser if needed.

    If ``CDP_ENDPOINT`` is set, resolve it (HTTP host:port -> its ``webSocketDebuggerUrl``,
    or pass a ``ws(s)://`` URL through unchanged) without launching anything. Otherwise
    launch a dedicated Edge/Chrome once and reuse it across calls.
    """
    override = os.environ.get(_CDP_ENDPOINT_ENV, "").strip()
    if override:
        return await _resolve_override(override)

    global _proc, _endpoint, _profile_dir
    async with _alock:
        if _proc is not None and _proc.poll() is None and _endpoint:
            return _endpoint

        browser = _find_browser()
        port = _free_port()
        profile_dir = tempfile.mkdtemp(prefix="psi-cdp-profile-")
        cmd = _build_command(browser, port, profile_dir)
        logger.info(f"Starting debug browser: {' '.join(cmd)}")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
        start_new_session = not _IS_WINDOWS
        try:
            proc = subprocess.Popen(  # noqa: ASYNC220 - quick, non-blocking spawn; readiness is awaited via HTTP polling
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as exc:
            with suppress(Exception):
                shutil.rmtree(profile_dir, ignore_errors=True)
            raise CDPError(f"Failed to launch debug browser: {exc}") from exc

        _proc, _profile_dir = proc, profile_dir
        try:
            endpoint = await _wait_for_endpoint(port)
        except CDPError:
            _shutdown()
            raise
        _endpoint = endpoint
        atexit.register(_shutdown)
        logger.info(f"Debug browser ready; CDP endpoint {endpoint}")
        return endpoint


async def _resolve_override(value: str) -> str:
    """Turn a ``CDP_ENDPOINT`` value into a WebSocket debugger URL."""
    if value.startswith(("ws://", "wss://")):
        return value
    if value.startswith(("http://", "https://")):
        base = value.rstrip("/")
        url = f"{base}/json/version"
        timeout = aiohttp.ClientTimeout(total=5.0)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url) as resp,
            ):
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise CDPError(f"Could not read CDP endpoint at {url}: {exc}") from exc
        ws = data.get("webSocketDebuggerUrl")
        if not ws:
            raise CDPError(f"No webSocketDebuggerUrl at {url}.")
        return str(ws)
    # Bare host:port -> treat as http.
    return await _resolve_override(f"http://{value}")


def _shutdown() -> None:
    global _proc, _endpoint, _profile_dir
    proc, profile_dir = _proc, _profile_dir
    _proc, _endpoint, _profile_dir = None, None, None
    if proc is not None and proc.poll() is None:
        _terminate_tree(proc)
    if profile_dir:
        with suppress(Exception):
            shutil.rmtree(profile_dir, ignore_errors=True)


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the browser and every child it spawned."""
    if _IS_WINDOWS:
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


# ── target (page) selection + sending a command ──────────────────────────────


async def _first_page_ws(base_http: str) -> str | None:
    """Return the WS debugger URL of the first ``page`` target, if any.

    A method like ``Page.*`` / ``Runtime.*`` / ``DOM.*`` must target a *page*, not the
    browser-level endpoint. ``base_http`` is the HTTP origin (``http://host:port``).
    """
    url = f"{base_http.rstrip('/')}/json"
    timeout = aiohttp.ClientTimeout(total=5.0)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url) as resp,
        ):
            targets = await resp.json()
    except aiohttp.ClientError, TimeoutError, ValueError:
        return None
    if not isinstance(targets, list):
        return None
    for t in targets:
        if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"])
    return None


def _http_origin(ws_url: str) -> str:
    """Best-effort HTTP origin (``http://host:port``) from a ``ws://host:port/...`` URL."""
    rest = ws_url.split("://", 1)[-1]
    authority = rest.split("/", 1)[0]
    return f"http://{authority}"


async def send_command(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    target: str = "page",
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Send one raw CDP command and return the parsed result dict.

    ``target`` selects the WebSocket endpoint:
    - ``"page"`` (default): the first open page target — required for ``Page.*``,
      ``Runtime.*``, ``DOM.*``, ``Network.*`` and most domains.
    - ``"browser"``: the browser-level endpoint — for ``Browser.*``, ``Target.*``,
      ``SystemInfo.*``.

    Returns ``{"ok": True, "method", "result"}`` on success, or
    ``{"ok": False, "method", "error"}`` when CDP reports a command error.
    """
    if not method or not isinstance(method, str):
        raise CDPError("A non-empty CDP 'method' string is required (e.g. 'Page.navigate').")

    browser_ws = await ensure_endpoint()
    ws_url = browser_ws
    if target == "page":
        page_ws = await _first_page_ws(_http_origin(browser_ws))
        if page_ws:
            ws_url = page_ws
        # No page target (e.g. a just-launched browser with only about:blank not yet
        # listed): fall back to the browser endpoint rather than failing.

    request_id = next(_ids)
    payload = json.dumps({"id": request_id, "method": method, "params": params or {}})
    total = timeout_s if timeout_s and timeout_s > 0 else _DEFAULT_COMMAND_TIMEOUT
    timeout = aiohttp.ClientTimeout(total=total + 5.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session, session.ws_connect(ws_url) as ws:
            await ws.send_str(payload)
            with anyio.fail_after(total):
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            raise CDPError("CDP WebSocket closed before a reply arrived.")
                        continue
                    data = json.loads(msg.data)
                    # Events have no matching id; skip until our response comes back.
                    if data.get("id") != request_id:
                        continue
                    if "error" in data:
                        return {"ok": False, "method": method, "error": data["error"]}
                    return {"ok": True, "method": method, "result": data.get("result", {})}
            raise CDPError(f"Timed out after {total:.0f}s waiting for a reply to {method!r}.")
    except TimeoutError:
        raise CDPError(f"Timed out after {total:.0f}s waiting for a reply to {method!r}.") from None
    except aiohttp.ClientError as exc:
        raise CDPError(f"CDP WebSocket request failed: {type(exc).__name__}: {exc}") from exc
