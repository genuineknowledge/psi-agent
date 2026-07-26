from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import math
import os
import re
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import httpx
import pytest
from aiohttp import web
from mcp.types import CallToolResult, TextContent, Tool

from psi_agent.session.tool_registry import ToolRegistry

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
SKILL_PATH = WORKSPACE_ROOT / "skills" / "haibao" / "SKILL.md"
GUIDE_PATH = WORKSPACE_ROOT / "docs" / "haibao-integration.md"
README_PATH = WORKSPACE_ROOT / "README.md"
AGENTS_PATH = WORKSPACE_ROOT / "AGENTS.md"
TOKEN = "t" * 32
ENV = {
    "HAIBAO_MCP_URL": "https://haibao.example.com/mcp",
    "HAIBAO_MCP_TOKEN": TOKEN,
    "HAIBAO_MCP_TIMEOUT": "12.5",
}


def _load(path: Path, prefix: str) -> Any:
    name = f"{prefix}_{hashlib.sha256(os.urandom(16)).hexdigest()}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


@pytest.fixture
def adapter(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("HAIBAO_"):
            monkeypatch.delenv(key, raising=False)
    return _load(TOOLS_DIR / "_haibao_mcp.py", "test_haibao_adapter")


def _schemas(*, bad_ask: bool = False, names: tuple[str, ...] | None = None) -> list[Tool]:
    ask_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 8000, "title": "Text"},
            "db_id": {"type": "string", "minLength": 1, "maxLength": 128, "title": "Db Id"},
            "mode": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
                "title": "Mode",
            },
        },
        "required": ["text", "db_id"],
        "title": "askArguments",
    }
    if bad_ask:
        ask_schema["properties"]["mode"].pop("enum")
    all_tools = {
        "haibao_list_datasets": Tool(
            name="haibao_list_datasets",
            inputSchema={"type": "object", "properties": {}, "title": "list_datasetsArguments"},
        ),
        "haibao_ask": Tool(name="haibao_ask", inputSchema=ask_schema),
    }
    return [all_tools[name] for name in (names or tuple(all_tools))]


class FakeSession:
    def __init__(self, result: Any, *, tools: list[Tool] | None = None, error: BaseException | None = None):
        self.result = result
        self.tools = tools or _schemas()
        self.error = error
        self.initialized = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        self.initialized += 1

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def _connector(session: FakeSession, state: dict[str, int] | None = None):
    state = state if state is not None else {"opened": 0, "closed": 0}

    @asynccontextmanager
    async def connect(config):
        assert config.url == ENV["HAIBAO_MCP_URL"]
        assert config.token == TOKEN
        state["opened"] += 1
        try:
            yield session
        finally:
            state["closed"] += 1

    return connect, state


def _result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[], structuredContent=payload, isError=is_error)


def _datasets() -> dict[str, Any]:
    return {
        "datasets": [
            {"db_id": "sales_2026", "dialect": "mysql", "source": "managed"},
            {"db_id": "inventory", "dialect": "postgresql", "source": "managed"},
        ],
        "request_id": "req-datasets",
    }


def _ask(status: str) -> dict[str, Any]:
    execution_by_status = {
        "success": {
            "executed": True,
            "ok": True,
            "columns": ["store", "amount"],
            "rows": [["Beijing", 125]],
            "row_count": 1,
        },
        "empty": {"executed": True, "ok": True, "columns": ["store"], "rows": [], "row_count": 0},
        "sql_only": None,
        "execution_failed": {"executed": True, "ok": False, "columns": [], "rows": [], "row_count": 0},
    }
    return {
        "status": status,
        "answer": "answer",
        "sql": "SELECT 1",
        "execution": execution_by_status[status],
        "request_id": f"req-{status}",
    }


