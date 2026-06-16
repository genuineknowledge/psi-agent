from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

import anyio
from aiohttp import ClientSession, ClientTimeout, WSMsgType
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.channel.platform import PlatformMessage
from psi_agent.channel.session_client import collect_session_reply
from psi_agent.errors import UserFacingError

QQBOT_DEFAULT_API_BASE_URL = "https://api.sgroup.qq.com"
QQBOT_DEFAULT_AUTH_BASE_URL = "https://bots.qq.com"
QQBOT_DEFAULT_GATEWAY_INTENTS = (1 << 25) | (1 << 26)
QQBOT_DISPATCH_EVENTS = {"C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"}

QQBotMessageKind = Literal["c2c", "group"]


@dataclass
class QQBotGatewayState:
    sequence: int | None = None


@dataclass
class QQBotAdapter:
    app_id: str
    client_secret: str
    api_base_url: str = QQBOT_DEFAULT_API_BASE_URL
    auth_base_url: str = QQBOT_DEFAULT_AUTH_BASE_URL
    access_token: str = ""
    access_token_expires_at: float = 0

    provider: Literal["qq"] = "qq"

    async def get_access_token(self, session: ClientSession) -> str:
        if self.access_token and self.access_token_expires_at - time.time() > 60:
            return self.access_token

        if not self.app_id or not self.client_secret:
            raise UserFacingError("Missing QQ Bot credentials.", "Set QQ_APP_ID and QQ_CLIENT_SECRET.")

        url = f"{self.auth_base_url.rstrip('/')}/app/getAppAccessToken"
        payload = {"appId": self.app_id, "clientSecret": self.client_secret}
        async with session.post(url, json=payload) as response:
            body = await response.text()
            if response.status >= 400:
                raise UserFacingError(f"QQ Bot access token request failed: HTTP {response.status}: {body[:300]}")
            data = _json_object(body, "QQ Bot access token response")

        if data.get("code") not in {None, 0}:
            raise UserFacingError(
                f"QQ Bot access token request failed: code={data.get('code')}: "
                f"{data.get('message') or data.get('msg') or body[:300]}"
            )

        token = _first_string(data, "access_token", "accessToken", "app_access_token", "appAccessToken")
        if not token:
            raise UserFacingError("QQ Bot access token response did not include access_token.")

        expires_in = _coerce_int(data.get("expires_in") or data.get("expiresIn"), default=7200)
        self.access_token = token
        self.access_token_expires_at = time.time() + max(expires_in, 1)
        return token

    def extract_messages(self, payload: dict[str, Any]) -> list[PlatformMessage]:
        event_type = str(payload.get("t") or payload.get("type") or "")
        raw_data = payload.get("d") if isinstance(payload.get("d"), dict) else payload.get("data")
        data = raw_data if isinstance(raw_data, dict) else payload

        if event_type and event_type not in QQBOT_DISPATCH_EVENTS:
            return []
        if not event_type:
            event_type = str(data.get("event_type") or data.get("eventType") or "")

        if event_type == "C2C_MESSAGE_CREATE":
            return self._extract_c2c_message(payload, cast(dict[str, Any], data))
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            return self._extract_group_message(payload, cast(dict[str, Any], data))
        return []

    def _extract_c2c_message(self, payload: dict[str, Any], data: dict[str, Any]) -> list[PlatformMessage]:
        text = _message_text(data)
        target_id = _first_string(data, "openid", "user_openid", "userOpenid", "author_id")
        raw_author = data.get("author")
        author = cast(dict[str, Any], raw_author) if isinstance(raw_author, dict) else {}
        target_id = target_id or _first_string(author, "user_openid", "userOpenid", "openid", "id")
        if not text or not target_id:
            return []

        metadata = _message_metadata(payload, data, kind="c2c")
        return [
            PlatformMessage(
                provider="qq",
                target_id=target_id,
                user_id=target_id,
                text=text,
                metadata=metadata,
            )
        ]

    def _extract_group_message(self, payload: dict[str, Any], data: dict[str, Any]) -> list[PlatformMessage]:
        text = _message_text(data)
        target_id = _first_string(data, "group_openid", "groupOpenid", "group_id", "groupId")
        raw_author = data.get("author")
        author = cast(dict[str, Any], raw_author) if isinstance(raw_author, dict) else {}
        user_id = _first_string(data, "openid", "user_openid", "member_openid", "memberOpenid")
        user_id = user_id or _first_string(author, "member_openid", "memberOpenid", "user_openid", "openid", "id")
        if not text or not target_id:
            return []

        metadata = _message_metadata(payload, data, kind="group")
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
        access_token = await self.get_access_token(session)
        message_kind = cast(QQBotMessageKind, message.metadata.get("message_kind") or "c2c")
        if message_kind == "group":
            url = f"{self.api_base_url.rstrip('/')}/v2/groups/{message.target_id}/messages"
        else:
            url = f"{self.api_base_url.rstrip('/')}/v2/users/{message.target_id}/messages"

        payload: dict[str, Any] = {
            "content": text,
            "msg_type": 0,
            "msg_seq": _coerce_int(message.metadata.get("msg_seq"), default=1),
        }
        message_id = message.metadata.get("message_id")
        if message_id:
            payload["msg_id"] = message_id

        await _post_qqbot_json(session, url, payload, access_token=access_token)


