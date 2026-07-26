from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ENV_NAMES = ("HAIBAO_MCP_URL", "HAIBAO_MCP_TOKEN", "HAIBAO_MCP_TIMEOUT")
TOOL_NAMES = frozenset({"haibao_list_datasets", "haibao_ask"})
MODES = frozenset({"low", "medium", "high"})
MAX_ROWS = 1000
logger = logging.getLogger(__name__)
_PRIVATE_LOGGERS = (
    "mcp.client.streamable_http",
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
)
_LOG_FILTER_MARKER = "_haibao_private_connector_filter"


class _DropPrivateConnectorLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


def _install_private_log_filters() -> None:
    with vars(logging)["_lock"]:
        for name in _PRIVATE_LOGGERS:
            private_logger = logging.getLogger(name)
            if not any(getattr(item, _LOG_FILTER_MARKER, False) for item in private_logger.filters):
                log_filter = _DropPrivateConnectorLogs()
                setattr(log_filter, _LOG_FILTER_MARKER, True)
                private_logger.addFilter(log_filter)


_install_private_log_filters()


class ConfigError(ValueError):
    pass


class ProtocolError(ValueError):
    pass


class RemoteHTTPStatusError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__("Haibao HTTP request failed")


class _StatusAwareSession:
    def __init__(
        self,
        session: Any,
        consume_status: Callable[[str], int | None],
    ):
        self._session = session
        self._consume_status = consume_status

    async def _run(self, operation_kind: str, operation: Callable[..., Any], *args: Any) -> Any:
        try:
            return await operation(*args)
        except Exception as exc:
            status = self._consume_status(operation_kind)
            if status is not None:
                raise RemoteHTTPStatusError(status) from exc
            raise

    async def initialize(self) -> Any:
        return await self._run("initialize", self._session.initialize)

    async def list_tools(self) -> Any:
        return await self._run("tools/list", self._session.list_tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._run("tools/call", self._session.call_tool, name, arguments)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


@dataclass(frozen=True)
class Config:
    url: str
    token: str = field(repr=False)
    timeout: float = 180.0


def build_config(env: Mapping[str, str]) -> Config:
    url = env.get("HAIBAO_MCP_URL", "")
    token = env.get("HAIBAO_MCP_TOKEN", "")
    timeout_text = env.get("HAIBAO_MCP_TIMEOUT", "180")
    _validate_url(url)
    if not 32 <= len(token) <= 512 or any(not 0x21 <= ord(character) <= 0x7E for character in token):
        raise ConfigError("HAIBAO_MCP_TOKEN is not configured or invalid")
    try:
        timeout = float(timeout_text)
    except (TypeError, ValueError) as exc:
        raise ConfigError("HAIBAO_MCP_TIMEOUT is invalid") from exc
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 180.0:
        raise ConfigError("HAIBAO_MCP_TIMEOUT is invalid")
    return Config(url=url, token=token, timeout=timeout)


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("HAIBAO_MCP_URL is invalid") from exc
    if (
        not url
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigError("HAIBAO_MCP_URL is not configured or invalid")
    if not parsed.hostname or (port is not None and not 1 <= port <= 65535):
        raise ConfigError("HAIBAO_MCP_URL is invalid")
    if parsed.scheme == "https":
        return
    if parsed.scheme != "http" or not _is_loopback(parsed.hostname):
        raise ConfigError("HAIBAO_MCP_URL must use HTTPS")


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@asynccontextmanager
async def _production_connector(config: Config) -> AsyncIterator[Any]:
    timeout = httpx.Timeout(config.timeout)
    statuses: dict[tuple[str, str | int], int] = {}

    def consume_status(operation_kind: str) -> int | None:
        for key in tuple(statuses):
            if key[0] == operation_kind:
                return statuses.pop(key)
        return None

    async def tag_operation(request: httpx.Request) -> None:
        if request.method != "POST":
            return
        try:
            payload = json.loads(request.content)
        except json.JSONDecodeError, TypeError, httpx.RequestNotRead:
            return
        if not isinstance(payload, dict) or "id" not in payload:
            return
        method = payload.get("method")
        request_id = payload["id"]
        if method not in {"initialize", "tools/list", "tools/call"} or not (
            isinstance(request_id, str) or type(request_id) is int
        ):
            return
        request.extensions["haibao_operation"] = (method, request_id)

    async def capture_error_status(response: httpx.Response) -> None:
        if response.request.method == "POST" and response.status_code in {401, 403, 429}:
            operation = response.request.extensions.get("haibao_operation")
            if (
                isinstance(operation, tuple)
                and len(operation) == 2
                and operation[0] in {"initialize", "tools/list", "tools/call"}
                and (isinstance(operation[1], str) or type(operation[1]) is int)
            ):
                statuses[operation] = response.status_code
                raise RemoteHTTPStatusError(response.status_code)

    stack = AsyncExitStack()
    cleanup_scope = anyio.CancelScope()
    pending_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    with cleanup_scope:
        try:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {config.token}"},
                    timeout=timeout,
                    event_hooks={"request": [tag_operation], "response": [capture_error_status]},
                )
            )
            read, write, *_ = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
            session = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=config.timeout))
            )
            try:
                yield _StatusAwareSession(session, consume_status)
            except BaseException as exc:
                pending_error = exc
        finally:
            cleanup_scope.shield = True
            cleanup_scope.deadline = anyio.current_time() + min(5.0, config.timeout)
            try:
                await stack.aclose()
            except BaseException as exc:
                if not cleanup_scope.cancel_called:
                    cleanup_error = exc
    if cleanup_scope.cancel_called:
        with suppress(Exception):
            logger.warning("Haibao MCP cleanup timed out; recycle the process")
    if isinstance(pending_error, anyio.get_cancelled_exc_class()):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise pending_error
        if cleanup_error is not None:
            for candidate in _walk_exceptions(cleanup_error):
                if isinstance(candidate, RemoteHTTPStatusError):
                    raise candidate from pending_error
        raise pending_error
    if pending_error is not None:
        raise pending_error
    if cleanup_error is not None:
        raise cleanup_error