@asynccontextmanager
async def _mcp_http_server(
    *,
    failure_method: str,
    status: int,
    session_id: str | None = None,
    get_status: int = 200,
    delete_status: int = 200,
):
    methods: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        if request.method == "GET":
            methods.append("GET")
            return web.Response(status=get_status)
        if request.method == "DELETE":
            methods.append("DELETE")
            return web.Response(status=delete_status)
        payload = await request.json()
        method = payload["method"]
        methods.append(method)
        if method == failure_method:
            return web.Response(status=status, text="remote secret must not escape; sentinel-traceback-private")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [tool.model_dump(by_alias=True, mode="json", exclude_none=True) for tool in _schemas()]}
        elif method == "tools/call":
            result = _result(_datasets()).model_dump(by_alias=True, mode="json", exclude_none=True)
        else:
            return web.Response(status=202)
        headers = {"Mcp-Session-Id": session_id} if session_id and method == "initialize" else None
        return web.json_response({"jsonrpc": "2.0", "id": payload["id"], "result": result}, headers=headers)

    app = web.Application()
    app.router.add_route("*", "/mcp", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", methods
    finally:
        with anyio.CancelScope(shield=True):
            await runner.cleanup()


@asynccontextmanager
async def _racing_notification_server():
    methods: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        payload = await request.json()
        method = payload["method"]
        methods.append(method)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
            return web.json_response({"jsonrpc": "2.0", "id": payload["id"], "result": result})
        if method == "notifications/initialized":
            await anyio.sleep(0.05)
            return web.Response(status=401)
        if method == "tools/list":
            return web.Response(status=500)
        return web.Response(status=202)

    app = web.Application()
    app.router.add_post("/mcp", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    site = web.SockSite(runner, sock)
    await site.start()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", methods
    finally:
        with anyio.CancelScope(shield=True):
            await runner.cleanup()


def test_build_config_accepts_only_secure_exact_mcp_urls(adapter):
    config = adapter.build_config(ENV)
    assert config.url == "https://haibao.example.com/mcp"
    assert config.timeout == 12.5
    assert TOKEN not in repr(config)

    for url in (
        "http://haibao.example.com/mcp",
        "https://user:pass@haibao.example.com/mcp",
        "https://@haibao.example.com/mcp",
        "https://:pass@haibao.example.com/mcp",
        "https://user:@haibao.example.com/mcp",
        "https://haibao.example.com/mcp?x=1",
        "https://haibao.example.com/mcp#fragment",
        "https://haibao.example.com/mcp/",
        "https://haibao.example.com/other",
    ):
        with pytest.raises(adapter.ConfigError):
            adapter.build_config({**ENV, "HAIBAO_MCP_URL": url})

    assert adapter.build_config({**ENV, "HAIBAO_MCP_URL": "http://localhost:9000/mcp"}).url.endswith("/mcp")
    assert adapter.build_config({**ENV, "HAIBAO_MCP_URL": "http://127.0.0.1/mcp"}).url.endswith("/mcp")
    assert adapter.build_config({**ENV, "HAIBAO_MCP_URL": "http://[::1]/mcp"}).url.endswith("/mcp")


@pytest.mark.parametrize(
    "overrides",
    [
        {"HAIBAO_MCP_URL": ""},
        {"HAIBAO_MCP_TOKEN": ""},
        {"HAIBAO_MCP_TOKEN": "short"},
        {"HAIBAO_MCP_TOKEN": "x" * 513},
        {"HAIBAO_MCP_TOKEN": "x" * 31 + " "},
        {"HAIBAO_MCP_TOKEN": "x" * 31 + "\n"},
        {"HAIBAO_MCP_TOKEN": "x" * 31 + "\x7f"},
        {"HAIBAO_MCP_TIMEOUT": "0.09"},
        {"HAIBAO_MCP_TIMEOUT": "180.1"},
        {"HAIBAO_MCP_TIMEOUT": "nan"},
        {"HAIBAO_MCP_TIMEOUT": "inf"},
    ],
)
def test_build_config_fails_closed_without_safe_credentials_and_timeout(adapter, overrides):
    with pytest.raises(adapter.ConfigError) as caught:
        adapter.build_config({**ENV, **overrides})
    assert TOKEN not in str(caught.value)


def test_build_config_uses_only_the_three_exact_environment_names(adapter):
    config = adapter.build_config(
        {
            **ENV,
            "HAIBAO_API_URL": "http://internal/api",
            "HAIBAO_API_KEY": "secret",
            "DATABASE_URL": "postgres://secret",
            "HAIBAO_MCP_TIMEOUT_SECONDS": "1",
        }
    )
    assert config.timeout == 12.5
    assert set(adapter.ENV_NAMES) == {"HAIBAO_MCP_URL", "HAIBAO_MCP_TOKEN", "HAIBAO_MCP_TIMEOUT"}


async def test_call_tool_fails_closed_at_call_time_without_import_side_effects(adapter):
    result = await adapter.call_tool("haibao_list_datasets", {}, env={})
    assert result == {
        "ok": False,
        "error": {"code": "configuration_error", "message": "Haibao MCP is not configured", "retryable": False},
    }


async def test_call_tool_rejects_unknown_tool_without_connecting(adapter):
    calls = 0

    @asynccontextmanager
    async def connector(_config):
        nonlocal calls
        calls += 1
        yield None

    result = await adapter.call_tool("other", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "invalid_argument"
    assert calls == 0


async def test_schema_discovery_requires_both_tools_and_critical_ask_schema(adapter):
    for tools in (_schemas(names=("haibao_ask",)), _schemas(bad_ask=True)):
        session = FakeSession(_result(_datasets()), tools=tools)
        connector, _ = _connector(session)
        result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
        assert result["error"]["code"] == "protocol_error"
        assert session.calls == []


@pytest.mark.parametrize(
    "tools",
    [
        [*_schemas(), _schemas()[0]],
        [
            Tool(
                name="haibao_list_datasets",
                inputSchema={"type": "object", "properties": {}, "required": ["token"]},
            ),
            _schemas()[1],
        ],
        [
            Tool(
                name="haibao_list_datasets",
                inputSchema={"type": "object", "properties": {"token": {"type": "string"}}},
            ),
            _schemas()[1],
        ],
        [
            Tool(
                name="haibao_list_datasets",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": True},
            ),
            _schemas()[1],
        ],
    ],
)
async def test_schema_discovery_rejects_duplicate_or_nonzero_list_schema(adapter, tools):
    session = FakeSession(_result(_datasets()), tools=tools)
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"
    assert session.calls == []


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("extra_property", {"type": "string"}),
        ("extra_required", "secret"),
        ("additional_properties", True),
        ("text_bounds", (0, 8000)),
        ("db_bounds", (1, 129)),
        ("missing_bound", ("text", "minLength")),
        ("missing_bound", ("text", "maxLength")),
        ("missing_bound", ("db_id", "minLength")),
        ("missing_bound", ("db_id", "maxLength")),
        ("missing_mode_default", None),
        ("wrong_mode_default", "high"),
    ],
)
async def test_schema_discovery_rejects_ask_schema_expansion_or_wrong_advertised_bounds(adapter, change, value):
    tools = _schemas()
    schema = tools[1].inputSchema
    if change == "extra_property":
        schema["properties"]["secret"] = value
    elif change == "extra_required":
        schema["required"].append(value)
    elif change == "additional_properties":
        schema["additionalProperties"] = value
    elif change == "text_bounds":
        schema["properties"]["text"].update(minLength=value[0], maxLength=value[1])
    elif change == "db_bounds":
        schema["properties"]["db_id"].update(minLength=value[0], maxLength=value[1])
    elif change == "missing_mode_default":
        schema["properties"]["mode"].pop("default")
    elif change == "wrong_mode_default":
        schema["properties"]["mode"]["default"] = value
    else:
        schema["properties"][value[0]].pop(value[1])
    session = FakeSession(_result(_datasets()), tools=tools)
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"
    assert session.calls == []


@pytest.mark.parametrize(
    "required",
    [
        [["text"], "db_id"],
        [1, "db_id"],
        ["text", "text"],
    ],
)
async def test_schema_discovery_rejects_malformed_required_entries(adapter, required):
    tools = _schemas()
    tools[1].inputSchema["required"] = required
    session = FakeSession(_result(_datasets()), tools=tools)
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"] == {
        "code": "protocol_error",
        "message": "Haibao returned an invalid response",
        "retryable": False,
    }
    assert session.calls == []


@pytest.mark.parametrize(
    "properties",
    [
        {1: {"type": "string"}},
        {"text": [], "db_id": {}, "mode": {}},
        ["text", "db_id", "mode"],
    ],
)
async def test_schema_discovery_rejects_malformed_properties(adapter, properties):
    tools = _schemas()
    tools[1].inputSchema["properties"] = properties
    session = FakeSession(_result(_datasets()), tools=tools)
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"] == {
        "code": "protocol_error",
        "message": "Haibao returned an invalid response",
        "retryable": False,
    }
    assert session.calls == []


async def test_schema_discovery_accepts_absent_or_false_additional_properties_and_metadata(adapter):
    for additional in (None, False):
        tools = _schemas()
        for tool in tools:
            tool.inputSchema["description"] = "metadata"
            if additional is not None:
                tool.inputSchema["additionalProperties"] = additional
        session = FakeSession(_result(_datasets()), tools=tools)
        connector, _ = _connector(session)
        result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
        assert result == _datasets()


