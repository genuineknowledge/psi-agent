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
