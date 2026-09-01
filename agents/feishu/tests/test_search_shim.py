"""Tests for the ``search`` tool's ``mcp`` 2.0 compatibility shim.

``serper-mcp-server`` 0.0.10 applies the ``Server.list_tools()`` /
``Server.call_tool()`` decorators at import time, and ``mcp`` 2.0 removed both in
favour of ``Server.add_request_handler``. Without the shim, importing serper raises
``AttributeError`` and every search fails before a request is sent.

The ``mcp>=1.28.1,<2.0.0`` pin is the primary fix; the shim covers an environment
that resolved to 2.x anyway, which is how the incident happened (``pip install -e
.`` ignores ``uv.lock`` and re-resolved).

These tests never call the Serper API. They pin the shim's translation against a
stub ``Server`` that mimics the 2.0 surface, so the behaviour is asserted under the
pinned 1.x too — where the real class still has the decorators and the shim is a
no-op.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

search: Any = importlib.import_module("search")


class _StubServer:
    """Mimics the ``mcp`` 2.0 lowlevel ``Server``: registrar present, decorators gone.

    ``list_tools`` / ``call_tool`` are *bare annotations*: they declare what the shim
    installs so a type checker can see the attributes, while creating nothing at
    runtime — so ``hasattr`` still reports them absent and the shim still installs.
    """

    list_tools: Any
    call_tool: Any

    def __init__(self) -> None:
        self.handlers: dict[str, tuple[type, Any]] = {}

    def add_request_handler(self, method: str, params_type: type, handler: Any) -> None:
        self.handlers[method] = (params_type, handler)


@pytest.fixture
def shimmed(monkeypatch: pytest.MonkeyPatch) -> type[_StubServer]:
    """Install the shim onto a throwaway stub instead of the real ``Server`` class.

    Patching the class the shim resolves keeps the real ``mcp`` untouched, so these
    tests cannot leak decorators into other tests in the same process. The stub is
    subclassed per test because the shim assigns onto the class it is given, which
    would otherwise persist and make the ``hasattr`` guard skip later installs.
    """
    stub = type("_StubServerPerTest", (_StubServer,), {})
    module = importlib.import_module("mcp.server.lowlevel.server")
    monkeypatch.setattr(module, "Server", stub, raising=True)
    assert search._install_lowlevel_decorator_shim() is True
    return stub


def test_shim_is_noop_when_decorators_already_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """On ``mcp`` 1.x — or a fixed upstream — the shim must not overwrite anything."""

    class _Server:
        def list_tools(self) -> None: ...
        def call_tool(self) -> None: ...

    original_list, original_call = _Server.list_tools, _Server.call_tool
    module = importlib.import_module("mcp.server.lowlevel.server")
    monkeypatch.setattr(module, "Server", _Server, raising=True)

    assert search._install_lowlevel_decorator_shim() is False
    assert _Server.list_tools is original_list
    assert _Server.call_tool is original_call


def test_shim_is_idempotent(shimmed: type[_StubServer]) -> None:
    """Re-running it is a no-op: both call sites invoke it on every connection."""
    installed = shimmed.list_tools
    assert search._install_lowlevel_decorator_shim() is False
    assert shimmed.list_tools is installed


def test_list_tools_decorator_registers_tools_list(shimmed: type[_StubServer]) -> None:
    """``@server.list_tools()`` must register ``tools/list`` and wrap the list in a result."""
    server = shimmed()

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [types.Tool(name="google_search", description="d", inputSchema={"type": "object"})]

    params_type, handler = server.handlers["tools/list"]
    # All-optional, so a request with no ``params`` member still reaches the handler.
    assert params_type is types.PaginatedRequestParams

    result = anyio.run(handler, None, params_type())
    assert isinstance(result, types.ListToolsResult)
    assert [tool.name for tool in result.tools] == ["google_search"]


def test_list_tools_decorator_returns_the_original_function(shimmed: type[_StubServer]) -> None:
    """Decorators must return the function so the module's own name stays bound."""
    server = shimmed()

    async def _list() -> list[Any]:
        return []

    assert server.list_tools()(_list) is _list

    async def _call(name: str, arguments: dict[str, Any]) -> list[Any]:
        return []

    assert server.call_tool()(_call) is _call


def test_call_tool_decorator_passes_name_and_arguments(shimmed: type[_StubServer]) -> None:
    """``@server.call_tool()`` must unpack params into serper's ``(name, arguments)``."""
    server = shimmed()
    seen: dict[str, Any] = {}

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        seen["name"] = name
        seen["arguments"] = arguments
        return [types.TextContent(type="text", text="{}")]

    params_type, handler = server.handlers["tools/call"]
    assert params_type is types.CallToolRequestParams

    result = anyio.run(handler, None, params_type(name="google_search", arguments={"q": "x", "num": "2"}))
    assert isinstance(result, types.CallToolResult)
    assert seen == {"name": "google_search", "arguments": {"q": "x", "num": "2"}}
    assert result.content[0].text == "{}"


def test_call_tool_handler_defaults_null_arguments_to_empty_dict(shimmed: type[_StubServer]) -> None:
    """serper indexes ``arguments`` directly, so ``None`` must not reach it."""
    server = shimmed()
    seen: dict[str, Any] = {}

    @server.call_tool()
    async def _call(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        seen["arguments"] = arguments
        return []

    params_type, handler = server.handlers["tools/call"]
    anyio.run(handler, None, params_type(name="google_search"))
    assert seen["arguments"] == {}


def test_call_tool_decorator_tolerates_extra_arguments(shimmed: type[_StubServer]) -> None:
    """The 1.x decorator grew keyword options; passing one must not break the shim."""
    server = shimmed()

    async def _call(name: str, arguments: dict[str, Any]) -> list[Any]:
        return []

    server.call_tool(validate_input=True)(_call)
    assert "tools/call" in server.handlers


def test_serper_import_sites_install_the_shim_first() -> None:
    """Both serper import paths must shim first, or the import raises ``AttributeError``.

    Asserted on source because the failure is an import-time crash inside serper:
    the ordering is the fix, and a reordering would only surface in production.
    """
    for func in (search._sync_api_key, search._transport):
        source = inspect.getsource(func)
        shim = source.index("_install_lowlevel_decorator_shim()")
        assert shim < source.index("import_module"), f"{func.__name__} imports serper before shimming"