@pytest.mark.parametrize(
    ("target", "keyword", "value"),
    [
        ("root", "pattern", "secret"),
        ("root", "minProperties", 2),
        ("text", "pattern", "secret"),
        ("text", "default", "secret"),
        ("text", "enum", ["secret"]),
        ("db_id", "format", "hostname"),
        ("db_id", "minimum", 1),
        ("mode", "minLength", 1),
        ("mode", "maximum", 3),
        ("mode", "enum", ["low", "medium", "high", "secret"]),
    ],
)
async def test_schema_discovery_rejects_every_non_annotation_semantic_keyword(adapter, target, keyword, value):
    tools = _schemas()
    schema = tools[1].inputSchema
    destination = schema if target == "root" else schema["properties"][target]
    destination[keyword] = value
    session = FakeSession(_result(_datasets()), tools=tools)
    connector, _ = _connector(session)

    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)

    assert result["error"]["code"] == "protocol_error"
    assert session.calls == []


@pytest.mark.parametrize(
    ("target", "annotation"),
    [("root", "title"), ("root", "description"), ("text", "title"), ("mode", "description")],
)
async def test_schema_discovery_allows_only_title_and_description_annotations(adapter, target, annotation):
    tools = _schemas()
    schema = tools[1].inputSchema
    destination = schema if target == "root" else schema["properties"][target]
    destination[annotation] = "annotation"
    session = FakeSession(_result(_datasets()), tools=tools)
    connector, _ = _connector(session)

    assert await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector) == _datasets()


async def test_malformed_sdk_response_is_a_protocol_error(adapter):
    class MalformedSession(FakeSession):
        async def list_tools(self):
            return object()

    session = MalformedSession(_result(_datasets()))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"


@pytest.mark.parametrize(
    "tool",
    [
        SimpleNamespace(inputSchema={}),
        SimpleNamespace(name=7, inputSchema={}),
        SimpleNamespace(name="haibao_list_datasets"),
        SimpleNamespace(name="haibao_list_datasets", inputSchema=[]),
        None,
    ],
)
async def test_malformed_tool_entries_map_to_protocol_error(adapter, tool):
    session = FakeSession(_result(_datasets()))
    session.tools = [tool, _schemas()[1]]
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"
    assert session.calls == []


async def test_task_group_wrapped_protocol_error_maps_to_protocol_error(adapter):
    session = FakeSession(_result(_datasets()), tools=_schemas(names=("haibao_ask",)))

    @asynccontextmanager
    async def task_group_connector(_config):
        async with anyio.create_task_group():
            yield session

    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=task_group_connector)
    assert result == {
        "ok": False,
        "error": {
            "code": "protocol_error",
            "message": "Haibao returned an invalid response",
            "retryable": False,
        },
    }


async def test_structured_dataset_success_is_validated(adapter):
    session = FakeSession(_result(_datasets()))
    connector, state = _connector(session)
    assert await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector) == _datasets()
    assert session.initialized == 1
    assert session.calls == [("haibao_list_datasets", {})]
    assert state == {"opened": 1, "closed": 1}


@pytest.mark.parametrize("status", ["success", "empty", "sql_only", "execution_failed"])
async def test_all_ask_statuses_are_validated_and_sql_only_is_normalized(adapter, status):
    session = FakeSession(_result(_ask(status)))
    connector, _ = _connector(session)
    result = await adapter.call_tool(
        "haibao_ask", {"text": "question", "db_id": "sales", "mode": "medium"}, env=ENV, connector=connector
    )
    expected = _ask(status)
    if status == "sql_only":
        expected["execution"] = {
            "executed": False,
            "ok": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
    assert result == expected


async def test_datasets_accept_optional_fields_omitted_by_exclude_none_server(adapter):
    """The production server dumps with exclude_none: dialect/source keys may be absent."""
    payload = {
        "datasets": [
            {"db_id": "wine", "dialect": "mysql", "source": "warehouse"},
            {"db_id": "_org_ontology", "source": "org-aggregate"},
            {"db_id": "minimal"},
        ]
    }
    session = FakeSession(_result(payload))
    connector, _ = _connector(session)
    assert await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector) == payload


async def test_ask_accepts_sql_only_without_sql_key(adapter):
    """The production server omits the sql key entirely for sql_only without SQL."""
    payload = {
        "status": "sql_only",
        "answer": "No SQL generated",
        "execution": {"executed": False, "ok": False, "columns": [], "rows": [], "row_count": 0},
        "request_id": "req-1",
    }
    session = FakeSession(_result(payload))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env=ENV, connector=connector)
    assert result == payload


async def test_ask_accepts_confidence_fields(adapter):
    """The production server forwards chatbi confidence fields when present."""
    payload = {
        "status": "success",
        "answer": "6",
        "sql": "SELECT 1",
        "execution": {"executed": True, "ok": True, "columns": ["n"], "rows": [[6]], "row_count": 1},
        "request_id": "req-1",
        "confidence_level": "high",
        "confidence_note": "结果可信。",
        "confidence_breakdown": [
            {"signal": "symbolic", "status": "ke2sql", "graded": False},
            {"signal": "semantic", "status": "pass", "graded": True},
        ],
    }
    session = FakeSession(_result(payload))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env=ENV, connector=connector)
    assert result == payload


@pytest.mark.parametrize(
    "patch",
    [
        {"confidence_level": "extreme"},
        {"confidence_note": "x" * 1001},
        {"confidence_breakdown": [{"signal": "s", "status": "pass"}]},
        {"confidence_breakdown": [{"signal": "s", "status": "pass", "graded": "yes"}]},
        {"confidence_breakdown": [{"signal": "s", "status": "pass", "graded": True, "extra": 1}]},
        {"confidence_breakdown": "high"},
    ],
)
async def test_ask_rejects_malformed_confidence(adapter, patch):
    payload = {
        "status": "success",
        "answer": "6",
        "sql": "SELECT 1",
        "execution": {"executed": True, "ok": True, "columns": ["n"], "rows": [[6]], "row_count": 1},
        "request_id": "req-1",
        **patch,
    }
    session = FakeSession(_result(payload))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"


async def test_is_error_never_exposes_remote_text(adapter):
    remote_secret = "remote stack and secret"
    session = FakeSession(
        CallToolResult(
            content=[TextContent(type="text", text=remote_secret)],
            structuredContent={"error": remote_secret},
            isError=True,
        )
    )
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result == {
        "ok": False,
        "error": {"code": "remote_error", "message": "Haibao request failed", "retryable": False},
    }
    assert remote_secret not in json.dumps(result)


