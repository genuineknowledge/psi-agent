"""Tests for the Haitun workspace ``canvas`` toolset and its MCP plumbing.

These tests never launch a real ``npx`` process or canvas server. They exercise:

- ``_mcp.mcp`` prefixing for the canvas declaration — Excalidraw's un-prefixed
  tool names (``create_element``) must become ``canvas_create_element`` (the
  default ``<func>_`` prefix), and a stdio config must be accepted.
- ``_canvas_impl`` command / env construction and its clear error when npx is
  absent.
- Discovery-failure containment: a missing ``npx`` (or a server that errors)
  must not crash tool loading — ``@mcp`` logs and registers no tools.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_mcp: Any = importlib.import_module("_mcp")
_canvas_impl: Any = importlib.import_module("_canvas_impl")


# ── _mcp prefix behaviour for the canvas declaration ─────────────────────────


@pytest.fixture
def _fake_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``mcp()`` skip the subprocess: return two canned Excalidraw schemas."""

    def _discover(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "create_element": {
                "name": "create_element",
                "description": "Create an element on the canvas.",
                "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}}, "required": ["type"]},
            },
            "describe_scene": {
                "name": "describe_scene",
                "description": "Describe the canvas as text.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        }

    monkeypatch.setattr(_mcp, "_discover", _discover)


def test_default_prefix_namespaces_canvas_tools(_fake_discover: None) -> None:
    """No ``prefix`` key -> ``canvas_`` prefix, so ``create_element`` stays namespaced."""
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    assert "canvas_create_element" in ns
    assert "canvas_describe_scene" in ns
    # The bare (un-prefixed) names must not leak — they'd collide with other tools.
    assert "create_element" not in ns
    assert "describe_scene" not in ns


def test_generated_canvas_tool_is_async_with_signature(_fake_discover: None) -> None:
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    fn = ns["canvas_create_element"]
    assert inspect.iscoroutinefunction(fn)
    assert "type" in inspect.signature(fn).parameters


def test_union_type_property_does_not_crash_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: Excalidraw emits union types (``"type": ["number", "array"]``).

    ``_build`` used to do ``_T.get(ps["type"])`` with the raw list, raising
    ``TypeError: unhashable type: 'list'`` and crashing the whole canvas.py load
    (no ``canvas_*`` tools registered). A union type must collapse to its first
    non-null member; a nullable array must still resolve to a ``list`` annotation.
    """

    def _discover(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "create_element": {
                "name": "create_element",
                "description": "Create an element.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        # union type: value may be a scalar or an array
                        "width": {"type": ["number", "array"]},
                        # nullable string (common JSON Schema union with null)
                        "label": {"type": ["string", "null"]},
                        # nullable array of numbers -> should stay list[...]
                        "points": {"type": ["array", "null"], "items": {"type": "number"}},
                    },
                    "required": ["width"],
                },
            }
        }

    monkeypatch.setattr(_mcp, "_discover", _discover)
    ns: dict[str, Any] = {}
    # Must not raise TypeError.
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    assert "canvas_create_element" in ns
    sig = inspect.signature(ns["canvas_create_element"])
    assert set(sig.parameters) == {"width", "label", "points"}


def test_object_and_object_array_params_degrade_to_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: batch_create_elements / apply have object & array-of-object params.

    The tool runtime only accepts str/int/float/bool/list[scalar], so it *skipped*
    ``canvas_batch_create_elements`` (``list[dict]``) and ``canvas_apply`` (nested
    ``dict``) entirely. ``_build`` must degrade those to ``str`` (JSON string) params
    so the tools load, and decode them back to structures before the MCP call.
    """

    def _discover(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "batch_create_elements": {
                "name": "batch_create_elements",
                "description": "Create many elements at once.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "elements": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["elements"],
                },
            },
            "apply": {
                "name": "apply",
                "description": "Multi-op patch.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"patch": {"type": "object"}},
                    "required": ["patch"],
                },
            },
        }

    monkeypatch.setattr(_mcp, "_discover", _discover)
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    # Both tools must now load, with their complex params typed as plain str.
    for tool, param in (("canvas_batch_create_elements", "elements"), ("canvas_apply", "patch")):
        assert tool in ns
        ann = inspect.signature(ns[tool]).parameters[param].annotation
        assert ann is str


