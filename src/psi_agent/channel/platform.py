from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlparse

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector, WSMsgType, web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.channel.session_client import collect_session_reply
from psi_agent.errors import UserFacingError
from psi_agent.net import make_server_site

PlatformProvider = Literal["telegram", "whatsapp", "discord", "slack", "qq", "wechat", "feishu", "dingtalk"]
DiscordMode = Literal["webhook", "gateway"]

DISCORD_DEFAULT_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
DISCORD_DEFAULT_GATEWAY_INTENTS = 1 | 512 | 4096 | 32768


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


@dataclass
class DiscordGatewayState:
    sequence: int | None = None


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


async def serve_discord_gateway_channel(
    *,
    session_socket: str,
    bot_token: str,
    api_base_url: str,
    gateway_url: str,
    gateway_intents: int,
) -> None:
    adapter = DiscordAdapter(bot_token=bot_token, api_base_url=api_base_url)
    timeout = ClientTimeout(total=None)

    async with ClientSession(timeout=timeout) as session:
        while True:
            try:
                logger.info(f"Connecting to Discord Gateway at {gateway_url}")
                async with session.ws_connect(gateway_url) as ws:
                    await _run_discord_gateway_connection(
                        ws=ws,
                        session=session,
                        session_socket=session_socket,
                        adapter=adapter,
                        bot_token=bot_token,
                        gateway_intents=gateway_intents,
                    )
            except Exception as e:
                logger.error(f"Discord Gateway connection error: {e}")
                await anyio.sleep(5)


async def _run_discord_gateway_connection(
    *,
    ws,
    session: ClientSession,
    session_socket: str,
    adapter: DiscordAdapter,
    bot_token: str,
    gateway_intents: int,
) -> None:
    state = DiscordGatewayState()

    async with anyio.create_task_group() as tg:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                payload = json.loads(msg.data)
                sequence = payload.get("s")
                if isinstance(sequence, int):
                    state.sequence = sequence

                op = payload.get("op")
                if op == 10:
                    interval_ms = _discord_heartbeat_interval(payload)
                    await _discord_identify(ws, bot_token=bot_token, gateway_intents=gateway_intents)
                    tg.start_soon(_discord_heartbeat_loop, ws, state, interval_ms / 1000)
                    continue
                if op == 0 and payload.get("t") == "MESSAGE_CREATE":
                    data = payload.get("d")
                    if isinstance(data, dict):
                        await _handle_discord_gateway_message(
                            session=session,
                            session_socket=session_socket,
                            adapter=adapter,
                            event=cast(dict[str, Any], data),
                        )
                    continue
                if op in {7, 9}:
                    logger.warning(f"Discord Gateway requested reconnect, op={op}")
                    tg.cancel_scope.cancel()
                    return
            elif msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                tg.cancel_scope.cancel()
                return


async def _discord_identify(ws, *, bot_token: str, gateway_intents: int) -> None:
    await ws.send_json(
        {
            "op": 2,
            "d": {
                "token": bot_token,
                "intents": gateway_intents,
                "properties": {
                    "os": os.name,
                    "browser": "psi-agent",
                    "device": "psi-agent",
                },
            },
        }
    )


async def _discord_heartbeat_loop(ws, state: DiscordGatewayState, interval_seconds: float) -> None:
    while True:
        await anyio.sleep(interval_seconds)
        await ws.send_json({"op": 1, "d": state.sequence})


async def _handle_discord_gateway_message(
    *,
    session: ClientSession,
    session_socket: str,
    adapter: DiscordAdapter,
    event: dict[str, Any],
) -> None:
    for message in adapter.extract_messages(event):
        reply = await collect_session_reply(session_socket=session_socket, message=message.text)
        if reply:
            await adapter.send_reply(session, message, reply)


