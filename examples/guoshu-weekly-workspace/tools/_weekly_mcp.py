"""MCP client for the guoshu-weekly取数 service.

Follows the fusion-memory pattern: a backend-neutral anyio supervisor thread
owns the MCP session, so the client survives the Session's own event loop being
cancelled between turns.  Connection is lazy -- the first tool call builds it,
which keeps import time off the first-token path (the ≤10s / first-token-now
acceptance targets in the plan).

All eleven tools are reads, so every request is replayable on transport loss.
"""

from __future__ import annotations

import hashlib
import json
import queue
import sys
import threading
import types
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
from anyio.from_thread import run_sync as run_sync_from_thread
from anyio.lowlevel import EventLoopToken, current_token
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOOLS_DIR = Path(__file__).resolve().parent


def _load_sibling_module(name: str) -> dict[str, Any]:
    path = TOOLS_DIR / f"{name}.py"
    module_name = f"guoshu_weekly_tool_{name}_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing.__dict__
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module.__dict__


_config = _load_sibling_module("_weekly_config")
CONFIG = _config["CONFIG"]
WeeklyConfigError = _config["WeeklyConfigError"]


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


@dataclass(eq=False)
class _Request:
    name: str
    arguments: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    completed: bool = False


@asynccontextmanager
async def _production_connector(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> AsyncIterator[Any]:
    timeout = httpx.Timeout(timeout_seconds)
    async with (
        httpx.AsyncClient(headers=headers, timeout=timeout) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, *_),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout_seconds)) as session,
    ):
        yield session


def _unwrap_json_text(payload: Any) -> Any:
    """Parse a JSON document that arrived as a string.

    The remote tools return their envelope as JSON *text*, and MCP wraps a
    string return value in ``structuredContent = {"result": "<text>"}``.  Left
    alone that reaches the model as an escaped string inside a field, so the
    ``ok`` / ``caliber`` / ``rows`` keys the prompt tells it to read are not
    addressable.  Unwrap one level of that encoding.
    """
    if not isinstance(payload, str):
        return payload
    with suppress(json.JSONDecodeError):
        return json.loads(payload)
    return payload


def _normalize(result: Any) -> dict[str, Any]:
    """Unwrap an MCP CallToolResult into this workspace's envelope."""
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps a plain string return in {"result": "<json text>"}.
        if set(structured) == {"result"}:
            inner = _unwrap_json_text(structured["result"])
            if isinstance(inner, dict):
                return inner
            return {"ok": not bool(getattr(result, "isError", False)), "result": inner}
        return structured
    blocks = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            blocks.append(text)
    if len(blocks) == 1:
        inner = _unwrap_json_text(blocks[0])
        if isinstance(inner, dict):
            return inner
        return {"ok": not bool(getattr(result, "isError", False)), "result": inner}
    joined = "\n".join(blocks)
    return {"ok": not bool(getattr(result, "isError", False)), "result": joined}