async def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    connector: Callable[[Config], Any] | None = None,
) -> dict[str, Any]:
    if name not in TOOL_NAMES:
        return _error("invalid_argument", "Unknown Haibao tool", False)
    try:
        config = build_config(os.environ if env is None else env)
    except ConfigError:
        return _error("configuration_error", "Haibao MCP is not configured", False)

    call_attempted = False
    session: Any = None
    try:
        with anyio.fail_after(config.timeout):
            async with (connector or _production_connector)(config) as session:
                await session.initialize()
                listed = await session.list_tools()
                tools = getattr(listed, "tools", None)
                if not isinstance(tools, list):
                    raise ProtocolError("invalid tools response")
                _validate_tools(tools)
                call_attempted = True
                result = await session.call_tool(name, arguments)
                return _normalize_result(name, result)
    except Exception as exc:
        return _map_exception(exc, operation=name, call_attempted=call_attempted)


def _validate_tools(tools: list[Any]) -> None:
    entries: list[tuple[str, dict[str, Any], Any]] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        schema = getattr(tool, "inputSchema", None)
        if not isinstance(name, str) or not name or not isinstance(schema, dict):
            raise ProtocolError("invalid tool entry")
        entries.append((name, schema, tool))
    names = [name for name, _schema, _tool in entries]
    if len(names) != len(TOOL_NAMES) or set(names) != TOOL_NAMES:
        raise ProtocolError("unexpected tools")
    by_name = {name: (schema, tool) for name, schema, tool in entries}
    list_schema = by_name["haibao_list_datasets"][0]
    if (
        not isinstance(list_schema, dict)
        or not _only_schema_keys(list_schema, {"type", "properties", "required", "additionalProperties"})
        or list_schema.get("type") != "object"
        or list_schema.get("properties") != {}
        or list_schema.get("required", []) != []
        or list_schema.get("additionalProperties", False) is not False
    ):
        raise ProtocolError("invalid list schema")
    ask_schema = by_name["haibao_ask"][0]
    if (
        not isinstance(ask_schema, dict)
        or not _only_schema_keys(ask_schema, {"type", "properties", "required", "additionalProperties"})
        or ask_schema.get("type") != "object"
        or ask_schema.get("additionalProperties", False) is not False
    ):
        raise ProtocolError("invalid ask schema")
    properties = ask_schema.get("properties")
    required = ask_schema.get("required")
    if (
        not isinstance(properties, dict)
        or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in properties.items())
        or not isinstance(required, list)
        or any(not isinstance(name, str) for name in required)
    ):
        raise ProtocolError("invalid ask schema")
    if (
        set(properties) != {"text", "db_id", "mode"}
        or len(required) != len(set(required))
        or set(required) != {"text", "db_id"}
    ):
        raise ProtocolError("invalid ask schema")
    text = properties["text"]
    db_id = properties["db_id"]
    if not _string_schema(text, minimum=1, maximum=8000) or not _string_schema(db_id, minimum=1, maximum=128):
        raise ProtocolError("invalid ask schema")
    mode = properties["mode"]
    if (
        not isinstance(mode, dict)
        or not _only_schema_keys(mode, {"type", "enum", "default"})
        or mode.get("type") != "string"
        or mode.get("enum") != ["low", "medium", "high"]
        or mode.get("default") != "medium"
    ):
        raise ProtocolError("invalid ask schema")