@dataclass
class ChannelQQBot:
    """Native QQ Bot Gateway channel."""

    session_socket: str
    """Path to the session Unix domain socket."""

    app_id: str = ""
    """QQ Bot app ID. Falls back to QQ_APP_ID."""

    client_secret: str = ""
    """QQ Bot client secret. Falls back to QQ_CLIENT_SECRET."""

    api_base_url: str = QQBOT_DEFAULT_API_BASE_URL
    """QQ Bot OpenAPI base URL."""

    auth_base_url: str = QQBOT_DEFAULT_AUTH_BASE_URL
    """QQ Bot credential API base URL."""

    gateway_url: str = ""
    """QQ Bot Gateway WebSocket URL. If omitted, fetched from the API."""

    gateway_intents: int = QQBOT_DEFAULT_GATEWAY_INTENTS
    """QQ Bot Gateway intents bitfield."""

    reconnect_delay: float = 5
    """Seconds to wait before reconnecting after a Gateway disconnect."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        app_id = self.app_id or os.environ.get("QQ_APP_ID", "")
        client_secret = self.client_secret or os.environ.get("QQ_CLIENT_SECRET", "")
        if not app_id or not client_secret:
            raise UserFacingError(
                "Missing QQ Bot credentials.",
                "Pass --app-id/--client-secret or set QQ_APP_ID and QQ_CLIENT_SECRET.",
            )

        await serve_qqbot_gateway_channel(
            session_socket=self.session_socket,
            app_id=app_id,
            client_secret=client_secret,
            api_base_url=self.api_base_url,
            auth_base_url=self.auth_base_url,
            gateway_url=self.gateway_url,
            gateway_intents=self.gateway_intents,
            reconnect_delay=self.reconnect_delay,
        )


async def serve_qqbot_gateway_channel(
    *,
    session_socket: str,
    app_id: str,
    client_secret: str,
    api_base_url: str = QQBOT_DEFAULT_API_BASE_URL,
    auth_base_url: str = QQBOT_DEFAULT_AUTH_BASE_URL,
    gateway_url: str = "",
    gateway_intents: int = QQBOT_DEFAULT_GATEWAY_INTENTS,
    reconnect_delay: float = 5,
) -> None:
    adapter = QQBotAdapter(
        app_id=app_id,
        client_secret=client_secret,
        api_base_url=api_base_url,
        auth_base_url=auth_base_url,
    )
    timeout = ClientTimeout(total=None)

    async with ClientSession(timeout=timeout) as session:
        while True:
            try:
                access_token = await adapter.get_access_token(session)
                resolved_gateway_url = gateway_url or await fetch_qqbot_gateway_url(
                    session=session,
                    api_base_url=api_base_url,
                    access_token=access_token,
                )
                logger.info(f"Connecting to QQ Bot Gateway at {resolved_gateway_url}")
                async with session.ws_connect(resolved_gateway_url) as ws:
                    await _run_qqbot_gateway_connection(
                        ws=ws,
                        session=session,
                        session_socket=session_socket,
                        adapter=adapter,
                        gateway_intents=gateway_intents,
                    )
            except Exception as e:
                logger.error(f"QQ Bot Gateway connection error: {e}")
                await anyio.sleep(reconnect_delay)


async def fetch_qqbot_gateway_url(*, session: ClientSession, api_base_url: str, access_token: str) -> str:
    url = f"{api_base_url.rstrip('/')}/gateway"
    async with session.get(url, headers=_qqbot_auth_headers(access_token)) as response:
        body = await response.text()
        if response.status >= 400:
            raise UserFacingError(f"QQ Bot Gateway URL request failed: HTTP {response.status}: {body[:300]}")
        data = _json_object(body, "QQ Bot Gateway URL response")

    gateway_url = _first_string(data, "url", "gateway_url", "gatewayUrl")
    if not gateway_url:
        raise UserFacingError("QQ Bot Gateway URL response did not include url.")
    return gateway_url


async def _run_qqbot_gateway_connection(
    *,
    ws,
    session: ClientSession,
    session_socket: str,
    adapter: QQBotAdapter,
    gateway_intents: int,
) -> None:
    state = QQBotGatewayState()

    async with anyio.create_task_group() as tg:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                payload = json.loads(msg.data)
                if not isinstance(payload, dict):
                    continue

                sequence = payload.get("s")
                if isinstance(sequence, int):
                    state.sequence = sequence

                op = payload.get("op")
                if op == 10:
                    interval_ms = _qqbot_heartbeat_interval(cast(dict[str, Any], payload))
                    await _qqbot_identify(ws, adapter=adapter, session=session, gateway_intents=gateway_intents)
                    tg.start_soon(_qqbot_heartbeat_loop, ws, state, interval_ms / 1000)
                    continue
                if op == 0 and payload.get("t") in QQBOT_DISPATCH_EVENTS:
                    await _handle_qqbot_gateway_message(
                        session=session,
                        session_socket=session_socket,
                        adapter=adapter,
                        payload=cast(dict[str, Any], payload),
                    )
                    continue
                if op in {7, 9}:
                    logger.warning(f"QQ Bot Gateway requested reconnect, op={op}")
                    tg.cancel_scope.cancel()
                    return
            elif msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                tg.cancel_scope.cancel()
                return


async def _qqbot_identify(
    ws,
    *,
    adapter: QQBotAdapter,
    session: ClientSession,
    gateway_intents: int,
) -> None:
    access_token = await adapter.get_access_token(session)
    await ws.send_json(
        {
            "op": 2,
            "d": {
                "token": f"QQBot {access_token}",
                "intents": gateway_intents,
                "shard": [0, 1],
                "properties": {
                    "os": os.name,
                    "browser": "psi-agent",
                    "device": "psi-agent",
                },
            },
        }
    )


async def _qqbot_heartbeat_loop(ws, state: QQBotGatewayState, interval_seconds: float) -> None:
    while True:
        await anyio.sleep(interval_seconds)
        await ws.send_json({"op": 1, "d": state.sequence})


async def _handle_qqbot_gateway_message(
    *,
    session: ClientSession,
    session_socket: str,
    adapter: QQBotAdapter,
    payload: dict[str, Any],
) -> None:
    for message in adapter.extract_messages(payload):
        reply = await collect_session_reply(session_socket=session_socket, message=message.text)
        if reply:
            await adapter.send_reply(session, message, reply)


def _qqbot_heartbeat_interval(payload: dict[str, Any]) -> int:
    data = payload.get("d")
    if not isinstance(data, dict):
        raise UserFacingError("QQ Bot Gateway hello payload is missing heartbeat metadata.")
    interval = data.get("heartbeat_interval") or data.get("heartbeatInterval")
    if not isinstance(interval, int):
        raise UserFacingError("QQ Bot Gateway hello payload is missing heartbeat_interval.")
    return interval


async def _post_qqbot_json(
    session: ClientSession,
    url: str,
    payload: dict[str, Any],
    *,
    access_token: str,
) -> None:
    async with session.post(url, json=payload, headers=_qqbot_auth_headers(access_token)) as response:
        body = await response.text()
        if response.status >= 400:
            raise UserFacingError(f"QQ Bot API request failed: HTTP {response.status}: {body[:300]}")
        if not body:
            return
        data = _json_object(body, "QQ Bot API response")
        if data.get("code") not in {None, 0}:
            error = data.get("message") or data.get("msg") or body[:300]
            raise UserFacingError(f"QQ Bot API request failed: code={data.get('code')}: {error}")
        logger.info("QQ Bot API request succeeded")


def _qqbot_auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"QQBot {access_token}"}


def _message_text(data: dict[str, Any]) -> str:
    raw_text = data.get("content") or data.get("text")
    if isinstance(raw_text, str):
        return raw_text.strip()
    if isinstance(raw_text, dict):
        return _first_string(raw_text, "text", "content").strip()
    return ""


def _message_metadata(payload: dict[str, Any], data: dict[str, Any], *, kind: QQBotMessageKind) -> dict[str, str]:
    message_id = _first_string(data, "id", "msg_id", "msgId", "message_id", "messageId")
    event_id = _first_string(payload, "id", "event_id", "eventId") or _first_string(data, "event_id", "eventId")
    msg_seq = _first_string(data, "msg_seq", "msgSeq", "seq")
    metadata = {
        "event_id": event_id,
        "message_id": message_id,
        "message_kind": kind,
        "msg_seq": msg_seq,
    }
    return {key: value for key, value in metadata.items() if value}


def _json_object(body: str, label: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise UserFacingError(f"{label} is not JSON: {body[:300]}") from None
    if not isinstance(data, dict):
        raise UserFacingError(f"{label} is not an object.")
    return cast(dict[str, Any], data)


def _first_string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


def _coerce_int(value: object, *, default: int) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default
