from __future__ import annotations

import asyncio
import json
import os
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.channel.session_client import collect_session_reply
from psi_agent.errors import UserFacingError

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0
CHANNEL_VERSION = "2.2.0"

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

ITEM_TEXT = 1
ITEM_VOICE = 3
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
RETRY_DELAY_SECONDS = 2.0
BACKOFF_DELAY_SECONDS = 30.0
MAX_CONSECUTIVE_FAILURES = 3


@dataclass(frozen=True)
class WeixinIlinkMessage:
    """A text inbound message from Tencent iLink."""

    peer_id: str
    text: str
    sender_id: str
    message_id: str = ""
    context_token: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class WeixinIlinkState:
    """In-memory long-poll cursor and context token cache."""

    sync_buf: str = ""
    context_tokens: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WeixinIlinkClient:
    """Minimal Tencent iLink Bot API client for text-only WeChat delivery."""

    token: str
    account_id: str
    base_url: str = ILINK_BASE_URL

    async def get_updates(
        self,
        session: ClientSession,
        *,
        sync_buf: str,
        timeout_ms: int = LONG_POLL_TIMEOUT_MS,
    ) -> dict[str, Any]:
        try:
            return await _api_post(
                session,
                base_url=self.base_url,
                endpoint=EP_GET_UPDATES,
                payload={"get_updates_buf": sync_buf},
                token=self.token,
                timeout_ms=timeout_ms,
            )
        except TimeoutError:
            return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}

    async def send_text(
        self,
        session: ClientSession,
        *,
        to_user_id: str,
        text: str,
        context_token: str = "",
        client_id: str = "",
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("Weixin iLink reply text must not be empty.")

        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id or f"psi-weixin-{uuid.uuid4().hex}",
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
        if context_token:
            message["context_token"] = context_token

        return await _api_post(
            session,
            base_url=self.base_url,
            endpoint=EP_SEND_MESSAGE,
            payload={"msg": message},
            token=self.token,
            timeout_ms=API_TIMEOUT_MS,
        )


@dataclass
class ChannelWeixinIlink:
    """WeChat personal account channel using Tencent iLink long polling."""

    session_socket: str
    """Path to the session Unix domain socket."""

    token: str = ""
    """Tencent iLink bot token. Falls back to WEIXIN_TOKEN."""

    account_id: str = ""
    """Tencent iLink account ID. Falls back to WEIXIN_ACCOUNT_ID."""

    base_url: str = ""
    """Tencent iLink API base URL. Falls back to WEIXIN_BASE_URL."""

    long_poll_timeout_ms: int = LONG_POLL_TIMEOUT_MS
    """Long-poll getupdates timeout in milliseconds."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    qr: bool = False
    """Print QR login helper guidance. First version still requires token/account credentials."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        if self.qr:
            await print_weixin_ilink_qr_help()
            return

        token = self.token or os.environ.get("WEIXIN_TOKEN", "")
        account_id = self.account_id or os.environ.get("WEIXIN_ACCOUNT_ID", "")
        base_url = self.base_url or os.environ.get("WEIXIN_BASE_URL", ILINK_BASE_URL)
        if not token:
            raise UserFacingError("Missing Weixin iLink token.", "Pass --token or set WEIXIN_TOKEN.")
        if not account_id:
            raise UserFacingError("Missing Weixin iLink account ID.", "Pass --account-id or set WEIXIN_ACCOUNT_ID.")

        client = WeixinIlinkClient(token=token, account_id=account_id, base_url=base_url.rstrip("/"))
        await run_weixin_ilink_channel(
            session_socket=self.session_socket,
            client=client,
            long_poll_timeout_ms=self.long_poll_timeout_ms,
        )


async def run_weixin_ilink_channel(
    *,
    session_socket: str,
    client: WeixinIlinkClient,
    state: WeixinIlinkState | None = None,
    long_poll_timeout_ms: int = LONG_POLL_TIMEOUT_MS,
) -> None:
    state = state or WeixinIlinkState()
    timeout = ClientTimeout(total=None)
    async with ClientSession(connector=TCPConnector(ssl=True), timeout=timeout) as session:
        logger.info(f"weixin-ilink channel polling account={_safe_id(client.account_id)} base={client.base_url}")
        consecutive_failures = 0
        while True:
            try:
                await poll_weixin_ilink_once(
                    session_socket=session_socket,
                    client=client,
                    state=state,
                    session=session,
                    timeout_ms=long_poll_timeout_ms,
                )
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                logger.exception(f"weixin-ilink poll failed ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {e}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    await anyio.sleep(BACKOFF_DELAY_SECONDS)
                    consecutive_failures = 0
                else:
                    await anyio.sleep(RETRY_DELAY_SECONDS)


async def poll_weixin_ilink_once(
    *,
    session_socket: str,
    client: WeixinIlinkClient,
    state: WeixinIlinkState,
    session: ClientSession,
    timeout_ms: int = LONG_POLL_TIMEOUT_MS,
) -> list[WeixinIlinkMessage]:
    response = await client.get_updates(session, sync_buf=state.sync_buf, timeout_ms=timeout_ms)
    _raise_for_ilink_error(response, "getupdates")

    new_sync_buf = response.get("get_updates_buf")
    if isinstance(new_sync_buf, str):
        state.sync_buf = new_sync_buf

    messages = extract_weixin_ilink_messages(response, account_id=client.account_id)
    for message in messages:
        if message.context_token:
            state.context_tokens[message.peer_id] = message.context_token
        reply = await collect_session_reply(session_socket=session_socket, message=message.text)
        if reply:
            await client.send_text(
                session,
                to_user_id=message.peer_id,
                text=reply,
                context_token=state.context_tokens.get(message.peer_id, ""),
            )
    return messages


def extract_weixin_ilink_messages(response: dict[str, Any], *, account_id: str) -> list[WeixinIlinkMessage]:
    messages: list[WeixinIlinkMessage] = []
    for raw_message in _iter_dicts(response.get("msgs")):
        sender_id = _first_string(raw_message, "from_user_id", "fromUserId")
        if not sender_id or sender_id == account_id:
            continue

        item_list = raw_message.get("item_list")
        if not isinstance(item_list, list):
            continue

        text = _extract_text(cast(list[dict[str, Any]], item_list)).strip()
        if not text:
            continue

        peer_id = _peer_id(raw_message, account_id=account_id)
        if not peer_id:
            continue

        context_token = _first_string(raw_message, "context_token", "contextToken")
        messages.append(
            WeixinIlinkMessage(
                peer_id=peer_id,
                text=text,
                sender_id=sender_id,
                message_id=_first_string(raw_message, "msg_id", "msgid", "message_id", "client_id"),
                context_token=context_token,
                raw=raw_message,
            )
        )
    return messages


async def print_weixin_ilink_qr_help() -> None:
    _ = EP_GET_BOT_QR, EP_GET_QR_STATUS
    raise UserFacingError(
        "Weixin iLink QR login helper is not implemented yet.",
        "First version requires existing WEIXIN_TOKEN and WEIXIN_ACCOUNT_ID credentials.",
    )


async def _api_post(
    session: ClientSession,
    *,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str,
    timeout_ms: int,
) -> dict[str, Any]:
    body = json.dumps({**payload, "base_info": {"channel_version": CHANNEL_VERSION}}, ensure_ascii=False)
    url = f"{base_url.rstrip('/')}/{endpoint}"

    async def request() -> dict[str, Any]:
        async with session.post(url, data=body, headers=_headers(token, body)) as response:
            raw = await response.text()
            if response.status >= 400:
                raise UserFacingError(f"Weixin iLink {endpoint} failed: HTTP {response.status}: {raw[:300]}")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise UserFacingError(f"Weixin iLink {endpoint} response is not JSON: {raw[:300]}") from None
            if not isinstance(data, dict):
                raise UserFacingError(f"Weixin iLink {endpoint} response is not an object.")
            return cast(dict[str, Any], data)

    return await asyncio.wait_for(request(), timeout=timeout_ms / 1000)


def _headers(token: str, body: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _raise_for_ilink_error(response: dict[str, Any], endpoint: str) -> None:
    ret = response.get("ret")
    errcode = response.get("errcode")
    if ret not in {None, 0} or errcode not in {None, 0}:
        message = response.get("errmsg") or response.get("error") or response
        raise UserFacingError(f"Weixin iLink {endpoint} failed: {message}")


def _peer_id(message: dict[str, Any], *, account_id: str) -> str:
    room_id = _first_string(message, "room_id", "chat_room_id", "roomId", "chatRoomId")
    if room_id:
        return room_id

    to_user_id = _first_string(message, "to_user_id", "toUserId")
    if to_user_id and to_user_id != account_id and message.get("msg_type") == MSG_TYPE_USER:
        return to_user_id

    return _first_string(message, "from_user_id", "fromUserId")


def _extract_text(item_list: list[dict[str, Any]]) -> str:
    for item in item_list:
        if item.get("type") == ITEM_TEXT:
            text_item = item.get("text_item")
            if isinstance(text_item, dict):
                text = text_item.get("text")
                if text:
                    return str(text)
    for item in item_list:
        if item.get("type") == ITEM_VOICE:
            voice_item = item.get("voice_item")
            if isinstance(voice_item, dict):
                text = voice_item.get("text")
                if text:
                    return str(text)
    return ""


def _iter_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]


def _first_string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


def _safe_id(value: str, keep: int = 8) -> str:
    if len(value) <= keep:
        return value or "?"
    return value[:keep]


def _random_wechat_uin() -> str:
    return str(random.randint(100_000_000, 999_999_999))
