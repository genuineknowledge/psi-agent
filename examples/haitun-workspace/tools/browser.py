"""Browser automation tools — Playwright MCP driving the system browser.

Exposes Playwright MCP's native ``browser_*`` tools (``browser_navigate``,
``browser_snapshot``, ``browser_click``, ``browser_type``, ``browser_press_key``,
``browser_navigate_back``, ``browser_console_messages``, ``browser_handle_dialog``,
``browser_take_screenshot`` and more) as first-class workspace tools.

How it wires together:

- :mod:`_browser_impl` launches one long-lived ``npx @playwright/mcp`` server
  (system Edge, ``--shared-browser-context``) and returns its HTTP endpoint.
- The :func:`_mcp.mcp` decorator connects to that endpoint at import time,
  enumerates the server's tools, and generates an async function per tool.
- ``prefix=""`` is passed because Playwright's tool names already start with
  ``browser_``; the default ``<func>_`` prefix would produce
  ``browser_browser_navigate``.

Prerequisites: Node.js / ``npx`` on PATH, and a system browser (Edge by default).
The first run may download the ``@playwright/mcp`` package. If Node is missing the
tools are skipped at load time with a logged error rather than crashing tool loading.

Env knobs (all optional): ``BROWSER_CHANNEL`` (default ``msedge``),
``BROWSER_HEADLESS`` (default headed/visible; set ``1`` for headless on displayless
hosts), ``BROWSER_CAPS`` (default ``vision,devtools``), ``BROWSER_MCP_PACKAGE``,
``BROWSER_STARTUP_TIMEOUT``.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _browser_impl as _b
from _mcp import mcp


@mcp
def browser() -> dict[str, object]:
    """For simple page reads prefer ``search`` + ``fetch`` (faster, cheaper). Reach for
    the browser tools when you need real interaction: clicking, typing into forms,
    scrolling to reveal content, reading console/network activity, handling dialogs, or
    seeing the page via a screenshot. Call ``browser_navigate`` first to open a page,
    then ``browser_snapshot`` to get ref IDs for clicking/typing. State persists across
    calls — the same browser window stays open for the whole conversation and is NOT
    closed between messages.

    When a page looks blank:
    - Right after ``browser_navigate`` a page may still be rendering (this is common on
      search-result pages and other SPAs). If ``browser_snapshot`` comes back blank,
      near-empty, or shows ``about:blank``, do NOT conclude the page is empty or that the
      load failed. Wait and retry: call ``browser_wait_for`` (for the text you expect, or a
      short delay) then ``browser_snapshot`` again, or re-run ``browser_navigate`` on the
      same URL. Only report a blank/broken page after retrying.

    Keeping pages open (important — this is about user experience):
    - Do NOT call ``browser_close`` on your own initiative. Every page you open stays OPEN.
      A page is closed only when the user closes it themselves, or when the user has
      explicitly told you to close it.
    - When a site needs the user to sign in — log in, scan a QR code, grant authorization,
      solve a captcha — do NOT close the page and do NOT treat it as a failure. Leave the
      page open, tell the user exactly what to do (e.g. "scan the login QR code in the
      open window"), and let the USER decide how to proceed. Pause and wait for the user
      rather than abandoning or closing the page.
    - When you finish a task, never silently close the tab. Ask the user whether they want
      the page closed, and call ``browser_close`` only after they confirm."""
    endpoint = _b.ensure_server()
    # Playwright MCP binds the open browser tab to the HTTP session; don't terminate the
    # session when a per-call connection closes, or the page resets between tool calls.
    return {"transport": "http", "url": endpoint, "prefix": "", "terminate_on_close": False}