def _string_schema(schema: Any, *, minimum: int, maximum: int) -> bool:
    if (
        not isinstance(schema, dict)
        or not _only_schema_keys(schema, {"type", "minLength", "maxLength"})
        or schema.get("type") != "string"
    ):
        return False
    return schema.get("minLength") == minimum and schema.get("maxLength") == maximum


def _only_schema_keys(schema: dict[str, Any], expected: set[str]) -> bool:
    return set(schema) <= expected | {"title", "description"}


# isError 文本中的稳定类别(FastMCP 固定前缀 "Error executing tool <name>: <category>"),
# 只提取已知类别词,不回传任何远程正文。
_TOOL_ERROR_MAP: dict[str, tuple[str, str, bool | None]] = {
    "not_data_query": ("not_data_query", "Not a data question; answer directly", False),
    "invalid_request": ("invalid_argument", "Invalid Haibao arguments", False),
    "invalid_response": ("protocol_error", "Haibao returned an invalid response", False),
    "unauthorized": ("unauthorized", "Haibao authentication failed", False),
    "rate_limited": ("rate_limited", "Haibao rate limit exceeded", None),
    "result_unknown": ("result_unknown", "Haibao result unknown; do not retry automatically", False),
    "transport_error": ("transport_error", "Haibao transport failed", None),
    "upstream_error": ("transport_error", "Haibao transport failed", None),
}


def _tool_error_category(result: Any) -> str | None:
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        return None
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        return None
    for category in _TOOL_ERROR_MAP:
        if f": {category}" in text:
            return category
    return None