async def test_single_text_json_object_is_the_only_fallback(adapter):
    session = FakeSession(CallToolResult(content=[TextContent(type="text", text=json.dumps(_datasets()))]))
    connector, _ = _connector(session)
    assert await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector) == _datasets()

    for content in (
        [TextContent(type="text", text="not json")],
        [TextContent(type="text", text="[]")],
        [TextContent(type="text", text="{}"), TextContent(type="text", text="{}")],
        [],
    ):
        bad_session = FakeSession(CallToolResult(content=content))
        bad_connector, _ = _connector(bad_session)
        result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=bad_connector)
        assert result["error"]["code"] == "protocol_error"


@pytest.mark.parametrize(
    "payload",
    [
        {"datasets": [{"db_id": "sales", "dialect": "mysql", "source": "managed", "secret": "no"}]},
        {"datasets": [{"db_id": "../sales", "dialect": "mysql", "source": "managed"}]},
        {"datasets": "sales"},
        {"datasets": [{"db_id": "sales", "dialect": 1, "source": "managed"}]},
        {"datasets": [], "unknown": True},
    ],
)
async def test_dataset_result_rejects_unknown_keys_and_types(adapter, payload):
    session = FakeSession(_result(payload))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.update(status="unknown"),
        lambda p: p.update(extra=True),
        lambda p: p.update(execution=None),
        lambda p: p["execution"].update(executed=False),
        lambda p: p["execution"].update(row_count=2),
        lambda p: p["execution"].update(rows=[["x"]] * 1001, row_count=1001),
        lambda p: p["execution"].update(rows=[[object()]]),
    ],
)
async def test_ask_result_rejects_inconsistent_or_oversized_results(adapter, mutator):
    payload = _ask("success")
    mutator(payload)
    session = FakeSession(_result(payload))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env=ENV, connector=connector)
    assert result["error"]["code"] == "protocol_error"


def _http_error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", ENV["HAIBAO_MCP_URL"])
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(body, request=request, response=response)


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (_http_error(401, "token leaked"), "unauthorized", False),
        (_http_error(403, "forbidden body"), "unauthorized", False),
        (_http_error(429, "rate body"), "rate_limited", False),
        (httpx.ConnectError("host and token leaked"), "transport_error", False),
        (TimeoutError("token leaked"), "timeout", False),
    ],
)
async def test_safe_transport_error_mapping_and_no_retry(adapter, error, code, retryable):
    session = FakeSession(None, error=error)
    connector, state = _connector(session)
    result = await adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env=ENV, connector=connector)
    assert result["error"]["code"] == code
    assert result["error"]["retryable"] is retryable
    assert "leaked" not in json.dumps(result)
    assert len(session.calls) == 1
    assert state == {"opened": 1, "closed": 1}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (_http_error(429, "rate body"), "rate_limited"),
        (httpx.ConnectError("host hidden"), "transport_error"),
        (TimeoutError("timeout hidden"), "timeout"),
    ],
)
async def test_list_datasets_transient_call_failures_remain_retryable(adapter, error, code):
    session = FakeSession(None, error=error)
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == code
    assert result["error"]["retryable"] is True
    assert len(session.calls) == 1