def test_json_string_param_is_decoded_before_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A JSON-string arg for an object/array param is parsed back before call_tool."""

    def _discover(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "batch_create_elements": {
                "name": "batch_create_elements",
                "description": "Create many elements.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"elements": {"type": "array", "items": {"type": "object"}}},
                    "required": ["elements"],
                },
            }
        }

    captured: dict[str, Any] = {}

    class _FakeSession:
        async def initialize(self) -> None:
            return None

        async def call_tool(self, name: str, kw: dict[str, Any]) -> Any:
            captured["name"] = name
            captured["kw"] = kw
            # A minimal MCP-shaped result: _fmt reads .content (TextContent list).
            return SimpleNamespace(content=[], structuredContent={"ok": True}, isError=False)

    class _FakeConn:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(_mcp, "_discover", _discover)
    monkeypatch.setattr(_mcp, "_connect", lambda _cfg: _FakeConn())

    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    fn = ns["canvas_batch_create_elements"]
    anyio.run(lambda: fn(elements='[{"type": "rectangle", "x": 1}]'))
    # The JSON string must have been decoded into a real list of dicts before call_tool.
    assert captured["name"] == "batch_create_elements"
    assert captured["kw"]["elements"] == [{"type": "rectangle", "x": 1}]


def test_json_type_helper() -> None:
    """The ``_json_type`` normalizer collapses union/absent types to one string."""
    assert _mcp._json_type({"type": "string"}) == "string"
    assert _mcp._json_type({"type": ["string", "null"]}) == "string"
    assert _mcp._json_type({"type": ["null", "number"]}) == "number"
    assert _mcp._json_type({"type": ["array", "null"]}) == "array"
    # absent type (anyOf/oneOf/enum-only property) falls back to string
    assert _mcp._json_type({"enum": ["a", "b"]}) == "string"
    assert _mcp._json_type({}) == "string"


def test_stdio_config_resolves(_fake_discover: None) -> None:
    """A stdio declaration must resolve to a stdio transport with command + args."""
    resolved = _mcp._resolve(
        {"transport": "stdio", "command": "/usr/bin/npx", "args": ["-y", "mcp-excalidraw-server@latest"]}
    )
    assert resolved["transport"] == "stdio"
    assert resolved["command"] == "/usr/bin/npx"
    assert resolved["args"] == ["-y", "mcp-excalidraw-server@latest"]


# ── discovery-failure containment (must not crash tool loading) ───────────────


def test_discovery_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain Exception during discovery must not escape ``@mcp``; no tools registered."""

    def _boom(_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise RuntimeError("npx not found / server failed to start")

    monkeypatch.setattr(_mcp, "_discover", _boom)
    ns: dict[str, Any] = {}
    exec(
        "from _mcp import mcp\n"
        "@mcp\n"
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    assert "canvas_create_element" not in ns
    assert "canvas" in ns  # the decorated declaration itself is still returned


def test_discovery_base_exception_group_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``BaseExceptionGroup`` from stdio/anyio teardown must be caught, not propagate."""

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
        "def canvas():\n"
        "    return {'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}\n",
        ns,
    )
    assert "canvas_create_element" not in ns
    assert "canvas" in ns


# ── _canvas_impl command + env + error handling ───────────────────────────────


def test_build_command_uses_npx_and_package() -> None:
    cmd = _canvas_impl.build_command("npx")
    assert cmd[0] == "npx"
    assert cmd[1] == "-y"
    assert cmd[2] == _canvas_impl._MCP_PACKAGE
    assert "mcp-excalidraw-server" in _canvas_impl._MCP_PACKAGE


def test_build_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPRESS_SERVER_URL", raising=False)
    monkeypatch.delenv("ENABLE_CANVAS_SYNC", raising=False)
    env = _canvas_impl.build_env()
    assert env["EXPRESS_SERVER_URL"] == _canvas_impl.DEFAULT_CANVAS_URL
    assert env["ENABLE_CANVAS_SYNC"] == "true"
    # Must inherit the parent PATH so npx/node resolve in the child (Windows landmine).
    assert "PATH" in env or "Path" in env


def test_build_env_respects_user_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPRESS_SERVER_URL", "http://127.0.0.1:4567")
    monkeypatch.setenv("ENABLE_CANVAS_SYNC", "false")
    env = _canvas_impl.build_env()
    assert env["EXPRESS_SERVER_URL"] == "http://127.0.0.1:4567"
    assert env["ENABLE_CANVAS_SYNC"] == "false"


def test_build_env_export_dir_defaults_to_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXCALIDRAW_EXPORT_DIR", raising=False)
    monkeypatch.setenv("WORKSPACE_DIR", "/tmp/some-workspace")
    env = _canvas_impl.build_env()
    assert env["EXCALIDRAW_EXPORT_DIR"] == "/tmp/some-workspace"


def test_find_npx_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_canvas_impl.shutil, "which", lambda _name: None)
    with pytest.raises(_canvas_impl.CanvasServerError, match="npx"):
        _canvas_impl._find_npx()


def test_find_npx_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_canvas_impl.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    assert _canvas_impl._find_npx() == "/usr/bin/npx"
