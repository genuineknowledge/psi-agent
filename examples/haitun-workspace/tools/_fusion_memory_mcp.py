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
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from psi_agent.session.runtime_context import get_session_id

TOOLS_DIR = Path(__file__).resolve().parent
READ_TOOLS = frozenset({"memory_search", "memory_answer_context", "memory_health"})


def _load_sibling_module(name: str) -> tuple[str, dict[str, Any]]:
    path = TOOLS_DIR / f"{name}.py"
    module_name = f"fusion_memory_tool_{name}_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return module_name, existing.__dict__
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module_name, module.__dict__


_, _config_module = _load_sibling_module("_fusion_memory_config")
MemoryMcpConfig = _config_module["MemoryMcpConfig"]
MemoryConfigError = _config_module["MemoryConfigError"]
ResolvedMemoryConfig = _config_module["ResolvedMemoryConfig"]
CONFIG = _config_module["CONFIG"]
resolve_memory_config = _config_module["resolve_memory_config"]
validate_mcp_url = _config_module["validate_mcp_url"]


@dataclass(eq=False)
class _Request:
    name: str
    arguments: dict[str, Any]
    retryable: bool
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    completed: bool = False


class MemoryMcpClient:
    """Lazy MCP client with a backend-neutral anyio supervisor thread."""

    def __init__(
        self,
        url: str,
        token: str,
        workspace_id: str = "haitun",
        session_id: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self.url = validate_mcp_url(url)
        self._token = token.strip()
        self.workspace_id = workspace_id or "haitun"
        self.session_id = session_id or None
        self.timeout_seconds = max(0.1, min(120.0, float(timeout_seconds)))
        self.max_retries = max(0, min(5, int(max_retries)))
        self._connector = connector or _production_connector
        self._incoming: queue.Queue[_Request | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.RLock()
        self._started = threading.Event()
        self._closed_event = threading.Event()
        self._terminal_error: dict[str, Any] | None = None
        self._pending: set[_Request] = set()
        self._closed = False

    async def call_tool(self, name: str, arguments: dict[str, Any], *, retryable: bool) -> dict[str, Any]:
        if not self.url or not self._token:
            missing = "url" if not self.url else "token"
            return _error("configuration_error", f"FUSION_MEMORY_MCP_{missing.upper()} is not configured", False)
        with self._thread_lock:
            if self._closed:
                return _error("client_closed", "Fusion Memory MCP client is closed", True)
        await self._ensure_started()
        request = _Request(name, dict(arguments), retryable)
        with self._thread_lock:
            if self._closed:
                return _error("client_closed", "Fusion Memory MCP client is closed", True)
            if self._terminal_error is not None:
                return dict(self._terminal_error)
            self._pending.add(request)
            self._incoming.put(request)
        while not request.done.is_set():  # noqa: ASYNC110 - set from the supervisor thread
            await anyio.sleep(0.01)
        if request.result is not None:
            return request.result
        return _error("request_failed", "Fusion Memory request failed", True)

    async def close(self) -> None:
        with self._thread_lock:
            thread = self._thread
            if thread is None:
                self._closed = True
                self._closed_event.set()
                return
            if not self._closed:
                self._closed = True
                self._closed_event.set()
                self._incoming.put(None)
        while thread.is_alive():  # noqa: ASYNC110 - thread-safe shutdown polling
            await anyio.sleep(0.01)
        with self._thread_lock:
            self._thread = None

    async def _ensure_started(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            if self._thread is None:
                self._started.clear()
                self._terminal_error = None
                self._thread = threading.Thread(target=self._thread_main, name="fusion-memory-mcp", daemon=True)
                self._thread.start()
            started = self._thread is not None
        if started:
            while not self._started.is_set():  # noqa: ASYNC110 - set from the supervisor thread
                await anyio.sleep(0.01)

    def _thread_main(self) -> None:
        try:
            anyio.run(self._supervisor_main)
        except BaseException:
            self._mark_terminal(self._thread_terminal_result())
        else:
            self._mark_terminal(self._thread_terminal_result())
        finally:
            self._started.set()

    def _thread_terminal_result(self) -> dict[str, Any]:
        if self._started.is_set():
            return _error("client_terminated", "Fusion Memory MCP client terminated", True)
        return _error("client_start_failed", "Fusion Memory MCP client failed to start", True)

    async def _supervisor_main(self) -> None:
        send, receive = anyio.create_memory_object_stream[_Request | None](0)
        async with send, receive, anyio.create_task_group() as task_group:
            self._started.set()
            finished = anyio.Event()
            task_group.start_soon(self._bridge_requests, send)
            task_group.start_soon(self._supervisor_loop, receive, finished)
            await finished.wait()
            task_group.cancel_scope.cancel()

    async def _bridge_requests(self, send: MemoryObjectSendStream[_Request | None]) -> None:
        while True:
            try:
                request = self._incoming.get_nowait()
            except queue.Empty:
                await anyio.sleep(0.01)
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
                opener = self._connector(self.url, self._headers(), self.timeout_seconds)
                session = await new_stack.enter_async_context(opener)
                await session.initialize()
            except BaseException:
                with suppress(Exception, BaseExceptionGroup):
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
                    result = _error("request_failed", "Fusion Memory request failed", True)
                self._complete_request(request, result)
        finally:
            await disconnect()
            finished.set()

    async def _execute(
        self,
        request: _Request,
        connect: Callable[[], Any],
        disconnect: Callable[[], Any],
    ) -> dict[str, Any]:
        can_replay = (request.retryable and request.name in READ_TOOLS) or _has_idempotency_key(request.arguments)
        attempts = 0
        while True:
            try:
                session = await connect()
                return _normalize_result(await session.call_tool(request.name, request.arguments))
            except Exception as exc:
                await disconnect()
                if isinstance(exc, httpx.HTTPStatusError):
                    status_code = exc.response.status_code
                    if status_code in {401, 403}:
                        return _error("unauthorized", "Fusion Memory authentication failed", False)
                    if status_code in {400, 405, 406, 415, 422}:
                        return _error("remote_request_error", "Fusion Memory rejected the request", False)
                if not can_replay or attempts >= self.max_retries:
                    return _error("transport_error", "Fusion Memory transport failed", True)
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
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Fusion-Memory-Workspace": self.workspace_id,
        }
        if self.session_id:
            headers["X-Fusion-Memory-Session"] = self.session_id
        return headers


@asynccontextmanager
async def _production_connector(url: str, headers: dict[str, str], timeout_seconds: float) -> AsyncIterator[Any]:
    timeout = httpx.Timeout(timeout_seconds)
    async with (
        httpx.AsyncClient(headers=headers, timeout=timeout) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, *_),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout_seconds)) as session,
    ):
        yield session