@pytest.mark.parametrize(("status", "code"), [(401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited")])
@pytest.mark.parametrize("operation", ["haibao_list_datasets", "haibao_ask"])
async def test_real_streamable_http_initialize_status_is_classified(adapter, status, code, operation):
    async with _mcp_http_server(failure_method="initialize", status=status) as (url, methods):
        result = await adapter.call_tool(
            operation,
            {} if operation == "haibao_list_datasets" else {"text": "q", "db_id": "sales"},
            env={**ENV, "HAIBAO_MCP_URL": url},
        )

    assert result["error"] == {
        "code": code,
        "message": "Haibao authentication failed" if code == "unauthorized" else "Haibao rate limit exceeded",
        "retryable": status == 429,
    }
    assert methods == ["initialize"]
    assert "remote secret" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "operation", "code", "retryable"),
    [
        (401, "haibao_ask", "unauthorized", False),
        (403, "haibao_ask", "unauthorized", False),
        (429, "haibao_ask", "rate_limited", False),
        (401, "haibao_list_datasets", "unauthorized", False),
        (403, "haibao_list_datasets", "unauthorized", False),
        (429, "haibao_list_datasets", "rate_limited", True),
    ],
)
async def test_real_streamable_http_call_status_is_classified(adapter, status, operation, code, retryable):
    async with _mcp_http_server(failure_method="tools/call", status=status) as (url, methods):
        result = await adapter.call_tool(
            operation,
            {} if operation == "haibao_list_datasets" else {"text": "q", "db_id": "sales"},
            env={**ENV, "HAIBAO_MCP_URL": url},
        )

    assert result["error"] == {
        "code": code,
        "message": "Haibao authentication failed" if code == "unauthorized" else "Haibao rate limit exceeded",
        "retryable": retryable,
    }
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert "remote secret" not in json.dumps(result)


@pytest.mark.parametrize(("status", "code"), [(401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited")])
async def test_real_streamable_http_list_status_is_classified(adapter, status, code):
    async with _mcp_http_server(failure_method="tools/list", status=status) as (url, methods):
        result = await adapter.call_tool(
            "haibao_list_datasets",
            {},
            env={**ENV, "HAIBAO_MCP_URL": url},
        )

    assert result["error"] == {
        "code": code,
        "message": "Haibao authentication failed" if code == "unauthorized" else "Haibao rate limit exceeded",
        "retryable": status == 429,
    }
    assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]


async def test_real_streamable_http_delayed_notification_status_is_not_mapped_to_list(adapter):
    async with _racing_notification_server() as (url, methods):
        result = await adapter.call_tool(
            "haibao_list_datasets",
            {},
            env={**ENV, "HAIBAO_MCP_URL": url},
        )

    assert result["error"]["code"] == "transport_error"
    assert methods == ["initialize", "notifications/initialized"]


async def test_real_sdk_logs_never_expose_connector_secrets(adapter, caplog):
    question = "sentinel-question-private"
    db_id = "sentinel-db-private"
    session_id = "sentinel-session-private"
    async with _mcp_http_server(
        failure_method="tools/call",
        status=429,
        session_id=session_id,
    ) as (url, _methods):
        private_env = {
            **ENV,
            "HAIBAO_MCP_URL": url,
            "HAIBAO_MCP_TOKEN": "sentinel-token-private-1234567890",
        }
        with caplog.at_level(logging.DEBUG):
            result = await adapter.call_tool(
                "haibao_ask",
                {"text": question, "db_id": db_id, "mode": "medium"},
                env=private_env,
            )

    assert result["error"]["code"] == "rate_limited"
    captured = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        question,
        db_id,
        session_id,
        url,
        private_env["HAIBAO_MCP_TOKEN"],
        "remote secret must not escape",
        "sentinel-traceback-private",
    ):
        assert secret not in captured
    assert adapter._PRIVATE_LOGGERS == (
        "mcp.client.streamable_http",
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
    )


@pytest.mark.parametrize("lifecycle_status", [401, 429])
async def test_real_streamable_http_lifecycle_status_never_overwrites_failed_call(adapter, lifecycle_status):
    async with _mcp_http_server(
        failure_method="never",
        status=500,
        session_id="test-session",
        get_status=lifecycle_status,
        delete_status=lifecycle_status,
    ) as (url, methods):
        result = await adapter.call_tool(
            "haibao_ask",
            {"text": "q", "db_id": "sales"},
            env={**ENV, "HAIBAO_MCP_URL": url},
        )

    assert result["error"]["code"] == "protocol_error"
    assert "GET" in methods
    assert methods[-1] == "DELETE"


async def test_real_streamable_http_connector_exposes_structured_status(adapter, monkeypatch):
    captured: list[BaseException] = []
    original = adapter._map_exception

    def capture(exc, **kwargs):
        captured.append(exc)
        return original(exc, **kwargs)

    monkeypatch.setattr(adapter, "_map_exception", capture)
    async with _mcp_http_server(failure_method="initialize", status=401) as (url, _methods):
        await adapter.call_tool(
            "haibao_list_datasets",
            {},
            env={**ENV, "HAIBAO_MCP_URL": url},
        )

    assert len(captured) == 1
    assert isinstance(captured[0], adapter.RemoteHTTPStatusError)
    assert captured[0].status_code == 401
    assert captured[0].__cause__ is not None
    assert "remote secret" not in str(captured[0])


@pytest.mark.parametrize(
    ("errors", "code", "retryable"),
    [
        ([httpx.ConnectError("hidden"), "protocol"], "protocol_error", False),
        ([httpx.ConnectError("hidden"), _http_error(403, "hidden")], "unauthorized", False),
        ([TimeoutError("hidden"), _http_error(429, "hidden")], "rate_limited", False),
        ([httpx.ConnectError("hidden"), TimeoutError("hidden")], "timeout", False),
    ],
)
async def test_nested_exception_classification_has_deterministic_priority(adapter, errors, code, retryable):
    nested = []
    for error in errors:
        nested.append(adapter.ProtocolError("hidden") if error == "protocol" else error)
    session = FakeSession(None, error=ExceptionGroup("hidden", [ExceptionGroup("hidden", nested)]))
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env=ENV, connector=connector)
    assert result["error"]["code"] == code
    assert result["error"]["retryable"] is retryable
    assert "hidden" not in json.dumps(result)


async def test_hard_timeout_closes_connector(adapter):
    class SlowSession(FakeSession):
        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            await anyio.sleep_forever()

    session = SlowSession(None)
    connector, state = _connector(session)
    result = await adapter.call_tool(
        "haibao_list_datasets", {}, env={**ENV, "HAIBAO_MCP_TIMEOUT": "0.1"}, connector=connector
    )
    assert result["error"]["code"] == "timeout"
    assert state == {"opened": 1, "closed": 1}


async def test_cancellation_closes_connector(adapter):
    started = anyio.Event()

    class SlowSession(FakeSession):
        async def call_tool(self, name, arguments):
            started.set()
            await anyio.sleep_forever()

    session = SlowSession(None)
    connector, state = _connector(session)
    task = asyncio.create_task(adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state == {"opened": 1, "closed": 1}


async def test_production_connector_wires_httpx_stream_and_client_session(adapter, monkeypatch):
    events: list[Any] = []

    class HttpClient:
        def __init__(self, **kwargs):
            events.append(("http_init", kwargs))

        async def __aenter__(self):
            events.append("http_enter")
            return self

        async def __aexit__(self, *_args):
            events.append("http_exit")

    @asynccontextmanager
    async def transport(url, *, http_client):
        events.append(("transport", url, http_client))
        yield "read", "write", lambda: "session-id"

    class Session:
        def __init__(self, read, write, read_timeout_seconds):
            events.append(("session_init", read, write, read_timeout_seconds.total_seconds()))

        async def __aenter__(self):
            events.append("session_enter")
            return self

        async def __aexit__(self, *_args):
            events.append("session_exit")

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)
    config = adapter.build_config(ENV)
    async with adapter._production_connector(config) as session:
        assert isinstance(session._session, Session)
    kwargs = events[0][1]
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert len(kwargs["event_hooks"]["response"]) == 1
    assert math.isfinite(kwargs["timeout"].read)
    assert events[-3:] == ["session_enter", "session_exit", "http_exit"]


async def test_production_connector_tags_post_status_with_request_operation(adapter, monkeypatch):
    hooks: dict[str, list[Any]] = {}
    stale_request = httpx.Request("POST", ENV["HAIBAO_MCP_URL"])

    class HttpClient:
        def __init__(self, **kwargs):
            hooks.update(kwargs["event_hooks"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: None

    class Session(FakeSession):
        def __init__(self, *_args, **_kwargs):
            super().__init__(_result(_datasets()))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            await hooks["request"][0](stale_request)

        async def list_tools(self):
            await hooks["response"][0](httpx.Response(401, request=stale_request))
            raise httpx.ConnectError("list failed")

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)

    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV)

    assert set(hooks) == {"request", "response"}
    assert result["error"]["code"] == "transport_error"


async def test_production_connector_classifies_request_content_without_consuming_or_retaining_arguments(
    adapter, monkeypatch
):
    hooks: dict[str, list[Any]] = {}

    class HttpClient:
        def __init__(self, **kwargs):
            hooks.update(kwargs["event_hooks"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: None

    class Session:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)

    async with adapter._production_connector(adapter.build_config(ENV)):
        request = httpx.Request(
            "POST",
            ENV["HAIBAO_MCP_URL"],
            json={"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"arguments": {"text": "secret"}}},
        )
        original_content = request.content
        await hooks["request"][0](request)
        assert request.content == original_content
        assert request.extensions["haibao_operation"] == ("tools/call", 17)
        assert "secret" not in repr(request.extensions)

        for method, content in (
            ("POST", b"not-json"),
            ("POST", b"[]"),
            ("POST", b'{"jsonrpc":"2.0","method":"tools/list"}'),
            ("POST", b'{"jsonrpc":"2.0","id":true,"method":"tools/list"}'),
            ("GET", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'),
            ("DELETE", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'),
        ):
            malformed = httpx.Request(method, ENV["HAIBAO_MCP_URL"], content=content)
            await hooks["request"][0](malformed)
            assert "haibao_operation" not in malformed.extensions


@pytest.mark.parametrize("status", [401, 429])
@pytest.mark.parametrize("cleanup", ["fails", "hangs"])
@pytest.mark.parametrize("cancellation_source", ["task", "operation"])
async def test_production_connector_cancellation_wins_over_captured_status_and_cleanup(
    adapter, monkeypatch, status, cleanup, cancellation_source
):
    operation_started = anyio.Event()
    cleanup_started = anyio.Event()
    hooks: dict[str, list[Any]] = {}

    class HttpClient:
        def __init__(self, **kwargs):
            hooks.update(kwargs["event_hooks"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: None

    class Session(FakeSession):
        def __init__(self, *_args, **_kwargs):
            super().__init__(_result(_datasets()))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            cleanup_started.set()
            if cleanup == "hangs":
                await anyio.sleep_forever()
            raise RuntimeError("private cleanup failure")

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=_schemas())

        async def call_tool(self, name, arguments):
            request = httpx.Request(
                "POST",
                ENV["HAIBAO_MCP_URL"],
                json={"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {"arguments": arguments}},
            )
            await hooks["request"][0](request)
            with pytest.raises(adapter.RemoteHTTPStatusError):
                await hooks["response"][0](httpx.Response(status, request=request))
            operation_started.set()
            if cancellation_source == "operation":
                raise asyncio.CancelledError
            await anyio.sleep_forever()

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)
    task = asyncio.create_task(
        adapter.call_tool("haibao_ask", {"text": "q", "db_id": "sales"}, env={**ENV, "HAIBAO_MCP_TIMEOUT": "0.1"})
    )
    await operation_started.wait()
    if cancellation_source == "task":
        task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup_started.is_set()


async def test_production_connector_hanging_cleanup_is_cancelled_with_finite_grace(adapter, monkeypatch):
    cleanup_started = anyio.Event()
    cleanup_cancelled = anyio.Event()

    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: "session-id"

    class Session(FakeSession):
        def __init__(self, *_args, **_kwargs):
            super().__init__(_result(_datasets()))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            cleanup_started.set()
            try:
                await anyio.sleep_forever()
            finally:
                cleanup_cancelled.set()

        async def call_tool(self, name, arguments):
            await anyio.sleep_forever()

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)
    started = time.monotonic()
    result = await adapter.call_tool("haibao_list_datasets", {}, env={**ENV, "HAIBAO_MCP_TIMEOUT": "0.1"})
    elapsed = time.monotonic() - started
    assert result["error"]["code"] == "timeout"
    assert cleanup_started.is_set()
    assert cleanup_cancelled.is_set()
    assert elapsed < 0.5


async def test_production_connector_cleanup_timeout_logs_one_sanitized_warning(adapter, monkeypatch, caplog):
    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: "secret-session-id"

    class Session(FakeSession):
        def __init__(self, *_args, **_kwargs):
            super().__init__(_result(_datasets()))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            await anyio.sleep_forever()

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)

    with caplog.at_level(logging.WARNING, logger=adapter.__name__):
        async with adapter._production_connector(adapter.build_config({**ENV, "HAIBAO_MCP_TIMEOUT": "0.1"})):
            pass

    records = [record for record in caplog.records if record.name == adapter.__name__]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message == "Haibao MCP cleanup timed out; recycle the process"
    for secret in (ENV["HAIBAO_MCP_URL"], TOKEN, "secret-session-id", "exception"):
        assert secret not in message


async def test_production_connector_normal_cleanup_does_not_warn(adapter, monkeypatch, caplog):
    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: None

    class Session:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)

    with caplog.at_level(logging.WARNING, logger=adapter.__name__):
        async with adapter._production_connector(adapter.build_config(ENV)):
            pass

    assert not [record for record in caplog.records if record.name == adapter.__name__]


async def test_production_connector_cleanup_logging_failure_does_not_escape(adapter, monkeypatch):
    class HttpClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    @asynccontextmanager
    async def transport(_url, *, http_client):
        yield "read", "write", lambda: None

    class Session:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            await anyio.sleep_forever()

    def fail_logging(_message):
        raise RuntimeError("logging failed")

    monkeypatch.setattr(adapter.httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr(adapter, "streamable_http_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)
    monkeypatch.setattr(adapter.logger, "warning", fail_logging)

    async with adapter._production_connector(adapter.build_config({**ENV, "HAIBAO_MCP_TIMEOUT": "0.1"})):
        pass


@pytest.mark.parametrize(
    "filename, function_name", [("haibao_list_datasets.py", "haibao_list_datasets"), ("haibao_ask.py", "haibao_ask")]
)
def test_public_modules_import_without_config_and_expose_one_async_function(adapter, filename, function_name):
    module = _load(TOOLS_DIR / filename, f"test_{function_name}")
    public_async = [
        name for name, value in vars(module).items() if not name.startswith("_") and inspect.iscoroutinefunction(value)
    ]
    assert public_async == [function_name]


async def test_real_tool_registry_registers_both_haibao_tools_with_expected_ask_args(tmp_path):
    tools_dir = Path(tmp_path) / "tools"
    tools_dir.mkdir()
    for filename in ("_haibao_mcp.py", "haibao_list_datasets.py", "haibao_ask.py"):
        (tools_dir / filename).write_bytes((TOOLS_DIR / filename).read_bytes())

    registry = await ToolRegistry.load(tools_dir)
    assert {"haibao_list_datasets", "haibao_ask"} <= registry.tools.keys()
    ask = registry.tools["haibao_ask"].parameters
    assert ask == {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 8000, "description": ""},
            "db_id": {"type": "string", "minLength": 1, "maxLength": 128, "description": ""},
            "mode": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
                "description": "",
            },
        },
        "required": ["text", "db_id"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize("filename", ["haibao_list_datasets.py", "haibao_ask.py"])
def test_public_wrapper_removes_failed_sibling_module_before_second_load(monkeypatch, filename):
    helper_path = TOOLS_DIR / "_haibao_mcp.py"
    helper_prefix = f"haibao_tool__haibao_mcp_{hashlib.sha256(str(helper_path).encode()).hexdigest()[:12]}_"
    for name in tuple(sys.modules):
        if name.startswith(helper_prefix):
            sys.modules.pop(name)
    original_read_bytes = Path.read_bytes
    failed = False

    def fail_once(path):
        nonlocal failed
        if path == helper_path and not failed:
            failed = True
            return b"invalid python !"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_once)
    with pytest.raises(SyntaxError):
        _load(TOOLS_DIR / filename, "test_failed_wrapper")
    assert not any(name.startswith(helper_prefix) for name in sys.modules)

    module = _load(TOOLS_DIR / filename, "test_reloaded_wrapper")
    current = [name for name in sys.modules if name.startswith(helper_prefix)]
    assert len(current) == 1
    assert module._mcp_module is sys.modules[current[0]]
    sys.modules.pop(current[0], None)


def test_public_wrappers_refresh_shared_helper_when_content_changes(tmp_path):
    tools_dir = Path(tmp_path)
    helper_path = tools_dir / "_haibao_mcp.py"
    for filename in ("haibao_list_datasets.py", "haibao_ask.py"):
        (tools_dir / filename).write_bytes((TOOLS_DIR / filename).read_bytes())
    prefix = f"haibao_tool__haibao_mcp_{hashlib.sha256(str(helper_path).encode()).hexdigest()[:12]}_"

    helper_path.write_text("MARKER = 1\nasync def call_tool(*args): return {}\n", encoding="utf-8")
    list_module = _load(tools_dir / "haibao_list_datasets.py", "test_list_v1")
    first_name = list_module._mcp_module.__name__
    assert list_module._mcp_module.MARKER == 1

    helper_path.write_text("MARKER = 2\nasync def call_tool(*args): return {}\n", encoding="utf-8")
    ask_module = _load(tools_dir / "haibao_ask.py", "test_ask_v2")
    second_name = ask_module._mcp_module.__name__
    refreshed_list = _load(tools_dir / "haibao_list_datasets.py", "test_list_v2")
    assert second_name != first_name
    assert ask_module._mcp_module.MARKER == 2
    assert refreshed_list._mcp_module is ask_module._mcp_module
    assert [name for name in sys.modules if name.startswith(prefix)] == [second_name]
    sys.modules.pop(second_name, None)


async def test_public_tools_validate_inputs_and_return_unicode_json(monkeypatch, adapter):
    ask_module = _load(TOOLS_DIR / "haibao_ask.py", "test_public_ask")
    list_module = _load(TOOLS_DIR / "haibao_list_datasets.py", "test_public_list")
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"answer": "北京"}

    monkeypatch.setattr(ask_module._mcp_module, "call_tool", fake_call)
    monkeypatch.setattr(list_module._mcp_module, "call_tool", fake_call)
    assert json.loads(await list_module.haibao_list_datasets()) == {"answer": "北京"}
    assert json.loads(await ask_module.haibao_ask("  question  ", " sales_2026 ", "high")) == {"answer": "北京"}
    assert calls == [
        ("haibao_list_datasets", {}),
        ("haibao_ask", {"text": "question", "db_id": "sales_2026", "mode": "high"}),
    ]

    for text, db_id, mode in (
        ("", "sales", "low"),
        ("x" * 8001, "sales", "low"),
        ("q", "../sales", "low"),
        ("q", "..", "low"),
        ("q", ".", "low"),
        ("q", "sales", "invalid"),
        ([], "sales", "low"),
        ("q", [], "low"),
        ("q", "sales", []),
    ):
        result = json.loads(await ask_module.haibao_ask(text, db_id, mode))
        assert result["error"]["code"] == "invalid_argument"
    assert len(calls) == 2


def test_env_example_contains_only_placeholders_for_exact_adapter_variables():
    lines = [line for line in (WORKSPACE_ROOT / ".env.haibao.example").read_text(encoding="utf-8").splitlines() if line]
    assert [line.split("=", 1)[0] for line in lines] == [
        "HAIBAO_MCP_URL",
        "HAIBAO_MCP_TOKEN",
        "HAIBAO_MCP_TIMEOUT",
    ]
    assert "example.com/mcp" in lines[0]
    assert "replace" in lines[1].lower()
    assert "HAIBAO_API" not in "\n".join(lines)
    assert lines[2] == "HAIBAO_MCP_TIMEOUT=180"


def test_haibao_skill_frontmatter_targets_business_data_not_sql_concepts():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1].lower()
    assert "name: haibao" in frontmatter
    assert "description:" in frontmatter
    assert any(term in frontmatter for term in ("business data", "metrics", "reporting"))
    assert "sql" in frontmatter
    assert any(term in frontmatter for term in ("not for", "do not use", "pure sql"))


def test_haibao_skill_has_dataset_selection_and_credential_boundaries():
    text = SKILL_PATH.read_text(encoding="utf-8").lower()
    tool_names = set(re.findall(r"\bhaibao_[a-z_]+\b", text)) - {"haibao_mcp_token"}
    assert tool_names == {"haibao_list_datasets", "haibao_ask"}
    assert "pure sql" in text and "do not call" in text
    assert "zero" in text and "unavailable" in text
    assert "one relevant" in text and "select" in text
    assert "multiple" in text and "ask the user" in text
    assert "never guess" in text and re.search(r"\bnames?\b", text)
    for secret in ("token", "api key", "password", "connection string"):
        assert secret in text
    assert "operator-approved" in text and "onboarding" in text and "not supported" in text


def test_haibao_skill_uses_configured_principal_not_user_identity():
    text = SKILL_PATH.read_text(encoding="utf-8").lower()
    assert "configured haibao principal" in text
    assert "process-global" in text and "haibao_mcp_token" in text
    assert "per-session identity" in text and "does not" in text
    assert "user's available business data" not in text


def test_haibao_skill_has_modes_status_interpretation_and_retry_rules():
    text = SKILL_PATH.read_text(encoding="utf-8").lower()
    for mode in ("low", "medium", "high"):
        assert mode in text
    assert "default" in text and "medium" in text and "conservative" in text
    for status in ("success", "empty", "sql_only", "execution_failed"):
        assert status in text
    assert "executed=true" in text and "ok=true" in text
    assert "does not prove" in text and "business fact" in text
    assert "not executed" in text
    assert "invent" in text
    assert "service error" in text and "blind" in text and "retry" in text
    assert "unknown" in text and "post" in text and "retry" in text


def test_haibao_skill_treats_results_as_untrusted_and_minimizes_disclosure():
    text = SKILL_PATH.read_text(encoding="utf-8").lower()
    assert "untrusted" in text and "prompt injection" in text
    assert "never follow" in text and "instructions" in text and "links" in text
    for detail in ("sql", "db_id", "caveats"):
        assert detail in text
    assert "sensitive rows" in text and "minimize" in text


def test_haibao_guide_documents_implemented_bundle_and_external_server_contract():
    text = GUIDE_PATH.read_text(encoding="utf-8").lower()
    assert "bundled" in text and "adapter" in text and "skill" in text
    assert "operator-provisioned" in text
    assert "not bundled" in text or "不 bundled" in text
    assert "production" in text and "deployed" in text
    assert "not" in text or "不表示" in text or "不得宣称" in text
    assert "oauth issuer" in text
    assert "streamable http" in text and "https" in text
    assert "loopback" in text and "development" in text
    assert "ddl" in text and "onboarding" in text
    assert "not" in text or "不提供" in text or "不支持" in text
    for gate in ("dedicated", "rate limiting", "quota", "monitoring"):
        assert gate in text


def test_haibao_guide_records_exact_schema_configuration_and_lifecycle():
    text = GUIDE_PATH.read_text(encoding="utf-8")
    compact = "".join(text.split())
    assert '"text":{"type":"string","minLength":1,"maxLength":8000}' in compact
    assert '"db_id":{"type":"string","minLength":1,"maxLength":128}' in compact
    assert '"enum":["low","medium","high"]' in compact
    assert '"additionalProperties":false' in compact
    env_names = set(re.findall(r"\bHAIBAO_[A-Z0-9_]+\b", text))
    assert env_names == {"HAIBAO_MCP_URL", "HAIBAO_MCP_TOKEN", "HAIBAO_MCP_TIMEOUT"}
    lower = text.lower()
    assert "tools/list" in lower and "schema" in lower
    assert "per-call" in lower and "lifecycle" in lower
    assert "omitted" in lower and "compatibility" in lower
    assert "reject" in lower and "true" in lower
    assert "only sends" in lower and "known" in lower and "arguments" in lower
    assert "ruff format --check ." in text
    assert "HAIBAO_MCP_TIMEOUT=180" in text


def test_haibao_docs_define_process_global_principal_boundary():
    for path in (README_PATH, AGENTS_PATH, GUIDE_PATH, SKILL_PATH):
        text = " ".join(path.read_text(encoding="utf-8").lower().split())
        assert "haibao_mcp_token" in text, path
        assert "process-global" in text, path
        assert "security boundary" in text, path
        assert re.search(r"separate (?:haitun )?(?:process|container|workspace)", text), path
        assert "distinct token" in text, path
        assert "distinct authorization" in text, path
        assert "per-session identity" in text, path


def test_haibao_guide_documents_restart_only_upgrade_behavior():
    text = " ".join(GUIDE_PATH.read_text(encoding="utf-8").lower().split())
    assert "toolregistry" in text
    assert "underscore" in text and "_haibao_mcp.py" in text and "skip" in text
    assert "public wrapper" in text and "hash" in text
    assert "public wrapper" in text and "hot refresh" in text
    assert "helper-only" in text and "session/process restart" in text


def test_haibao_guide_documents_bounded_cleanup_tradeoff():
    text = " ".join(GUIDE_PATH.read_text(encoding="utf-8").lower().split())
    assert "shielded" in text and "max 5 seconds" in text
    assert "cleanup hangs" in text and "cancelled/abandoned" in text
    assert "recycle" in text and "process" in text
    assert "monitor" in text and "repeated cleanup timeout" in text
    assert "haibao mcp cleanup timed out; recycle the process" in text
    assert "complete cleanup" in text and "not guaranteed" in text


def test_haibao_guide_requires_pre_parse_response_body_limits():
    text = " ".join(GUIDE_PATH.read_text(encoding="utf-8").lower().split())
    assert "after sdk parsing" in text
    assert "reverse proxy" in text and "private mcp" in text
    assert "body limit" in text and "pre-validation memory exhaustion" in text
    production_gates = text.split("## 5. 生产门禁", 1)[1]
    assert "body limit" in production_gates


def test_haibao_guide_documents_real_status_capture_and_retryability():
    text = " ".join(GUIDE_PATH.read_text(encoding="utf-8").lower().split())
    assert "structured response hook" in text
    assert "status only" in text and "no remote body" in text
    assert "initialize 429" in text and "retryable=true" in text
    assert "list call 429/transient" in text and "retryable=true" in text
    assert "ask post-attempt" in text and "retryable=false" in text
    assert "auth" in text and "retryable=false" in text


def test_haibao_public_docs_include_current_files_commands_and_no_private_paths():
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    agents = AGENTS_PATH.read_text(encoding="utf-8")
    combined = "\n".join((guide, readme, agents))
    for path in (
        "tools/_haibao_mcp.py",
        "tools/haibao_list_datasets.py",
        "tools/haibao_ask.py",
        "skills/haibao/SKILL.md",
        "tests/test_haibao_tools.py",
    ):
        assert path in guide
    for command in ("psi-agent session", "pytest", "ruff check", "ty check", "diff --check"):
        assert command in guide
    assert "superseded" in guide.lower()
    assert "adapter" in readme.lower() and "skill" in readme.lower() and "bundled" in readme.lower()
    assert "adapter" in agents.lower() and "skill" in agents.lower() and "bundled" in agents.lower()
    assert "private" in combined.lower() and "server" in combined.lower() and "required" in combined.lower()
    assert not re.search(r"https?://(?:\d{1,3}\.){3}\d{1,3}", combined)
    for forbidden in (
        "HAIBAO_API_KEY",
        "HAIBAO_API_BASE",
        "X-User-Id",
        "chatbi.py",
        "/v1/conversations",
    ):
        assert forbidden not in combined


@pytest.mark.parametrize(
    ("category", "code", "retryable"),
    [
        ("not_data_query", "not_data_query", False),
        ("invalid_request", "invalid_argument", False),
        ("invalid_response", "protocol_error", False),
        ("unauthorized", "unauthorized", False),
        ("rate_limited", "rate_limited", True),
        ("result_unknown", "result_unknown", False),
        ("transport_error", "transport_error", True),
        ("upstream_error", "transport_error", True),
    ],
)
async def test_is_error_text_categories_are_mapped_distinctly(adapter, category, code, retryable):
    """isError 文本中的稳定类别必须映射为对应错误码,而不是统一的 remote_error。"""
    session = FakeSession(
        CallToolResult(
            content=[TextContent(type="text", text=f"Error executing tool haibao_list_datasets: {category}")],
            isError=True,
        )
    )
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["ok"] is False
    assert result["error"]["code"] == code
    assert result["error"]["retryable"] is retryable


async def test_is_error_with_unknown_text_remains_remote_error(adapter):
    session = FakeSession(
        CallToolResult(content=[TextContent(type="text", text="some unstructured failure")], isError=True)
    )
    connector, _ = _connector(session)
    result = await adapter.call_tool("haibao_list_datasets", {}, env=ENV, connector=connector)
    assert result["error"]["code"] == "remote_error"
