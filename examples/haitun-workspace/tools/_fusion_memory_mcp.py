from __future__ import annotations

import hashlib
import json
import queue
import sys
import threading
import time
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
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from loguru import logger
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

_, _history_module = _load_sibling_module("_fusion_memory_history")
completed_history_batches = _history_module["completed_history_batches"]
history_paths = _history_module["history_paths"]
load_checkpoint = _history_module["load_checkpoint"]
save_checkpoint = _history_module["save_checkpoint"]


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
        self._supervisor_cancel_scope: anyio.CancelScope | None = None
        self._supervisor_token: EventLoopToken | None = None
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
        self.request_close()
        with self._thread_lock:
            thread = self._thread
            if thread is None:
                return
        while thread.is_alive():  # noqa: ASYNC110 - thread-safe shutdown polling
            await anyio.sleep(0.01)
        with self._thread_lock:
            self._thread = None

    def request_close(self) -> None:
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
            self._closed_event.set()
            if self._terminal_error is None:
                self._terminal_error = _error("client_closed", "Fusion Memory MCP client is closed", True)
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
                opener = self._connector(self.url, self._headers(), self.timeout_seconds)
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
                    result = _error("request_failed", "Fusion Memory request failed", True)
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
        can_replay = (request.retryable and request.name in READ_TOOLS) or _has_idempotency_key(request.arguments)
        attempts = 0
        while True:
            if self._closed_event.is_set():
                return _error("client_closed", "Fusion Memory MCP client is closed", True)
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


@dataclass
class _HistoryWatcher:
    stop: threading.Event
    thread: threading.Thread
    last_activation: float