def _has_idempotency_key(arguments: dict[str, Any]) -> bool:
    return any(arguments.get(key) for key in ("idempotency_key", "idempotencyKey", "batch_id"))


def _normalize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if getattr(result, "isError", False) and structured.get("ok") is not False:
            return {
                "ok": False,
                "error": structured.get("error")
                or {"code": "remote_error", "message": "Fusion Memory tool returned an error", "retryable": True},
                "result": structured,
            }
        return structured
    content = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            content.append(text)
    payload: Any = "\n".join(content)
    if len(content) == 1:
        with suppress(json.JSONDecodeError):
            payload = json.loads(content[0])
    return {"ok": not bool(getattr(result, "isError", False)), "result": payload}


def _error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


@dataclass(frozen=True)
class _ClientKey:
    identity_key: str
    token_digest: str
    url: str
    workspace_id: str
    session_id: str | None


class MemoryMcpRouter:
    """Resolve trusted Session credentials before selecting an MCP client."""

    def __init__(
        self,
        config: Any,
        *,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._connector = connector
        self._clients: dict[_ClientKey, MemoryMcpClient] = {}
        self._session_keys: dict[str, _ClientKey] = {}
        self._lock = threading.RLock()

    async def call_tool(self, name: str, arguments: dict[str, Any], *, retryable: bool) -> dict[str, Any]:
        return await self.call_tool_for_session(get_session_id(), name, arguments, retryable=retryable)

    async def call_tool_for_session(
        self,
        session_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        retryable: bool,
    ) -> dict[str, Any]:
        try:
            resolved = await resolve_memory_config(session_id, self._config)
        except MemoryConfigError as exc:
            return _error(exc.code, str(exc), False)

        client, stale_client = self._client_for(session_id, resolved)
        if stale_client is not None:
            await stale_client.close()
        return await client.call_tool(name, arguments, retryable=retryable)

    async def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._session_keys.clear()
        async with anyio.create_task_group() as task_group:
            for client in clients:
                task_group.start_soon(client.close)

    def _client_for(
        self,
        trusted_session_id: str,
        resolved: Any,
    ) -> tuple[MemoryMcpClient, MemoryMcpClient | None]:
        route_key = trusted_session_id.strip() or resolved.session_id or resolved.identity_key
        key = _ClientKey(
            identity_key=resolved.identity_key,
            token_digest=hashlib.sha256(resolved.token.encode()).hexdigest(),
            url=resolved.url,
            workspace_id=resolved.workspace_id,
            session_id=resolved.session_id,
        )
        with self._lock:
            stale_client = None
            previous_key = self._session_keys.get(route_key)
            if previous_key is not None and previous_key != key:
                stale_client = self._clients.pop(previous_key, None)
            client = self._clients.get(key)
            if client is None:
                client = MemoryMcpClient(
                    resolved.url,
                    resolved.token,
                    resolved.workspace_id,
                    resolved.session_id,
                    resolved.timeout_seconds,
                    resolved.max_retries,
                    connector=self._connector,
                )
                self._clients[key] = client
            self._session_keys[route_key] = key
        return client, stale_client


CLIENT = MemoryMcpRouter(CONFIG)
