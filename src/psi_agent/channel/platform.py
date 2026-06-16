from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from loguru import logger

from psi_agent.channel.session_client import collect_session_reply
from psi_agent.errors import UserFacingError
from psi_agent.net import make_server_site

PlatformProvider = Literal["telegram", "whatsapp", "discord", "slack", "qq", "wechat", "feishu", "dingtalk"]


@dataclass(frozen=True)
class PlatformMessage:
    provider: PlatformProvider
    target_id: str
    text: str
    user_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


class PlatformAdapter(Protocol):
    provider: PlatformProvider

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None: ...

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None: ...

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None: ...

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]: ...

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None: ...


@dataclass
class DeduplicationStore:
    ttl_seconds: float = 600
    max_entries: int = 4096
    items: OrderedDict[str, float] = field(default_factory=OrderedDict)

    def mark_new(self, key: str) -> bool:
        now = time.monotonic()
        self._prune(now)

        expires_at = self.items.get(key)
        if expires_at is not None and expires_at > now:
            return False

        self.items[key] = now + self.ttl_seconds
        self.items.move_to_end(key)
        self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        expired = [key for key, expires_at in self.items.items() if expires_at <= now]
        for key in expired:
            del self.items[key]
        while len(self.items) > self.max_entries:
            self.items.popitem(last=False)


SESSION_SOCKET_KEY = web.AppKey("session_socket", str)
ADAPTER_KEY = web.AppKey("adapter", PlatformAdapter)
BACKGROUND_TASKS_KEY = web.AppKey("background_tasks", set[asyncio.Task[None]])
DEDUPLICATION_STORE_KEY = web.AppKey("deduplication_store", DeduplicationStore)
DEDUPLICATION_STORES_KEY = web.AppKey("deduplication_stores", dict[str, DeduplicationStore])


async def serve_platform_channel(
    *,
    session_socket: str,
    listen: str,
    webhook_path: str,
    adapter: PlatformAdapter,
) -> None:
    path = _resolve_webhook_path(listen, webhook_path)

    app = web.Application()
    app[SESSION_SOCKET_KEY] = session_socket
    app[ADAPTER_KEY] = adapter
    app[BACKGROUND_TASKS_KEY] = set()
    app[DEDUPLICATION_STORE_KEY] = DeduplicationStore()
    app.on_cleanup.append(_cleanup_background_tasks)
    app.router.add_get(path, handle_platform_get)
    app.router.add_post(path, handle_platform_post)

    runner = web.AppRunner(app)
    await runner.setup()
    site = await make_server_site(runner, listen)
    await site.start()

    logger.info(f"{adapter.provider} channel listening on {listen} path={path}")

    try:
        await anyio.sleep_forever()
    finally:
        await runner.cleanup()


async def serve_platform_channels(
    *,
    session_socket: str,
    listen: str,
    routes: list[tuple[str, PlatformAdapter]],
) -> None:
    if not routes:
        raise UserFacingError("Platform channel group has no routes.")

    route_paths = _resolve_group_route_paths(listen, routes)
    app = web.Application()
    app[SESSION_SOCKET_KEY] = session_socket
    app[BACKGROUND_TASKS_KEY] = set()
    app[DEDUPLICATION_STORES_KEY] = {path: DeduplicationStore() for path in route_paths}
    app.on_cleanup.append(_cleanup_background_tasks)

    for path, (_, adapter) in zip(route_paths, routes, strict=True):
        app.router.add_get(path, _make_platform_get_handler(adapter))
        app.router.add_post(path, _make_platform_post_handler(adapter, path))

    runner = web.AppRunner(app)
    await runner.setup()
    site = await make_server_site(runner, listen)
    await site.start()

    route_summary = ", ".join(
        f"{adapter.provider}:{path}" for path, (_, adapter) in zip(route_paths, routes, strict=True)
    )
    logger.info(f"Platform channel group listening on {listen} routes={route_summary}")

    try:
        await anyio.sleep_forever()
    finally:
        await runner.cleanup()


async def handle_platform_get(request: web.Request) -> web.StreamResponse:
    adapter = request.app[ADAPTER_KEY]
    return await _handle_platform_get_for_adapter(request, adapter)


async def _handle_platform_get_for_adapter(
    request: web.Request,
    adapter: PlatformAdapter,
) -> web.StreamResponse:
    response = await adapter.handle_get(request)
    if response is not None:
        return response
    return web.json_response({"ok": True, "provider": adapter.provider})


async def handle_platform_post(request: web.Request) -> web.StreamResponse:
    adapter = request.app[ADAPTER_KEY]
    return await _handle_platform_post_for_adapter(
        request,
        adapter=adapter,
        session_socket=request.app[SESSION_SOCKET_KEY],
        deduplication_store=request.app[DEDUPLICATION_STORE_KEY],
    )