class WeeklyMcpClient:
    """Lazy MCP client with an anyio supervisor thread."""

    def __init__(
        self,
        config: Any,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._connector = connector or _production_connector
        self._incoming: queue.Queue[_Request | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.RLock()
        self._started = threading.Event()
        self._closed_event = threading.Event()
        self._terminal_error: dict[str, Any] | None = None
        self._pending: set[_Request] = set()
        self._supervisor_cancel_scope: anyio.CancelScope | None = None
        self._supervisor_token: EventLoopToken | None = None
        self._closed = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._config.url or not self._config.token:
            missing = "URL" if not self._config.url else "TOKEN"
            return _error(
                "configuration_error",
                f"GUOSHU_WEEKLY_MCP_{missing} is not configured; the process starter owns it",
                False,
            )
        with self._thread_lock:
            if self._closed:
                return _error("client_closed", "weekly MCP client is closed", True)
        await self._ensure_started()
        request = _Request(name, dict(arguments))
        with self._thread_lock:
            if self._closed:
                return _error("client_closed", "weekly MCP client is closed", True)
            if self._terminal_error is not None:
                return dict(self._terminal_error)
            self._pending.add(request)
            self._incoming.put(request)
        while not request.done.is_set():  # noqa: ASYNC110 - set from the supervisor thread
            await anyio.sleep(0.01)
        if request.result is not None:
            return request.result
        return _error("request_failed", "weekly MCP request failed", True)

    def request_close(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
            self._closed_event.set()
            if self._terminal_error is None:
                self._terminal_error = _error("client_closed", "weekly MCP client is closed", True)
            cancel_scope = self._supervisor_cancel_scope
            token = self._supervisor_token
            thread = self._thread
        if cancel_scope is not None and token is not None:
            try:
                run_sync_from_thread(cancel_scope.cancel, token=token)
            except RuntimeError:
                self._incoming.put(None)
        elif thread is not None:
            self._incoming.put(None)

    async def _ensure_started(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            if self._thread is None:
                self._started.clear()
                self._terminal_error = None
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="guoshu-weekly-mcp",
                    daemon=True,
                )
                self._thread.start()
        while not self._started.is_set():  # noqa: ASYNC110 - set from the supervisor thread
            await anyio.sleep(0.01)

    def _thread_main(self) -> None:
        try:
            anyio.run(self._supervisor_main)
        finally:
            self._mark_terminal(
                _error("client_terminated", "weekly MCP client terminated", True)
                if self._started.is_set()
                else _error("client_start_failed", "weekly MCP client failed to start", True)
            )
            self._started.set()
            with self._thread_lock:
                self._thread = None

    async def _supervisor_main(self) -> None:
        send, receive = anyio.create_memory_object_stream[_Request | None](0)
        try:
            async with send, receive, anyio.create_task_group() as task_group:
                with self._thread_lock:
                    self._supervisor_cancel_scope = task_group.cancel_scope
                    self._supervisor_token = current_token()
                self._started.set()
                finished = anyio.Event()
                task_group.start_soon(self._bridge_requests, send)
                task_group.start_soon(self._supervisor_loop, receive, finished)
                await finished.wait()
                task_group.cancel_scope.cancel()
        finally:
            with self._thread_lock:
                self._supervisor_cancel_scope = None
                self._supervisor_token = None

    async def _bridge_requests(self, send: MemoryObjectSendStream[_Request | None]) -> None:
        while True:
            try:
                request = self._incoming.get_nowait()
            except queue.Empty:
                await anyio.sleep(0.01)
                continue
            if request is not None and self._closed_event.is_set():
                continue
            await send.send(request)
            if request is None:
                return

    async def _supervisor_loop(
        self,
        receive: MemoryObjectReceiveStream[_Request | None],
        finished: anyio.Event,
    ) -> None:
        stack: AsyncExitStack | None = None
        session: Any = None

        async def disconnect() -> None:
            nonlocal stack, session
            if stack is not None:
                with suppress(Exception, BaseExceptionGroup):
                    await stack.aclose()
            stack = None
            session = None

        async def connect() -> Any:
            nonlocal stack, session
            if session is not None:
                return session
            new_stack = AsyncExitStack()
            try:
                opener = self._connector(
                    self._config.url,
                    self._headers(),
                    self._config.timeout_seconds,
                )
                session = await new_stack.enter_async_context(opener)
                await session.initialize()
            except BaseException:
                with anyio.CancelScope(shield=True), suppress(Exception, BaseExceptionGroup):
                    await new_stack.aclose()
                raise
            stack = new_stack
            return session

        try:
            while True:
                request = await receive.receive()
                if request is None:
                    await disconnect()
                    return
                try:
                    result = await self._execute(request, connect, disconnect)
                except Exception:
                    result = _error("request_failed", "weekly MCP request failed", True)
                self._complete_request(request, result)
        finally:
            with anyio.CancelScope(shield=True):
                await disconnect()
            finished.set()

    async def _execute(
        self,
        request: _Request,
        connect: Callable[[], Any],
        disconnect: Callable[[], Any],
    ) -> dict[str, Any]:
        attempts = 0
        while True:
            if self._closed_event.is_set():
                return _error("client_closed", "weekly MCP client is closed", True)
            try:
                session = await connect()
                return _normalize(await session.call_tool(request.name, request.arguments))
            except Exception as exc:
                await disconnect()
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status in {401, 403}:
                        return _error("unauthorized", "weekly MCP authentication failed", False)
                    if status in {400, 405, 406, 415, 422}:
                        return _error("remote_request_error", "weekly MCP rejected the request", False)
                # Every weekly tool is a read, so replay is always safe.
                if attempts >= self._config.max_retries:
                    return _error("transport_error", "weekly MCP transport failed", True)
                await anyio.sleep(min(0.25 * (2**attempts), 2.0))
                attempts += 1

    def _mark_terminal(self, result: dict[str, Any]) -> None:
        with self._thread_lock:
            if self._terminal_error is None:
                self._terminal_error = result
            terminal = self._terminal_error
            for request in tuple(self._pending):
                self._complete_request_locked(request, terminal)

    def _complete_request(self, request: _Request, result: dict[str, Any]) -> None:
        with self._thread_lock:
            self._complete_request_locked(request, result)

    def _complete_request_locked(self, request: _Request, result: dict[str, Any]) -> None:
        if request.completed:
            return
        request.result = result
        request.completed = True
        self._pending.discard(request)
        request.done.set()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.token}",
            "Accept": "application/json, text/event-stream",
        }


CLIENT = WeeklyMcpClient(CONFIG)


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


async def call(name: str, arguments: dict[str, Any]) -> str:
    """Call one remote weekly tool and return its JSON envelope as text."""
    return dumps(await CLIENT.call_tool(name, arguments))


def invalid_argument(message: str) -> str:
    return dumps(_error("invalid_argument", message, False))
