"""Tests for the ``browser_cdp`` tool and its private CDP helper.

No real browser or npx process is launched. We exercise:

- browser discovery + command construction (``_find_browser`` / ``_build_command``),
- ``CDP_ENDPOINT`` override resolution (``ws://`` pass-through; ``http://`` -> WS URL),
- HTTP-origin parsing from a WS URL,
- the public tool's JSON contract on success and on a ``CDPError`` (transport failure),
  with ``send_command`` monkeypatched so no socket is opened.
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_browser_cdp_impl")
_tool: Any = importlib.import_module("browser_cdp")


# ── browser discovery + command construction ─────────────────────────────────


def test_build_command_defaults(tmp_path: Path) -> None:
    cmd = _impl._build_command("msedge", 9333, str(tmp_path))
    assert cmd[0] == "msedge"
    assert "--remote-debugging-port=9333" in cmd
    assert f"--user-data-dir={tmp_path}" in cmd
    assert "--remote-debugging-address=127.0.0.1" in cmd
    # Headed by default: no headless flag unless opted in.
    assert not any(c.startswith("--headless") for c in cmd)


def test_build_command_headless_toggle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_impl, "_headless_flag", lambda: True)
    cmd = _impl._build_command("chrome", 1, str(tmp_path))
    assert "--headless=new" in cmd


def test_find_browser_prefers_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _which(name: str) -> str | None:
        seen.append(name)
        return f"/usr/bin/{name}" if name == "msedge" else None

    monkeypatch.setattr(_impl.shutil, "which", _which)
    monkeypatch.setattr(_impl, "_BROWSER_CHANNEL", "")
    assert _impl._find_browser() == "/usr/bin/msedge"
    # Edge candidate is tried before any chrome candidate.
    assert seen[0] == "msedge"


def test_find_browser_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl.shutil, "which", lambda _name: None)
    monkeypatch.setattr(_impl, "_IS_WINDOWS", False)
    with pytest.raises(_impl.CDPError, match="Chromium"):
        _impl._find_browser()


# ── WS URL / origin parsing ───────────────────────────────────────────────────


def test_http_origin_from_ws_url() -> None:
    ws = "ws://127.0.0.1:9222/devtools/page/ABC123"
    assert _impl._http_origin(ws) == "http://127.0.0.1:9222"


# ── CDP_ENDPOINT override resolution ──────────────────────────────────────────


def test_resolve_override_ws_passthrough() -> None:
    async def _run() -> str:
        return await _impl._resolve_override("ws://host:9222/devtools/browser/x")

    assert anyio.run(_run) == "ws://host:9222/devtools/browser/x"


def test_ensure_endpoint_uses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """When CDP_ENDPOINT is set, no browser is launched; the override is resolved."""
    monkeypatch.setenv("CDP_ENDPOINT", "ws://example:1/devtools/browser/z")

    def _no_launch() -> str:  # pragma: no cover - must not be reached
        raise AssertionError("must not launch a browser when CDP_ENDPOINT is set")

    monkeypatch.setattr(_impl, "_find_browser", _no_launch)

    async def _run() -> str:
        return await _impl.ensure_endpoint()

    assert anyio.run(_run) == "ws://example:1/devtools/browser/z"


# ── public tool JSON contract ─────────────────────────────────────────────────


def test_tool_is_async_with_signature() -> None:
    assert inspect.iscoroutinefunction(_tool.browser_cdp)
    params = inspect.signature(_tool.browser_cdp).parameters
    assert "method" in params and "params" in params and "target" in params


def test_tool_registers_via_toolfunction() -> None:
    """The tool loader only accepts str/int/float/bool/list[X] params — a ``dict``
    annotation makes ``ToolFunction.from_callable`` raise and the tool is silently
    skipped at load time. Guard against a regression to a dict-typed ``params``."""
    tf = ToolFunction.from_callable(_tool.browser_cdp)
    props = tf.parameters["properties"]
    assert props["params"]["type"] == "string"
    assert props["method"]["type"] == "string"
    # method has no default -> required; params has a default -> optional.
    assert "method" in tf.parameters["required"]
    assert "params" not in tf.parameters["required"]


def test_tool_parses_json_string_params(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_send(method: str, params: Any, *, target: str, timeout_s: float) -> dict[str, Any]:
        assert method == "Page.navigate"
        assert params == {"url": "https://example.com"}
        return {"ok": True, "method": method, "result": {"frameId": "F1"}}

    monkeypatch.setattr(_impl, "send_command", _fake_send)

    async def _run() -> str:
        return await _tool.browser_cdp("Page.navigate", '{"url": "https://example.com"}')

    out = json.loads(anyio.run(_run))
    assert out["ok"] is True
    assert out["result"] == {"frameId": "F1"}


def test_tool_empty_params_sends_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_send(method: str, params: Any, *, target: str, timeout_s: float) -> dict[str, Any]:
        assert params == {}
        return {"ok": True, "method": method, "result": {}}

    monkeypatch.setattr(_impl, "send_command", _fake_send)

    async def _run() -> str:
        return await _tool.browser_cdp("Page.enable")

    assert json.loads(anyio.run(_run))["ok"] is True


def test_tool_rejects_malformed_json_params() -> None:
    async def _run() -> str:
        return await _tool.browser_cdp("Page.navigate", "{not json}")

    out = json.loads(anyio.run(_run))
    assert out["ok"] is False
    assert "not valid JSON" in out["message"]


def test_tool_rejects_non_object_params() -> None:
    async def _run() -> str:
        return await _tool.browser_cdp("Page.navigate", "[1, 2, 3]")

    out = json.loads(anyio.run(_run))
    assert out["ok"] is False
    assert "JSON object" in out["message"]


def test_tool_wraps_cdp_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(method: str, params: Any, *, target: str, timeout_s: float) -> dict[str, Any]:
        raise _impl.CDPError("No Chromium-family browser found.")

    monkeypatch.setattr(_impl, "send_command", _boom)

    async def _run() -> str:
        return await _tool.browser_cdp("Browser.getVersion", target="browser")

    out = json.loads(anyio.run(_run))
    assert out["ok"] is False
    assert "Chromium" in out["message"]
    assert out["method"] == "Browser.getVersion"