def _normalize_result(name: str, result: Any) -> dict[str, Any]:
    if bool(getattr(result, "isError", False)):
        category = _tool_error_category(result)
        if category is not None:
            code, message, retryable = _TOOL_ERROR_MAP[category]
            if retryable is None:
                retryable = name == "haibao_list_datasets"
            return _error(code, message, retryable)
        return _error("remote_error", "Haibao request failed", False)
    payload = getattr(result, "structuredContent", None)
    if not isinstance(payload, dict):
        content = getattr(result, "content", None)
        if not isinstance(content, list) or len(content) != 1 or getattr(content[0], "type", None) != "text":
            raise ProtocolError("invalid content")
        try:
            payload = json.loads(content[0].text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProtocolError("invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProtocolError("invalid payload")
    if name == "haibao_list_datasets":
        return _validate_datasets(payload)
    return _validate_ask(payload)


def _validate_datasets(payload: dict[str, Any]) -> dict[str, Any]:
    if not {"datasets"} <= payload.keys() <= {"datasets", "request_id"}:
        raise ProtocolError("invalid dataset keys")
    datasets = payload["datasets"]
    if not isinstance(datasets, list) or len(datasets) > MAX_ROWS:
        raise ProtocolError("invalid datasets")
    for dataset in datasets:
        if not isinstance(dataset, dict) or not {"db_id"} <= set(dataset) <= {"db_id", "dialect", "source"}:
            raise ProtocolError("invalid dataset")
        dialect = dataset.get("dialect")
        source = dataset.get("source")
        if (
            not _safe_id(dataset["db_id"])
            or (dialect is not None and not _bounded_string(dialect, 1, 128))
            or (source is not None and not _bounded_string(source, 1, 128))
        ):
            raise ProtocolError("invalid dataset")
    _validate_request_id(payload)
    return payload


def _validate_ask(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"status", "execution", "request_id"}
    allowed = {
        "status",
        "answer",
        "sql",
        "execution",
        "request_id",
        "confidence_level",
        "confidence_note",
        "confidence_breakdown",
    }
    if not required <= set(payload) <= allowed:
        raise ProtocolError("invalid ask keys")
    status = payload["status"]
    if status not in {"success", "empty", "sql_only", "execution_failed"}:
        raise ProtocolError("invalid status")
    answer = payload.get("answer")
    sql = payload.get("sql")
    if (answer is not None and not _bounded_string(answer, 0, 100_000)) or (
        sql is not None and not _bounded_string(sql, 1, 100_000)
    ):
        raise ProtocolError("invalid ask text")
    _validate_confidence(payload)
    _validate_request_id(payload)
    if status == "sql_only" and payload["execution"] is None:
        payload = dict(payload)
        payload["execution"] = {"executed": False, "ok": False, "columns": [], "rows": [], "row_count": 0}
    execution = payload["execution"]
    if not isinstance(execution, dict) or set(execution) != {"executed", "ok", "columns", "rows", "row_count"}:
        raise ProtocolError("invalid execution")
    executed, ok = execution["executed"], execution["ok"]
    if type(executed) is not bool or type(ok) is not bool:
        raise ProtocolError("invalid execution flags")
    expected_flags = {
        "success": (True, True),
        "empty": (True, True),
        "sql_only": (False, False),
        "execution_failed": (True, False),
    }
    if (executed, ok) != expected_flags[status]:
        raise ProtocolError("inconsistent execution")
    columns, rows, row_count = execution["columns"], execution["rows"], execution["row_count"]
    if (
        not isinstance(columns, list)
        or len(columns) > 256
        or any(not _bounded_string(column, 1, 512) for column in columns)
    ):
        raise ProtocolError("invalid columns")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or type(row_count) is not int or row_count != len(rows):
        raise ProtocolError("invalid rows")
    if (status == "success" and not rows) or (status in {"empty", "sql_only", "execution_failed"} and rows):
        raise ProtocolError("inconsistent rows")
    if any(
        not isinstance(row, list) or len(row) != len(columns) or not all(_safe_json_value(value) for value in row)
        for row in rows
    ):
        raise ProtocolError("invalid row")
    return payload


def _validate_request_id(payload: dict[str, Any]) -> None:
    if "request_id" in payload and not _bounded_string(payload["request_id"], 1, 256):
        raise ProtocolError("invalid request id")


def _validate_confidence(payload: dict[str, Any]) -> None:
    level = payload.get("confidence_level")
    if level is not None and level not in {"high", "medium", "low"}:
        raise ProtocolError("invalid confidence level")
    note = payload.get("confidence_note")
    if note is not None and not _bounded_string(note, 1, 1000):
        raise ProtocolError("invalid confidence note")
    breakdown = payload.get("confidence_breakdown")
    if breakdown is None:
        return
    if not isinstance(breakdown, list) or len(breakdown) > 20:
        raise ProtocolError("invalid confidence breakdown")
    for item in breakdown:
        if (
            not isinstance(item, dict)
            or set(item) != {"signal", "status", "graded"}
            or not _bounded_string(item["signal"], 1, 64)
            or not _bounded_string(item["status"], 1, 64)
            or type(item["graded"]) is not bool
        ):
            raise ProtocolError("invalid confidence signal")


def _safe_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value not in {".", ".."}
        and all(character.isascii() and (character.isalnum() or character in "_-.") for character in value)
    )


def _bounded_string(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum


def _safe_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return not isinstance(value, str) or len(value) <= 100_000
    return isinstance(value, float) and math.isfinite(value)


def _map_exception(exc: Exception, *, operation: str, call_attempted: bool) -> dict[str, Any]:
    classifications = _exception_classifications(exc)
    transient_retryable = operation == "haibao_list_datasets" or not call_attempted
    if "protocol" in classifications:
        return _error("protocol_error", "Haibao returned an invalid response", False)
    if "unauthorized" in classifications:
        return _error("unauthorized", "Haibao authentication failed", False)
    if "rate_limited" in classifications:
        return _error("rate_limited", "Haibao rate limit exceeded", transient_retryable)
    if "timeout" in classifications:
        return _error("timeout", "Haibao request timed out", transient_retryable)
    return _error("transport_error", "Haibao transport failed", transient_retryable)


def _exception_classifications(exc: BaseException) -> set[str]:
    classifications: set[str] = set()
    for current in _walk_exceptions(exc):
        if isinstance(current, ProtocolError):
            classifications.add("protocol")
        elif isinstance(current, RemoteHTTPStatusError):
            if current.status_code in {401, 403}:
                classifications.add("unauthorized")
            elif current.status_code == 429:
                classifications.add("rate_limited")
        elif isinstance(current, httpx.HTTPStatusError):
            if current.response.status_code in {401, 403}:
                classifications.add("unauthorized")
            elif current.response.status_code == 429:
                classifications.add("rate_limited")
        elif isinstance(current, (TimeoutError, httpx.TimeoutException)):
            classifications.add("timeout")
    return classifications


def _walk_exceptions(exc: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return found


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}