def _discord_heartbeat_interval(payload: dict[str, Any]) -> int:
    data = payload.get("d")
    if not isinstance(data, dict):
        raise UserFacingError("Discord Gateway hello payload is missing heartbeat metadata.")
    interval = data.get("heartbeat_interval")
    if not isinstance(interval, int):
        raise UserFacingError("Discord Gateway hello payload is missing heartbeat_interval.")
    return interval


@dataclass(frozen=True)
class TelegramAdapter:
    token: str
    api_base_url: str = "https://api.telegram.org"
    webhook_secret: str = ""

    provider: PlatformProvider = "telegram"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = raw_body
        if self.webhook_secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != self.webhook_secret:
            raise web.HTTPUnauthorized(text="invalid Telegram webhook secret")

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        _ = body
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        update = body.get("message") or body.get("edited_message") or body.get("channel_post")
        if not isinstance(update, dict):
            return []

        text = update.get("text") or update.get("caption")
        chat = update.get("chat")
        if not text or not isinstance(chat, dict):
            return []

        chat_id = chat.get("id")
        if chat_id is None:
            return []

        sender = update.get("from") if isinstance(update.get("from"), dict) else {}
        update_id = body.get("update_id")
        message_id = update.get("message_id")
        metadata = {
            key: value
            for key, value in {
                "event_id": str(update_id) if update_id is not None else "",
                "message_id": str(message_id) if message_id is not None else "",
            }.items()
            if value
        }
        return [
            PlatformMessage(
                provider="telegram",
                target_id=str(chat_id),
                user_id=str(sender.get("id") or ""),
                text=str(text),
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        url = f"{self.api_base_url.rstrip('/')}/bot{self.token}/sendMessage"
        payload = {"chat_id": message.target_id, "text": text}
        await post_platform_json(session, url, payload)


@dataclass(frozen=True)
class WhatsAppAdapter:
    token: str
    phone_number_id: str
    api_base_url: str = "https://graph.facebook.com"
    verify_token: str = ""

    provider: PlatformProvider = "whatsapp"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = request, raw_body

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        if request.query.get("hub.mode") != "subscribe":
            return None
        if self.verify_token and request.query.get("hub.verify_token") != self.verify_token:
            raise web.HTTPForbidden(text="invalid WhatsApp verify token")
        challenge = request.query.get("hub.challenge", "")
        return web.Response(text=challenge)

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        _ = body
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        messages: list[PlatformMessage] = []
        for entry in _iter_dicts(body.get("entry")):
            for change in _iter_dicts(entry.get("changes")):
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                for item in _iter_dicts(value.get("messages")):
                    text = _whatsapp_message_text(item)
                    sender = item.get("from")
                    if text and sender:
                        message_id = item.get("id")
                        messages.append(
                            PlatformMessage(
                                provider="whatsapp",
                                target_id=str(sender),
                                user_id=str(sender),
                                text=text,
                                metadata={"message_id": str(message_id)} if message_id else {},
                            )
                        )
        return messages

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        url = f"{self.api_base_url.rstrip('/')}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": message.target_id,
            "type": "text",
            "text": {"body": text},
        }
        await post_platform_json(session, url, payload, headers=headers)


@dataclass(frozen=True)
class SlackAdapter:
    bot_token: str
    api_base_url: str = "https://slack.com/api"
    signing_secret: str = ""

    provider: PlatformProvider = "slack"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        if not self.signing_secret:
            return
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        base = b"v0:" + timestamp.encode() + b":" + raw_body
        digest = hmac.new(self.signing_secret.encode(), base, hashlib.sha256).hexdigest()
        expected = f"v0={digest}"
        if not hmac.compare_digest(signature, expected):
            raise web.HTTPUnauthorized(text="invalid Slack signature")

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        if body.get("type") == "url_verification":
            return web.Response(text=str(body.get("challenge", "")))
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        event = body.get("event")
        if not isinstance(event, dict):
            return []
        if event.get("type") != "message" or event.get("subtype") or event.get("bot_id"):
            return []

        channel = event.get("channel")
        text = event.get("text")
        if not channel or not text:
            return []

        event_id = _first_string(body, "event_id", "eventId")
        message_id = _first_string(event, "client_msg_id", "ts", "event_ts")
        metadata = {
            key: value
            for key, value in {
                "event_id": event_id,
                "message_id": message_id,
            }.items()
            if value
        }
        return [
            PlatformMessage(
                provider="slack",
                target_id=str(channel),
                user_id=str(event.get("user") or ""),
                text=str(text),
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        url = f"{self.api_base_url.rstrip('/')}/chat.postMessage"
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        payload = {"channel": message.target_id, "text": text}
        await post_platform_json(session, url, payload, headers=headers)


@dataclass(frozen=True)
class DiscordAdapter:
    bot_token: str
    api_base_url: str = "https://discord.com/api/v10"
    relay_secret: str = ""

    provider: PlatformProvider = "discord"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = raw_body
        if self.relay_secret and request.headers.get("Authorization") != f"Bearer {self.relay_secret}":
            raise web.HTTPUnauthorized(text="invalid Discord relay secret")

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        if body.get("type") == 1:
            return web.json_response({"type": 1})
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        if body.get("type") in {2, 4}:
            return self._extract_interaction(body)
        return self._extract_message_create(body)

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        interaction_id = message.metadata.get("interaction_id")
        interaction_token = message.metadata.get("interaction_token")
        if interaction_id and interaction_token:
            url = f"{self.api_base_url.rstrip('/')}/interactions/{interaction_id}/{interaction_token}/callback"
            await post_platform_json(session, url, {"type": 4, "data": {"content": text}})
            return

        url = f"{self.api_base_url.rstrip('/')}/channels/{message.target_id}/messages"
        headers = {"Authorization": f"Bot {self.bot_token}"}
        await post_platform_json(session, url, {"content": text}, headers=headers)

    def _extract_message_create(self, body: dict[str, Any]) -> list[PlatformMessage]:
        raw_author = body.get("author")
        author = raw_author if isinstance(raw_author, dict) else {}
        if author.get("bot"):
            return []

        channel_id = body.get("channel_id")
        content = body.get("content")
        if not channel_id or not content:
            return []

        return [
            PlatformMessage(
                provider="discord",
                target_id=str(channel_id),
                user_id=str(author.get("id") or ""),
                text=str(content),
                metadata={"message_id": str(body.get("id"))} if body.get("id") else {},
            )
        ]

    def _extract_interaction(self, body: dict[str, Any]) -> list[PlatformMessage]:
        channel_id = body.get("channel_id")
        interaction_id = body.get("id")
        interaction_token = body.get("token")
        if not channel_id or not interaction_id or not interaction_token:
            return []

        raw_data = body.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        text = _discord_interaction_text(data)
        if not text:
            return []

        raw_member = body.get("member")
        member = raw_member if isinstance(raw_member, dict) else {}
        raw_user = member.get("user") or body.get("user")
        user = raw_user if isinstance(raw_user, dict) else {}
        return [
            PlatformMessage(
                provider="discord",
                target_id=str(channel_id),
                user_id=str(user.get("id") or ""),
                text=text,
                metadata={
                    "interaction_id": str(interaction_id),
                    "interaction_token": str(interaction_token),
                },
            )
        ]


@dataclass(frozen=True)
class WeChatBridgeAdapter:
    reply_url: str = ""
    bridge_secret: str = ""

    provider: PlatformProvider = "wechat"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = raw_body
        if not self.bridge_secret:
            return
        authorization = request.headers.get("Authorization", "")
        header_secret = request.headers.get("X-WeChat-Bridge-Secret", "")
        if authorization == f"Bearer {self.bridge_secret}" or header_secret == self.bridge_secret:
            return
        raise web.HTTPUnauthorized(text="invalid WeChat bridge secret")

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        if body.get("type") == "ping":
            return web.json_response({"ok": True, "provider": self.provider, "type": "pong"})
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        event_type = body.get("type") or body.get("event")
        if event_type and event_type not in {"message", "text"}:
            return []

        raw_message = body.get("message")
        message = raw_message if isinstance(raw_message, dict) else body
        text = _first_string(message, "text", "content", "body")
        if not text:
            return []

        target_id = _first_string(
            message,
            "conversation_id",
            "chat_id",
            "room_id",
            "group_id",
            "target_id",
            "user_id",
            "from_user",
        )
        if not target_id:
            return []

        user_id = _first_string(message, "user_id", "from_user", "sender_id", "open_id")
        message_id = _first_string(message, "message_id", "msg_id", "id")
        event_id = _first_string(body, "event_id", "eventId", "uuid")
        reply_url = _first_string(message, "reply_url") or _first_string(body, "reply_url")

        metadata = {
            key: value
            for key, value in {
                "event_id": event_id,
                "conversation_id": target_id,
                "message_id": message_id,
                "reply_url": reply_url,
            }.items()
            if value
        }
        return [
            PlatformMessage(
                provider="wechat",
                target_id=target_id,
                user_id=user_id,
                text=text,
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        reply_url = message.metadata.get("reply_url") or self.reply_url
        if not reply_url:
            raise UserFacingError(
                "Missing WeChat bridge reply URL.",
                "Pass --reply-url, set WECHAT_BRIDGE_REPLY_URL, or include reply_url in the bridge payload.",
            )

        payload = {
            "conversation_id": message.metadata.get("conversation_id") or message.target_id,
            "user_id": message.user_id,
            "text": text,
        }
        message_id = message.metadata.get("message_id")
        if message_id:
            payload["in_reply_to"] = message_id

        headers = {"Authorization": f"Bearer {self.bridge_secret}"} if self.bridge_secret else None
        await post_platform_json(session, reply_url, payload, headers=headers)


@dataclass(frozen=True)
class QQBridgeAdapter:
    reply_url: str = ""
    bridge_secret: str = ""

    provider: PlatformProvider = "qq"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = raw_body
        if not self.bridge_secret:
            return
        authorization = request.headers.get("Authorization", "")
        header_secret = request.headers.get("X-QQ-Bridge-Secret", "")
        if authorization == f"Bearer {self.bridge_secret}" or header_secret == self.bridge_secret:
            return
        raise web.HTTPUnauthorized(text="invalid QQ bridge secret")

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        if body.get("type") == "ping":
            return web.json_response({"ok": True, "provider": self.provider, "type": "pong"})
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        event_type = body.get("type") or body.get("event")
        if event_type and event_type not in {"message", "text"}:
            return []

        raw_message = body.get("message")
        message = raw_message if isinstance(raw_message, dict) else body
        text = _first_string(message, "text", "content", "body")
        if not text:
            return []

        target_id = _first_string(
            message,
            "conversation_id",
            "channel_id",
            "group_id",
            "chat_id",
            "target_id",
            "user_id",
            "open_id",
        )
        if not target_id:
            return []

        user_id = _first_string(message, "user_id", "author_id", "sender_id", "open_id")
        message_id = _first_string(message, "message_id", "msg_id", "id")
        event_id = _first_string(body, "event_id", "eventId", "uuid")
        reply_url = _first_string(message, "reply_url") or _first_string(body, "reply_url")
        metadata = {
            key: value
            for key, value in {
                "event_id": event_id,
                "conversation_id": target_id,
                "message_id": message_id,
                "reply_url": reply_url,
            }.items()
            if value
        }
        return [
            PlatformMessage(
                provider="qq",
                target_id=target_id,
                user_id=user_id,
                text=text,
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        reply_url = message.metadata.get("reply_url") or self.reply_url
        if not reply_url:
            raise UserFacingError(
                "Missing QQ bridge reply URL.",
                "Pass --reply-url, set QQ_BRIDGE_REPLY_URL, or include reply_url in the bridge payload.",
            )

        payload = {
            "conversation_id": message.metadata.get("conversation_id") or message.target_id,
            "user_id": message.user_id,
            "text": text,
        }
        message_id = message.metadata.get("message_id")
        if message_id:
            payload["in_reply_to"] = message_id

        headers = {"Authorization": f"Bearer {self.bridge_secret}"} if self.bridge_secret else None
        await post_platform_json(session, reply_url, payload, headers=headers)


@dataclass(frozen=True)
class FeishuAdapter:
    tenant_access_token: str = ""
    app_id: str = ""
    app_secret: str = ""
    api_base_url: str = "https://open.feishu.cn"
    verification_token: str = ""

    provider: PlatformProvider = "feishu"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = request, raw_body

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        if body.get("type") != "url_verification":
            return None
        if self.verification_token and body.get("token") != self.verification_token:
            raise web.HTTPForbidden(text="invalid Feishu verification token")
        return web.json_response({"challenge": str(body.get("challenge") or "")})

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        raw_event = body.get("event")
        event = raw_event if isinstance(raw_event, dict) else body
        raw_header = body.get("header")
        header = raw_header if isinstance(raw_header, dict) else {}
        raw_message = event.get("message")
        message = raw_message if isinstance(raw_message, dict) else event

        text = _feishu_message_text(message)
        target_id = _first_string(message, "chat_id", "open_chat_id", "chatId")
        if not text or not target_id:
            return []

        message_id = _first_string(message, "message_id", "messageId", "id")
        event_id = _first_string(header, "event_id", "eventId") or _first_string(body, "event_id", "eventId", "uuid")
        metadata = {
            key: value
            for key, value in {
                "event_id": event_id,
                "message_id": message_id,
                "chat_id": target_id,
            }.items()
            if value
        }
        return [
            PlatformMessage(
                provider="feishu",
                target_id=target_id,
                user_id=_feishu_sender_id(event),
                text=text,
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        message_id = message.metadata.get("message_id")
        tenant_access_token = self.tenant_access_token or await _fetch_feishu_tenant_access_token(
            session=session,
            api_base_url=self.api_base_url,
            app_id=self.app_id,
            app_secret=self.app_secret,
        )
        headers = {"Authorization": f"Bearer {tenant_access_token}"}
        content = json.dumps({"text": text}, ensure_ascii=False)
        if message_id:
            url = f"{self.api_base_url.rstrip('/')}/open-apis/im/v1/messages/{message_id}/reply"
            payload = {"msg_type": "text", "content": content}
        else:
            url = f"{self.api_base_url.rstrip('/')}/open-apis/im/v1/messages?receive_id_type=chat_id"
            payload = {"receive_id": message.target_id, "msg_type": "text", "content": content}
        await post_platform_json(session, url, payload, headers=headers)


@dataclass(frozen=True)
class DingTalkAdapter:
    session_webhook: str = ""
    outgoing_token: str = ""

    provider: PlatformProvider = "dingtalk"

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        _ = request, raw_body

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        _ = request
        return None

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        if body.get("type") == "ping":
            return web.json_response({"ok": True, "provider": self.provider, "type": "pong"})
        return None

    def extract_messages(self, body: dict[str, Any]) -> list[PlatformMessage]:
        if self.outgoing_token and body.get("token") != self.outgoing_token:
            return []

        msg_type = body.get("msgtype") or body.get("msgType")
        if msg_type and msg_type != "text":
            return []

        text = _dingtalk_message_text(body)
        target_id = _first_string(body, "conversationId", "conversation_id", "chat_id")
        if not text or not target_id:
            return []

        message_id = _first_string(body, "msgId", "message_id", "id")
        event_id = _first_string(body, "event_id", "eventId", "uuid")
        reply_url = _first_string(body, "sessionWebhook", "reply_url")
        metadata = {
            key: value
            for key, value in {
                "event_id": event_id,
                "message_id": message_id,
                "reply_url": reply_url,
            }.items()
            if value
        }
        return [
            PlatformMessage(
                provider="dingtalk",
                target_id=target_id,
                user_id=_first_string(body, "senderId", "senderStaffId", "senderNick", "user_id"),
                text=text,
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        reply_url = message.metadata.get("reply_url") or self.session_webhook
        if not reply_url:
            raise UserFacingError(
                "Missing DingTalk session webhook.",
                "Pass --session-webhook, set DINGTALK_SESSION_WEBHOOK, or include sessionWebhook in the payload.",
            )
        await post_platform_json(session, reply_url, {"msgtype": "text", "text": {"content": text}})


@dataclass
class ChannelTelegram:
    """Telegram webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    token: str = ""
    """Telegram bot token. Falls back to TELEGRAM_BOT_TOKEN."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    api_base_url: str = "https://api.telegram.org"
    """Telegram Bot API base URL."""

    webhook_secret: str = ""
    """Optional Telegram webhook secret token."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        token = self.token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise UserFacingError("Missing Telegram bot token.", "Pass --token or set TELEGRAM_BOT_TOKEN.")
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=TelegramAdapter(token=token, api_base_url=self.api_base_url, webhook_secret=self.webhook_secret),
        )


@dataclass
class ChannelWhatsApp:
    """WhatsApp Cloud API webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    token: str = ""
    """WhatsApp Cloud API access token. Falls back to WHATSAPP_ACCESS_TOKEN."""

    phone_number_id: str = ""
    """WhatsApp phone number ID. Falls back to WHATSAPP_PHONE_NUMBER_ID."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    api_base_url: str = "https://graph.facebook.com"
    """WhatsApp Graph API base URL."""

    verify_token: str = ""
    """Optional WhatsApp webhook verification token. Falls back to WHATSAPP_VERIFY_TOKEN."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        token = self.token or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        phone_number_id = self.phone_number_id or os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        verify_token = self.verify_token or os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        if not token:
            raise UserFacingError("Missing WhatsApp access token.", "Pass --token or set WHATSAPP_ACCESS_TOKEN.")
        if not phone_number_id:
            raise UserFacingError(
                "Missing WhatsApp phone number ID.",
                "Pass --phone-number-id or set WHATSAPP_PHONE_NUMBER_ID.",
            )
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=WhatsAppAdapter(
                token=token,
                phone_number_id=phone_number_id,
                api_base_url=self.api_base_url,
                verify_token=verify_token,
            ),
        )


@dataclass
class ChannelDiscord:
    """Discord webhook relay or Gateway channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    bot_token: str = ""
    """Discord bot token. Falls back to DISCORD_BOT_TOKEN."""

    mode: DiscordMode = "webhook"
    """Discord channel mode: webhook or gateway."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on in webhook mode."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path in webhook mode."""

    api_base_url: str = "https://discord.com/api/v10"
    """Discord API base URL."""

    gateway_url: str = DISCORD_DEFAULT_GATEWAY_URL
    """Discord Gateway WebSocket URL."""

    gateway_intents: int = DISCORD_DEFAULT_GATEWAY_INTENTS
    """Discord Gateway intents bitfield."""

    relay_secret: str = ""
    """Optional bearer token required for custom message relay POSTs."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        bot_token = self.bot_token or os.environ.get("DISCORD_BOT_TOKEN", "")
        if not bot_token:
            raise UserFacingError("Missing Discord bot token.", "Pass --bot-token or set DISCORD_BOT_TOKEN.")
        if self.mode == "gateway":
            await serve_discord_gateway_channel(
                session_socket=self.session_socket,
                bot_token=bot_token,
                api_base_url=self.api_base_url,
                gateway_url=self.gateway_url,
                gateway_intents=self.gateway_intents,
            )
            return
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=DiscordAdapter(bot_token=bot_token, api_base_url=self.api_base_url, relay_secret=self.relay_secret),
        )


@dataclass
class ChannelSlack:
    """Slack Events API webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    bot_token: str = ""
    """Slack bot token. Falls back to SLACK_BOT_TOKEN."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    api_base_url: str = "https://slack.com/api"
    """Slack Web API base URL."""

    signing_secret: str = ""
    """Optional Slack signing secret. Falls back to SLACK_SIGNING_SECRET."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        bot_token = self.bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        signing_secret = self.signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
        if not bot_token:
            raise UserFacingError("Missing Slack bot token.", "Pass --bot-token or set SLACK_BOT_TOKEN.")
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=SlackAdapter(
                bot_token=bot_token,
                api_base_url=self.api_base_url,
                signing_secret=signing_secret,
            ),
        )


@dataclass
class ChannelWeChatBridge:
    """WeChat/QClaw normalized bridge webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    reply_url: str = ""
    """Bridge reply endpoint. Falls back to WECHAT_BRIDGE_REPLY_URL."""

    bridge_secret: str = ""
    """Optional bearer/header secret. Falls back to WECHAT_BRIDGE_SECRET."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        reply_url = self.reply_url or os.environ.get("WECHAT_BRIDGE_REPLY_URL", "")
        bridge_secret = self.bridge_secret or os.environ.get("WECHAT_BRIDGE_SECRET", "")
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=WeChatBridgeAdapter(reply_url=reply_url, bridge_secret=bridge_secret),
        )


@dataclass
class ChannelQQBridge:
    """QQ normalized bridge webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    reply_url: str = ""
    """Bridge reply endpoint. Falls back to QQ_BRIDGE_REPLY_URL."""

    bridge_secret: str = ""
    """Optional bearer/header secret. Falls back to QQ_BRIDGE_SECRET."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        reply_url = self.reply_url or os.environ.get("QQ_BRIDGE_REPLY_URL", "")
        bridge_secret = self.bridge_secret or os.environ.get("QQ_BRIDGE_SECRET", "")
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=QQBridgeAdapter(reply_url=reply_url, bridge_secret=bridge_secret),
        )


@dataclass
class ChannelFeishu:
    """Feishu/Lark Events API webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    tenant_access_token: str = ""
    """Feishu tenant access token. Falls back to FEISHU_TENANT_ACCESS_TOKEN."""

    app_id: str = ""
    """Feishu app ID. Falls back to FEISHU_APP_ID when tenant access token is omitted."""

    app_secret: str = ""
    """Feishu app secret. Falls back to FEISHU_APP_SECRET when tenant access token is omitted."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    api_base_url: str = "https://open.feishu.cn"
    """Feishu/Lark Open API base URL."""

    verification_token: str = ""
    """Optional event subscription verification token. Falls back to FEISHU_VERIFICATION_TOKEN."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        tenant_access_token = self.tenant_access_token or os.environ.get("FEISHU_TENANT_ACCESS_TOKEN", "")
        app_id = self.app_id or os.environ.get("FEISHU_APP_ID", "")
        app_secret = self.app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        verification_token = self.verification_token or os.environ.get("FEISHU_VERIFICATION_TOKEN", "")
        if not tenant_access_token and not (app_id and app_secret):
            raise UserFacingError(
                "Missing Feishu credentials.",
                "Pass --tenant-access-token, set FEISHU_TENANT_ACCESS_TOKEN, or set FEISHU_APP_ID/FEISHU_APP_SECRET.",
            )
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=FeishuAdapter(
                tenant_access_token=tenant_access_token,
                app_id=app_id,
                app_secret=app_secret,
                api_base_url=self.api_base_url,
                verification_token=verification_token,
            ),
        )


@dataclass
class ChannelDingTalk:
    """DingTalk outgoing robot webhook channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    listen: str = "http://127.0.0.1:8080"
    """HTTP endpoint to listen on."""

    webhook_path: str = "/webhook"
    """Webhook path when listen does not include a path."""

    session_webhook: str = ""
    """Fallback DingTalk sessionWebhook. Falls back to DINGTALK_SESSION_WEBHOOK."""

    outgoing_token: str = ""
    """Optional outgoing robot token. Falls back to DINGTALK_OUTGOING_TOKEN."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        session_webhook = self.session_webhook or os.environ.get("DINGTALK_SESSION_WEBHOOK", "")
        outgoing_token = self.outgoing_token or os.environ.get("DINGTALK_OUTGOING_TOKEN", "")
        await serve_platform_channel(
            session_socket=self.session_socket,
            listen=self.listen,
            webhook_path=self.webhook_path,
            adapter=DingTalkAdapter(session_webhook=session_webhook, outgoing_token=outgoing_token),
        )


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


async def _fetch_feishu_tenant_access_token(
    *,
    session: ClientSession,
    api_base_url: str,
    app_id: str,
    app_secret: str,
) -> str:
    if not app_id or not app_secret:
        raise UserFacingError(
            "Missing Feishu tenant access token.",
            "Pass --tenant-access-token, set FEISHU_TENANT_ACCESS_TOKEN, or configure app_id/app_secret.",
        )

    url = f"{api_base_url.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal"
    async with session.post(url, json={"app_id": app_id, "app_secret": app_secret}) as response:
        body = await response.text()
        if response.status >= 400:
            raise UserFacingError(f"Feishu tenant token request failed: HTTP {response.status}: {body[:300]}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise UserFacingError(f"Feishu tenant token response is not JSON: {body[:300]}") from None
        if not isinstance(data, dict):
            raise UserFacingError("Feishu tenant token response is not an object.")
        if data.get("code") not in {None, 0}:
            raise UserFacingError(
                f"Feishu tenant token request failed: code={data.get('code')}: {data.get('msg') or body[:300]}"
            )
        token = data.get("tenant_access_token")
        if not token:
            raise UserFacingError("Feishu tenant token response did not include tenant_access_token.")
        return str(token)


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


def _iter_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            items.append(cast(dict[str, Any], item))
    return items


def _whatsapp_message_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    if isinstance(text, dict) and text.get("body"):
        return str(text["body"])

    button = message.get("button")
    if isinstance(button, dict) and button.get("text"):
        return str(button["text"])

    interactive = message.get("interactive")
    if isinstance(interactive, dict):
        for key in ("button_reply", "list_reply"):
            reply = interactive.get(key)
            if isinstance(reply, dict):
                title = reply.get("title")
                if title:
                    return str(title)
    return ""


def _discord_interaction_text(data: dict[str, Any]) -> str:
    options = data.get("options")
    if isinstance(options, list):
        values = [str(option["value"]) for option in options if isinstance(option, dict) and option.get("value")]
        if values:
            return " ".join(values)
    return str(data.get("name") or "")


def _feishu_message_text(message: dict[str, Any]) -> str:
    message_type = _first_string(message, "message_type", "messageType")
    if message_type and message_type != "text":
        return ""

    content = message.get("content")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(parsed, dict) and parsed.get("text"):
            return str(parsed["text"])
        return content
    if isinstance(content, dict) and content.get("text"):
        return str(content["text"])
    return _first_string(message, "text", "body")


def _feishu_sender_id(event: dict[str, Any]) -> str:
    raw_sender = event.get("sender")
    sender = raw_sender if isinstance(raw_sender, dict) else {}
    raw_sender_id = sender.get("sender_id")
    sender_id = raw_sender_id if isinstance(raw_sender_id, dict) else {}
    return _first_string(sender_id, "open_id", "user_id", "union_id") or _first_string(sender, "open_id", "user_id")


def _dingtalk_message_text(body: dict[str, Any]) -> str:
    raw_text = body.get("text")
    if isinstance(raw_text, dict) and raw_text.get("content"):
        return str(raw_text["content"])
    if isinstance(raw_text, str):
        return raw_text
    return _first_string(body, "content", "body")


def _first_string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return str(value)
    return ""
