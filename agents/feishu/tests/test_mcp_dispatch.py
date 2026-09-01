"""Tests for ``@mcp(dispatch=True)`` — one generic tool instead of N wrappers.

An MCP server's schemas are authored upstream, so the only lever on their
context cost is how many we expose. Dispatch mode exposes one
``<group>_call(tool, args_json)`` and moves the argument documentation into a
generated skill. These tests pin the behaviour that makes that safe:

- the dispatcher reaches every tool, under both the prefixed and bare name;
- bad input is refused **locally**, without a request reaching the server;
- a transport failure comes back as a tool error rather than propagating (the
  containment that keeps a dead MCP server from taking the gateway down);
- ``keep=`` still generates real wrappers, and names a rename broke are logged
  instead of failing tool loading.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import TextContent

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_mcp: Any = importlib.import_module("_mcp")

DECL = (
    "from _mcp import mcp\n"
    "@mcp({args})\n"
    "def grp():\n"
    "    return {{'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'x']}}\n"
)


@pytest.fixture
def _fake_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two canned schemas, and never read or write the committed cache."""
    monkeypatch.setattr(_mcp, "_load_cached_schemas", lambda _n: (None, None))
    monkeypatch.setattr(_mcp, "_save_cached_schemas", lambda _n, _p, _s: None)
    monkeypatch.setattr(
        _mcp,
        "_discover",
        lambda _c: {
            "do_thing": {
                "name": "do_thing",
                "description": "Do a thing.",
                "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            },
            "read_thing": {
                "name": "read_thing",
                "description": "Read a thing.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        },
    )


def _load(args: str, _fake: None) -> dict[str, Any]:
    ns: dict[str, Any] = {}
    exec(DECL.format(args=args), ns)
    return ns


def test_dispatch_replaces_per_tool_wrappers(_fake_discover: None) -> None:
    ns = _load("dispatch=True", _fake_discover)
    assert "grp_call" in ns
    # The whole point: the per-tool schemas are gone from the exposed surface.
    assert "grp_do_thing" not in ns
    assert "grp_read_thing" not in ns
    assert inspect.iscoroutinefunction(ns["grp_call"])


def test_dispatch_signature_is_two_strings(_fake_discover: None) -> None:
    """The dispatcher's cost must not scale with the number of server tools."""
    params = inspect.signature(_load("dispatch=True", _fake_discover)["grp_call"]).parameters
    assert list(params) == ["tool", "args_json"]
    assert params["args_json"].default == ""


def test_docstring_lists_every_tool(_fake_discover: None) -> None:
    """The model needs to know what it can dispatch to without reading the skill."""
    doc = _load("dispatch=True", _fake_discover)["grp_call"].__doc__
    assert "grp_do_thing" in doc
    assert "grp_read_thing" in doc


def test_keep_still_generates_real_wrappers(_fake_discover: None) -> None:
    ns = _load("dispatch=True, keep=('grp_do_thing',)", _fake_discover)
    assert "grp_call" in ns
    assert "grp_do_thing" in ns  # kept as its own tool
    assert "x" in inspect.signature(ns["grp_do_thing"]).parameters
    assert "grp_read_thing" not in ns  # not kept -> dispatch only


def test_keep_accepts_bare_name(_fake_discover: None) -> None:
    ns = _load("dispatch=True, keep=('do_thing',)", _fake_discover)
    assert "grp_do_thing" in ns


def test_unknown_keep_name_is_logged_not_fatal(_fake_discover: None) -> None:
    """An upstream rename must not break tool loading."""
    ns = _load("dispatch=True, keep=('grp_gone_upstream',)", _fake_discover)
    assert "grp_call" in ns  # still usable
    assert "grp_gone_upstream" not in ns


def test_bare_decorator_still_generates_per_tool(_fake_discover: None) -> None:
    """Default behaviour is unchanged — dispatch is opt-in."""
    ns = _load("", _fake_discover)
    assert "grp_do_thing" in ns
    assert "grp_read_thing" in ns
    assert "grp_call" not in ns


# ── runtime: what actually reaches the server ────────────────────────────────


class _Session:
    """Minimal MCP session double recording the (name, args) it was called with."""

    def __init__(self, sink: list[tuple[str, dict[str, Any]]]) -> None:
        self.sink = sink

    async def initialize(self) -> None:
        return None

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.sink.append((name, args))
        return SimpleResult(f"called {name} with {args}")


class SimpleResult:
    def __init__(self, text: str) -> None:
        self.content = [TextContent(type="text", text=text)]
        self.isError = False


@pytest.fixture
def _capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Replace the transport so no subprocess runs; record dispatched calls."""
    sink: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def _connect(_cfg: dict[str, Any]) -> Any:
        yield _Session(sink)

    monkeypatch.setattr(_mcp, "_connect", _connect)
    return sink


def test_dispatch_sends_bare_name_to_server(_fake_discover: None, _capture: list) -> None:
    """The skill documents ``grp_do_thing``; the server only knows ``do_thing``."""
    fn = _load("dispatch=True", _fake_discover)["grp_call"]
    out = anyio.run(lambda: fn("grp_do_thing", '{"x": 7}'))
    assert _capture == [("do_thing", {"x": 7})]
    assert "called do_thing" in out


def test_dispatch_accepts_bare_name_too(_fake_discover: None, _capture: list) -> None:
    fn = _load("dispatch=True", _fake_discover)["grp_call"]
    anyio.run(lambda: fn("do_thing", '{"x": 1}'))
    assert _capture == [("do_thing", {"x": 1})]


def test_empty_args_json_means_no_arguments(_fake_discover: None, _capture: list) -> None:
    fn = _load("dispatch=True", _fake_discover)["grp_call"]
    anyio.run(lambda: fn("grp_read_thing"))
    assert _capture == [("read_thing", {})]


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("grp_nope", "{}", "unknown grp tool"),
        ("grp_do_thing", "{oops", "not valid JSON"),
        ("grp_do_thing", "[1, 2]", "must be a JSON *object*"),
    ],
)
def test_bad_input_is_refused_locally(
    _fake_discover: None, _capture: list, tool: str, args: str, expected: str
) -> None:
    """A typo or malformed payload must never reach the server as a real call."""
    fn = _load("dispatch=True", _fake_discover)["grp_call"]
    out = anyio.run(lambda: fn(tool, args))
    assert expected in out
    assert _capture == []  # nothing was sent


def test_transport_failure_becomes_a_tool_error(_fake_discover: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Containment: a dead MCP server must not propagate out of the tool.

    Without this the exception escapes into the session and can take the
    gateway down — the generated wrappers have the same guard, and the two
    paths only agreed once the dispatcher grew it.
    """

    @asynccontextmanager
    async def _boom(_cfg: dict[str, Any]) -> Any:
        raise RuntimeError("server exited during startup")
        yield  # pragma: no cover

    monkeypatch.setattr(_mcp, "_connect", _boom)
    fn = _load("dispatch=True", _fake_discover)["grp_call"]
    out = anyio.run(lambda: fn("grp_do_thing", '{"x": 1}'))
    assert out.startswith("Error: MCP tool 'do_thing' failed")
    assert "server exited during startup" in out


def test_keyboard_interrupt_still_propagates(_fake_discover: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Containment must not swallow genuine interpreter-control signals."""

    @asynccontextmanager
    async def _stop(_cfg: dict[str, Any]) -> Any:
        raise KeyboardInterrupt
        yield  # pragma: no cover

    monkeypatch.setattr(_mcp, "_connect", _stop)
    fn = _load("dispatch=True", _fake_discover)["grp_call"]
    with pytest.raises(KeyboardInterrupt):
        anyio.run(lambda: fn("grp_do_thing", '{"x": 1}'))
