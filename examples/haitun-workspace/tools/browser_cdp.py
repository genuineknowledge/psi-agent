"""Raw Chrome DevTools Protocol tool — send a low-level CDP command to a browser.

The ``browser_*`` tools (Playwright MCP) cover ordinary interaction: navigate, click,
type, snapshot. ``browser_cdp`` is the escape hatch for everything they don't wrap — any
raw CDP command in any domain (``Page.*``, ``Network.*``, ``Emulation.*``, ``Runtime.*``,
``Browser.*``, ``Target.*``, …), e.g. capturing a full-page PDF, throttling the network,
overriding geolocation, or reading the accessibility tree.

It talks to a dedicated debug browser it launches on first use (Edge, then Chrome, with
``--remote-debugging-port`` and an isolated profile — separate from the Playwright MCP
browser), or to an already-running browser when ``CDP_ENDPOINT`` is set. CDP is a
JSON-over-WebSocket protocol; this sends one command and returns its reply.

Prerequisites: a Chromium-family browser (Edge/Chrome) installed, OR ``CDP_ENDPOINT``
pointing at a browser started with ``--remote-debugging-port``. All IO is async
(``aiohttp`` WebSocket, already a core dependency — no extra packages).

Env knobs (all optional): ``CDP_ENDPOINT`` (connect to an existing browser instead of
launching), ``CDP_BROWSER_CHANNEL`` (``msedge``/``chrome``), ``CDP_HEADLESS`` (``1``/``0``,
default headed), ``CDP_STARTUP_TIMEOUT``, ``CDP_COMMAND_TIMEOUT``.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import json
from typing import Any

import _browser_cdp_impl as _c


async def browser_cdp(
    method: str,
    params: dict[str, Any] | None = None,
    target: str = "page",
    timeout_s: float = 30.0,
) -> str:
    """Send a raw Chrome DevTools Protocol command to the browser and return its reply.

    Use this when the higher-level ``browser_*`` tools don't expose what you need: any CDP
    domain method works. Examples: ``Page.navigate`` ``{"url": "https://example.com"}``;
    ``Page.printToPDF`` ``{}`` to render the current page to a PDF (base64 in the result);
    ``Emulation.setGeolocationOverride`` ``{"latitude": 48.8, "longitude": 2.3, "accuracy": 1}``;
    ``Network.emulateNetworkConditions`` to throttle; ``Runtime.evaluate``
    ``{"expression": "document.title", "returnByValue": true}``.

    On first use a dedicated debug browser (Edge, then Chrome) is launched with remote
    debugging and reused across calls; set ``CDP_ENDPOINT`` (e.g. ``http://localhost:9222``)
    to target an already-running browser instead. Many domains must be enabled first —
    e.g. call ``Network.enable`` before ``Network.*`` events, or ``Page.enable`` before
    some ``Page`` methods.

    Args:
        method: CDP method name, ``Domain.command`` (e.g. ``"Page.navigate"``). Required.
        params: The command's parameters object. Defaults to ``{}``.
        target: Which endpoint to send to. ``"page"`` (default) targets the first open
            page — use for ``Page``/``Runtime``/``DOM``/``Network``/``Emulation``. ``"browser"``
            targets the browser-level endpoint — use for ``Browser``/``Target``/``SystemInfo``.
        timeout_s: Seconds to wait for the reply (default 30).

    Returns:
        JSON string. On success ``{"ok": true, "method", "result": {...}}`` where
        ``result`` is the raw CDP result object. On a CDP-level error
        ``{"ok": false, "method", "error": {...}}``. On a transport/launch failure
        ``{"ok": false, "method", "message": "..."}``.
    """
    try:
        result = await _c.send_command(method, params, target=target, timeout_s=timeout_s)
    except _c.CDPError as exc:
        return json.dumps({"ok": False, "method": method, "message": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)