class MemoryMcpRouter:
    """Resolve trusted Session credentials before selecting an MCP client."""

    def __init__(
        self,
        config: Any,
        *,
        connector: Callable[..., Any] | None = None,
        poll_interval_seconds: float = 1.0,
        watcher_idle_seconds: float = 300.0,
    ) -> None:
        self._config = config
        self._connector = connector
        self._poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self._watcher_idle_seconds = max(0.1, float(watcher_idle_seconds))
        self._clients: dict[_ClientKey, MemoryMcpClient] = {}
        self._session_keys: dict[str, _ClientKey] = {}
        self._watchers: dict[tuple[str, str], _HistoryWatcher] = {}
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
            stale_client.request_close()
        return await client.call_tool(name, arguments, retryable=retryable)

    async def activate_current_session(self, workspace_root: Any) -> dict[str, Any]:
        session_id = get_session_id().strip()
        if not session_id:
            return _error(
                "memory_user_not_configured",
                "Fusion Memory activation requires a trusted Session context",
                False,
            )
        try:
            await resolve_memory_config(session_id, self._config)
        except MemoryConfigError as exc:
            return _error(exc.code, str(exc), False)

        workspace_anyio = await anyio.Path(str(workspace_root)).expanduser()
        workspace_anyio = await workspace_anyio.absolute()
        watcher_key = (str(workspace_anyio), session_id)
        with self._lock:
            existing = self._watchers.get(watcher_key)
            if existing is not None and existing.thread.is_alive():
                existing.last_activation = time.monotonic()
                return {"ok": True, "running": True, "already_running": True}
            stop = threading.Event()
            thread = threading.Thread(
                target=self._watch_history_thread,
                args=(workspace_anyio, session_id, stop),
                name=f"fusion-memory-history-{hashlib.sha256(session_id.encode()).hexdigest()[:8]}",
                daemon=True,
            )
            self._watchers[watcher_key] = _HistoryWatcher(
                stop=stop,
                thread=thread,
                last_activation=time.monotonic(),
            )
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._watchers.pop(watcher_key, None)
            return _error("watcher_start_failed", "Fusion Memory history watcher failed to start", True)
        logger.info(f"Fusion Memory history watcher starting for session {session_id!r}")
        return {"ok": True, "running": True, "already_running": False}

    async def close(self) -> None:
        with self._lock:
            watchers = list(self._watchers.values())
            self._watchers.clear()
            for watcher in watchers:
                watcher.stop.set()
        for watcher in watchers:
            await run_sync_in_worker_thread(watcher.thread.join)

        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._session_keys.clear()
        async with anyio.create_task_group() as task_group:
            for client in clients:
                task_group.start_soon(client.close)

    def _watch_history_thread(self, workspace: anyio.Path, session_id: str, stop: threading.Event) -> None:
        try:
            anyio.run(self._watch_history, workspace, session_id, stop)
        except Exception as exc:
            logger.warning(
                f"Fusion Memory history watcher stopped for session {session_id!r} after {type(exc).__name__}"
            )
        finally:
            watcher_key = (str(workspace), session_id)
            with self._lock:
                existing = self._watchers.get(watcher_key)
                if existing is not None and existing.thread is threading.current_thread():
                    self._watchers.pop(watcher_key, None)

    async def _watch_history(self, workspace: anyio.Path, session_id: str, stop: threading.Event) -> None:
        healthy = False
        backoff = 0.5
        last_error_code = ""
        last_history_signature: tuple[int, int] | None = None
        while not stop.is_set():
            if self._watcher_lease_expired(workspace, session_id):
                stale_client = self._discard_session_client(session_id)
                if stale_client is not None:
                    stale_client.request_close()
                logger.info(f"Fusion Memory history watcher lease expired for session {session_id!r}")
                return
            route_valid, route_changed, route_error = await self._refresh_active_route(session_id)
            if not route_valid:
                healthy = False
                if route_error == "memory_user_not_configured":
                    logger.warning(f"Fusion Memory history watcher revoked for session {session_id!r}")
                    return
                last_error_code = self._log_watcher_error(
                    session_id,
                    _error(route_error, "Fusion Memory route is temporarily unavailable", True),
                    last_error_code,
                )
                await self._wait_for_stop(stop, backoff)
                backoff = min(backoff * 2.0, 30.0)
                continue
            if route_changed:
                healthy = False
            if not healthy:
                result = await self.call_tool_for_session(session_id, "memory_health", {}, retryable=True)
                if result.get("ok") is not True:
                    last_error_code = self._log_watcher_error(session_id, result, last_error_code)
                    await self._wait_for_stop(stop, backoff)
                    backoff = min(backoff * 2.0, 30.0)
                    continue
                healthy = True
                backoff = 0.5
                if last_error_code:
                    logger.info(f"Fusion Memory history watcher recovered for session {session_id!r}")
                    last_error_code = ""
            if stop.is_set():
                break

            history_signature = await self._history_signature(workspace, session_id)
            if history_signature is None or history_signature == last_history_signature:
                await self._wait_for_stop(stop, self._poll_interval_seconds)
                continue
            result = await self._sync_history_once(workspace, session_id)
            if result.get("ok") is not True:
                healthy = False
                last_error_code = self._log_watcher_error(session_id, result, last_error_code)
                await self._wait_for_stop(stop, backoff)
                backoff = min(backoff * 2.0, 30.0)
                continue
            backoff = 0.5
            last_history_signature = history_signature
            await self._wait_for_stop(stop, self._poll_interval_seconds)

    async def _sync_history_once(self, workspace: anyio.Path, session_id: str) -> dict[str, Any]:
        history_path, checkpoint_path = history_paths(workspace, session_id)
        batches = await completed_history_batches(history_path, session_id)
        checkpoint = await load_checkpoint(checkpoint_path)
        submitted = set(checkpoint.get("submitted_batches") or [])
        for batch in batches:
            batch_id = batch["batch_id"]
            if batch_id in submitted:
                continue
            result = await self.call_tool_for_session(
                session_id,
                "memory_add_batch",
                {
                    "messages": batch["messages"],
                    "batch_id": batch_id,
                    "metadata": batch["metadata"],
                },
                retryable=False,
            )
            if result.get("ok") is not True:
                return result
            submitted.add(batch_id)
            checkpoint.setdefault("submitted_batches", []).append(batch_id)
            checkpoint.update(
                {
                    "history_path": str(history_path),
                    "session_id": session_id,
                    "last_batch_id": batch_id,
                }
            )
            await save_checkpoint(checkpoint_path, checkpoint)
        return {"ok": True, "submitted_count": len(submitted), "batch_count": len(batches)}

    @staticmethod
    async def _history_signature(workspace: anyio.Path, session_id: str) -> tuple[int, int] | None:
        history_path, _checkpoint_path = history_paths(workspace, session_id)
        try:
            stat = await history_path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _log_watcher_error(session_id: str, result: dict[str, Any], previous_code: str) -> str:
        error = result.get("error")
        code = str(error.get("code") or "request_failed") if isinstance(error, dict) else "request_failed"
        if code != previous_code:
            logger.warning(f"Fusion Memory history watcher deferred for session {session_id!r}: {code}")
        return code

    @staticmethod
    async def _wait_for_stop(stop: threading.Event, seconds: float) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0 and not stop.is_set():
            delay = min(0.1, remaining)
            await anyio.sleep(delay)
            remaining -= delay

    def _client_for(
        self,
        trusted_session_id: str,
        resolved: Any,
    ) -> tuple[MemoryMcpClient, MemoryMcpClient | None]:
        route_key = trusted_session_id.strip() or resolved.session_id or resolved.identity_key
        key = self._client_key(resolved)
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

    async def _refresh_active_route(self, session_id: str) -> tuple[bool, bool, str]:
        try:
            resolved = await resolve_memory_config(session_id, self._config)
        except MemoryConfigError as exc:
            stale_client = self._discard_session_client(session_id)
            if stale_client is not None:
                stale_client.request_close()
            return False, False, exc.code

        key = self._client_key(resolved)
        route_key = session_id.strip() or resolved.session_id or resolved.identity_key
        with self._lock:
            previous_key = self._session_keys.get(route_key)
            if previous_key is None or previous_key == key:
                return True, False, ""
            stale_client = self._clients.pop(previous_key, None)
            self._session_keys.pop(route_key, None)
        if stale_client is not None:
            stale_client.request_close()
        return True, True, ""

    def _discard_session_client(self, session_id: str) -> MemoryMcpClient | None:
        with self._lock:
            key = self._session_keys.pop(session_id.strip(), None)
            return self._clients.pop(key, None) if key is not None else None

    def _watcher_lease_expired(self, workspace: anyio.Path, session_id: str) -> bool:
        with self._lock:
            watcher = self._watchers.get((str(workspace), session_id))
            if watcher is None:
                return True
            return time.monotonic() - watcher.last_activation >= self._watcher_idle_seconds

    @staticmethod
    def _client_key(resolved: Any) -> _ClientKey:
        return _ClientKey(
            identity_key=resolved.identity_key,
            token_digest=hashlib.sha256(resolved.token.encode()).hexdigest(),
            url=resolved.url,
            workspace_id=resolved.workspace_id,
            session_id=resolved.session_id,
        )


CLIENT = MemoryMcpRouter(CONFIG)