async def _handle_platform_post_for_adapter(
    request: web.Request,
    *,
    adapter: PlatformAdapter,
    session_socket: str,
    deduplication_store: DeduplicationStore,
) -> web.StreamResponse:
    raw_body = await request.read()
    await adapter.validate_post(request, raw_body)

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as e:
        return web.json_response({"ok": False, "error": f"invalid JSON: {e}"}, status=400)

    control_response = await adapter.handle_control(body)
    if control_response is not None:
        return control_response

    messages = adapter.extract_messages(body)
    if not messages:
        return web.json_response({"ok": True, "messages": 0})

    fresh_messages: list[PlatformMessage] = []
    duplicate_count = 0
    for message in messages:
        deduplication_key = _message_deduplication_key(message)
        if deduplication_key and not deduplication_store.mark_new(deduplication_key):
            duplicate_count += 1
            logger.info(f"Ignoring duplicate {message.provider} webhook message: {deduplication_key}")
            continue
        fresh_messages.append(message)

    if fresh_messages:
        _track_background_task(
            request.app,
            _process_platform_messages(
                session_socket=session_socket,
                adapter=adapter,
                messages=fresh_messages,
            ),
        )

    return web.json_response(
        {"ok": True, "messages": len(messages), "queued": len(fresh_messages), "duplicates": duplicate_count}
    )


def _make_platform_get_handler(adapter: PlatformAdapter):
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _handle_platform_get_for_adapter(request, adapter)

    return handler


def _make_platform_post_handler(adapter: PlatformAdapter, path: str):
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _handle_platform_post_for_adapter(
            request,
            adapter=adapter,
            session_socket=request.app[SESSION_SOCKET_KEY],
            deduplication_store=request.app[DEDUPLICATION_STORES_KEY][path],
        )

    return handler


async def _process_platform_messages(
    *,
    session_socket: str,
    adapter: PlatformAdapter,
    messages: list[PlatformMessage],
) -> None:
    timeout = ClientTimeout(total=None)
    async with ClientSession(connector=TCPConnector(ssl=True), timeout=timeout) as session:
        for message in messages:
            try:
                reply = await collect_session_reply(session_socket=session_socket, message=message.text)
                if reply:
                    await adapter.send_reply(session, message, reply)
            except Exception as e:
                logger.exception(f"{message.provider} channel message processing failed: {e}")


def _track_background_task(app: web.Application, coroutine) -> None:
    task = asyncio.create_task(coroutine)
    app[BACKGROUND_TASKS_KEY].add(task)
    task.add_done_callback(lambda completed: _finish_background_task(app, completed))


def _finish_background_task(app: web.Application, task: asyncio.Task[None]) -> None:
    app[BACKGROUND_TASKS_KEY].discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.exception(f"Platform background task failed: {e}")


async def _cleanup_background_tasks(app: web.Application) -> None:
    tasks = app[BACKGROUND_TASKS_KEY]
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _message_deduplication_key(message: PlatformMessage) -> str:
    for metadata_key in ("event_id", "message_id", "interaction_id"):
        metadata_value = message.metadata.get(metadata_key)
        if metadata_value:
            return f"{message.provider}:{metadata_key}:{metadata_value}"
    return ""


async def post_platform_json(
    session: ClientSession,
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> None:
    async with session.post(url, json=payload, headers=headers) as response:
        body = await response.text()
        if response.status >= 400:
            raise UserFacingError(f"Platform API request failed: HTTP {response.status}: {body[:300]}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict) and data.get("ok") is False:
            error = data.get("error") or data.get("description") or body[:300]
            raise UserFacingError(f"Platform API request failed: {error}")
        if isinstance(data, dict) and data.get("errcode") not in {None, 0}:
            error = data.get("errmsg") or body[:300]
            raise UserFacingError(f"Platform API request failed: errcode={data.get('errcode')}: {error}")
        if isinstance(data, dict) and data.get("code") not in {None, 0}:
            error = data.get("msg") or data.get("message") or body[:300]
            raise UserFacingError(f"Platform API request failed: code={data.get('code')}: {error}")
        logger.info(f"Platform API request succeeded: {_redacted_url_for_log(url)}")


def _resolve_webhook_path(listen: str, webhook_path: str) -> str:
    parsed = urlparse(listen)
    path = parsed.path or webhook_path
    if not path.startswith("/"):
        return f"/{path}"
    return path


def _resolve_group_route_paths(listen: str, routes: list[tuple[str, PlatformAdapter]]) -> list[str]:
    parsed = urlparse(listen)
    listen_path = parsed.path
    paths: list[str] = []
    seen: set[str] = set()
    for webhook_path, adapter in routes:
        path = listen_path or webhook_path
        if not path.startswith("/"):
            path = f"/{path}"
        if path in seen:
            raise UserFacingError(
                f"Duplicate platform channel route path: {path}",
                f"Give each channel on {listen} a unique webhook_path. Duplicate provider: {adapter.provider}.",
            )
        seen.add(path)
        paths.append(path)
    return paths


def _redacted_url_for_log(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
