"""Tests for the Haitun workspace ``browser`` tool and its MCP prefix plumbing.

These tests never launch a real browser or npx process. They exercise:

- ``_mcp.mcp`` prefix behaviour — ``prefix=""`` yields un-prefixed tool names (so
  Playwright's ``browser_navigate`` does not become ``browser_browser_navigate``),
  while the default keeps the historical ``<func>_`` prefix used by ``serper``.
- ``_browser_impl`` command construction and its clear error when npx is absent.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_mcp: Any = importlib.import_module("_mcp")
_browser_impl: Any = importlib.import_module("_browser_impl")


# ── _mcp prefix behaviour ────────────────────────────────────────────────────


@pytest.fixture
def _fake_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``mcp()`` skip the network: return two canned tool schemas."""

    def _discover(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "browser_navigate": {
                "name": "browser_navigate",
                "description": "Navigate to a URL.",
                "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            },
            "search": {
                "name": "search",
                "description": "Search the web.",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            },
        }

    monkeypatch.setattr(_mcp, "_discover", _discover)


def test_empty_prefix_keeps_native_names(_fake_discover: None) -> None:
    """``prefix=""`` must not double up the tool name."""
    ns: dict[str, Any] = {}
    # Run the decorator inside an exec'd module namespace so ``mcp()`` (which writes
    # generated tools into its caller's frame globals) has a namespace we can inspect.
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    # Empty prefix means each MCP tool name is used verbatim.
    assert "browser_navigate" in ns
    assert "search" in ns
    assert "browser_browser_navigate" not in ns


def test_default_prefix_uses_function_name(_fake_discover: None) -> None:
    """No ``prefix`` key -> historical ``<func>_`` prefix (serper_* behaviour)."""
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def serper():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp'}\n",
        ns,
    )
    assert "serper_search" in ns
    assert "serper_browser_navigate" in ns
    assert "search" not in ns


def test_generated_tool_is_async_with_signature(_fake_discover: None) -> None:
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    fn = ns["browser_navigate"]
    assert inspect.iscoroutinefunction(fn)
    assert "url" in inspect.signature(fn).parameters


# ── discovery-failure containment (regression: crashed the gateway) ───────────
#
# When the MCP server is unreachable or errors during discovery (e.g. Playwright MCP
# returning HTTP 502), the failure used to propagate — sometimes as a
# ``BaseExceptionGroup`` from the anyio/httpx teardown — past the tool loader's
# ``except Exception`` and take the whole gateway process down. The ``@mcp`` decorator
# must instead log and register no tools, so the rest of the workspace keeps loading.


def test_discovery_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain Exception during discovery must not escape ``@mcp``; no tools registered."""

    def _boom(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise RuntimeError("Server error '502 Bad Gateway'")

    monkeypatch.setattr(_mcp, "_discover", _boom)
    ns: dict[str, Any] = {}
    # Must not raise.
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    assert "browser_navigate" not in ns
    assert "browser" in ns  # the decorated declaration itself is still returned


def test_discovery_base_exception_group_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real failure mode: a ``BaseExceptionGroup`` (not an ``Exception``) must be caught.

    ``streamable_http_client`` teardown raised ``BaseExceptionGroup`` /
    ``RuntimeError('Attempted to exit cancel scope in a different task')`` which a plain
    ``except Exception`` cannot catch. ``@mcp`` must still contain it.
    """

    def _boom(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise BaseExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")],
        )

    monkeypatch.setattr(_mcp, "_discover", _boom)
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    assert "browser_navigate" not in ns


def test_fatal_signals_still_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyboardInterrupt / SystemExit must never be swallowed by the containment."""

    def _interrupt(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise KeyboardInterrupt

    monkeypatch.setattr(_mcp, "_discover", _interrupt)
    ns: dict[str, Any] = {}
    with pytest.raises(KeyboardInterrupt):
        exec(
            "from _mcp import mcp\n"
            "@mcp\n"
            "def browser():\n"
            "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
            ns,
        )


def test_is_fatal_classification() -> None:
    assert _mcp._is_fatal(KeyboardInterrupt())
    assert _mcp._is_fatal(SystemExit())
    assert not _mcp._is_fatal(RuntimeError("cancel scope"))
    assert not _mcp._is_fatal(BaseExceptionGroup("g", [RuntimeError("x")]))
    # A group carrying a fatal leaf is fatal.
    assert _mcp._is_fatal(BaseExceptionGroup("g", [KeyboardInterrupt()]))


def test_failed_tool_call_returns_error_string(_fake_discover: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed per-call invocation returns an ``Error:`` string, not a raised exception."""
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def browser():\n"
        "    return {'transport': 'http', 'url': 'http://localhost:1/mcp', 'prefix': ''}\n",
        ns,
    )
    fn = ns["browser_navigate"]

    def _boom(_config: dict[str, Any]) -> Any:
        # Mimic the transport teardown surfacing a BaseExceptionGroup (not an Exception).
        raise BaseExceptionGroup("teardown", [RuntimeError("peer closed connection")])

    monkeypatch.setattr(_mcp, "_connect", _boom)

    async def _call() -> str:
        return await fn(url="http://example.com")

    result = anyio.run(_call)
    assert result.startswith("Error:")
    assert "browser_navigate" in result


# ── _browser_impl command + error handling ───────────────────────────────────


def test_build_command_defaults() -> None:
    cmd = _browser_impl._build_command("npx", 12345)
    assert cmd[:3] == ["npx", "-y", _browser_impl._MCP_PACKAGE]
    assert "--port" in cmd and "12345" in cmd
    assert "--browser" in cmd
    assert "--shared-browser-context" in cmd
    # caps default is vision,devtools
    assert "--caps" in cmd


def test_build_command_headless_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_HEADLESS", "0")
    assert "--headless" not in _browser_impl._build_command("npx", 1)
    monkeypatch.setenv("BROWSER_HEADLESS", "1")
    assert "--headless" in _browser_impl._build_command("npx", 1)


def test_find_npx_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_browser_impl.shutil, "which", lambda _name: None)
    with pytest.raises(_browser_impl.BrowserServerError, match="npx"):
        _browser_impl._find_npx()


def test_ensure_server_propagates_missing_npx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_browser_impl.shutil, "which", lambda _name: None)
    # ensure no stale state short-circuits the check
    monkeypatch.setattr(_browser_impl, "_proc", None)
    monkeypatch.setattr(_browser_impl, "_endpoint", None)
    with pytest.raises(_browser_impl.BrowserServerError):
        _browser_impl.ensure_server()
