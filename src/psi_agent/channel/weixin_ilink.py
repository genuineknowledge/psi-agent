from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import random
import re
import secrets
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.channel.session_client import collect_session_reply
from psi_agent.errors import UserFacingError

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0
CHANNEL_VERSION = "2.2.0"

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

ITEM_TEXT = 1
ITEM_VOICE = 3
ITEM_FILE = 4
MEDIA_TYPE_FILE = 3
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
RETRY_DELAY_SECONDS = 2.0
BACKOFF_DELAY_SECONDS = 30.0
MAX_CONSECUTIVE_FAILURES = 3
QR_LOGIN_TIMEOUT_SECONDS = 180.0
QR_LOGIN_POLL_INTERVAL_SECONDS = 2.0
QR_LOGIN_MAX_REFRESHES = 3
QR_BOT_TYPE = 3
DEFAULT_STATE_DIR = "~/.psi-agent/channels/weixin-ilink"

QR_WAIT_STATUSES = {"wait", "scaned", "scanned"}
QR_REDIRECT_STATUSES = {"scaned_but_redirect"}
QR_SUCCESS_STATUSES = {"confirmed", "success", "connected"}
QR_NOOP_SUCCESS_STATUSES = {"binded_redirect"}
QR_REFRESH_STATUSES = {"expired", "verify_code_blocked"}
QR_VERIFY_STATUSES = {"need_verifycode", "need_verify_code"}
DEFAULT_FILE_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".txt",
    ".xls",
    ".xlsx",
    ".zip",
}
MEDIA_LINE_RE = re.compile(r"^\s*MEDIA:\s*(?P<path>.+?)\s*$")


@dataclass(frozen=True)
class WeixinIlinkMessage:
    """A text inbound message from Tencent iLink."""

    peer_id: str
    text: str
    sender_id: str
    message_id: str = ""
    context_token: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeixinReplyMedia:
    """A local file attachment requested by an agent reply MEDIA marker."""

    path: Path
    display_name: str


@dataclass(frozen=True)
class WeixinInboundMedia:
    """A file-like item received from Tencent iLink."""

    file_name: str
    size: int = 0
    md5: str = ""
    download_url: str = ""
    encrypt_query_param: str = ""
    aes_key: str = ""
    encrypt_type: int = 0
    kind: str = "file"


@dataclass(frozen=True)
class WeixinUploadedMedia:
    """Encrypted CDN upload descriptor used by iLink file_item media."""

    encrypt_query_param: str
    aes_key: str
    encrypt_type: int
    raw_size: int
    md5: str
    display_name: str


@dataclass
class WeixinIlinkState:
    """In-memory long-poll cursor and context token cache."""

    sync_buf: str = ""
    context_tokens: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WeixinIlinkCredentials:
    """Persisted Tencent iLink bot login credentials."""

    token: str
    account_id: str
    base_url: str = ILINK_BASE_URL
    user_id: str = ""
    storage_id: str = ""
    saved_at: str = ""

    def client(self) -> WeixinIlinkClient:
        return WeixinIlinkClient(
            token=self.token,
            account_id=self.account_id,
            base_url=(self.base_url or ILINK_BASE_URL).rstrip("/"),
        )


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

        return await self.send_items(
            session,
            to_user_id=to_user_id,
            items=[{"type": ITEM_TEXT, "text_item": {"text": text}}],
            context_token=context_token,
            client_id=client_id,
        )

    async def send_file(
        self,
        session: ClientSession,
        *,
        to_user_id: str,
        path: Path,
        context_token: str = "",
        client_id: str = "",
    ) -> dict[str, Any]:
        upload = await self.upload_file(session, to_user_id=to_user_id, path=path)
        return await self.send_items(
            session,
            to_user_id=to_user_id,
            items=[
                {
                    "type": ITEM_FILE,
                    "file_item": {
                        "file_name": upload.display_name,
                        "len": str(upload.raw_size),
                        "media": {
                            "encrypt_query_param": upload.encrypt_query_param,
                            "aes_key": upload.aes_key,
                            "encrypt_type": upload.encrypt_type,
                        },
                    },
                }
            ],
            context_token=context_token,
            client_id=client_id,
        )

    async def send_items(
        self,
        session: ClientSession,
        *,
        to_user_id: str,
        items: list[dict[str, Any]],
        context_token: str = "",
        client_id: str = "",
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id or f"psi-weixin-{uuid.uuid4().hex}",
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": items,
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

    async def upload_file(
        self,
        session: ClientSession,
        *,
        to_user_id: str,
        path: Path,
    ) -> WeixinUploadedMedia:
        raw = await anyio.Path(path).read_bytes()
        aes_key = secrets.token_bytes(16)
        aes_key_hex = aes_key.hex()
        encrypted = _aes_128_ecb_encrypt(raw, aes_key)
        file_md5 = hashlib.md5(raw).hexdigest()
        display_name, upload_key, upload_url_response = await self._get_upload_url_for_file(
            session,
            to_user_id=to_user_id,
            original_name=path.name,
            raw_size=len(raw),
            encrypted_size=len(encrypted),
            file_md5=file_md5,
            aes_key=aes_key_hex,
        )
        upload_url = _weixin_cdn_upload_url(upload_url_response, filekey=upload_key)

        upload_response = await _upload_weixin_encrypted_file(session, upload_url=upload_url, payload=encrypted)
        encrypt_query_param = _weixin_encrypt_query_param(
            upload_response=upload_response,
        )
        if not encrypt_query_param:
            raise UserFacingError("Weixin iLink file upload response did not include x-encrypted-param.")

        return WeixinUploadedMedia(
            encrypt_query_param=encrypt_query_param,
            aes_key=base64.b64encode(aes_key_hex.encode("ascii")).decode("ascii"),
            encrypt_type=1,
            raw_size=len(raw),
            md5=file_md5,
            display_name=display_name,
        )

    async def _get_upload_url_for_file(
        self,
        session: ClientSession,
        *,
        to_user_id: str,
        original_name: str,
        raw_size: int,
        encrypted_size: int,
        file_md5: str,
        aes_key: str,
    ) -> tuple[str, str, dict[str, Any]]:
        last_response: dict[str, Any] = {}
        for display_name in _weixin_upload_file_name_candidates(original_name):
            upload_key = secrets.token_hex(16)
            response = await _api_post(
                session,
                base_url=self.base_url,
                endpoint=EP_GET_UPLOAD_URL,
                payload={
                    "filekey": upload_key,
                    "rawsize": raw_size,
                    "rawfilemd5": file_md5,
                    "filesize": encrypted_size,
                    "aeskey": aes_key,
                    "media_type": MEDIA_TYPE_FILE,
                    "no_need_thumb": True,
                    "to_user_id": to_user_id,
                },
                token=self.token,
                timeout_ms=API_TIMEOUT_MS,
            )
            last_response = response
            if response.get("ret") in {None, 0} and response.get("errcode") in {None, 0}:
                return display_name, upload_key, response
            if not _should_retry_weixin_upload_name(response, original_name=original_name, display_name=display_name):
                _raise_for_ilink_error(response, "getuploadurl")

        _raise_for_ilink_error(last_response, "getuploadurl")
        raise UserFacingError("Weixin iLink getuploadurl failed.")


async def request_weixin_ilink_qrcode(
    session: ClientSession,
    *,
    base_url: str = ILINK_BASE_URL,
    local_tokens: list[str] | None = None,
    timeout_ms: int = API_TIMEOUT_MS,
) -> dict[str, Any]:
    endpoint = f"{EP_GET_BOT_QR}?{urlencode({'bot_type': QR_BOT_TYPE})}"
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=endpoint,
        payload={"local_token_list": local_tokens or []},
        token="",
        timeout_ms=timeout_ms,
    )


async def get_weixin_ilink_qrcode_status(
    session: ClientSession,
    *,
    base_url: str,
    qrcode: str,
    verify_code: str = "",
    timeout_ms: int = API_TIMEOUT_MS,
) -> dict[str, Any]:
    params = {"qrcode": qrcode}
    if verify_code:
        params["verify_code"] = verify_code
    try:
        return await _api_get(
            session,
            base_url=base_url,
            endpoint=f"{EP_GET_QR_STATUS}?{urlencode(params)}",
            token="",
            timeout_ms=timeout_ms,
        )
    except TimeoutError:
        return {"ret": 0, "status": "wait"}


@dataclass
class ChannelWeixinIlink:
    """WeChat personal account channel using Tencent iLink long polling."""

    session_socket: str = ""
    """Path to the session Unix domain socket."""

    token: str = ""
    """Tencent iLink bot token. Falls back to WEIXIN_TOKEN."""

    account_id: str = ""
    """Tencent iLink account ID. Falls back to WEIXIN_ACCOUNT_ID."""

    base_url: str = ""
    """Tencent iLink API base URL. Falls back to WEIXIN_BASE_URL."""

    state_dir: str = ""
    """Directory for QR-login account state. Falls back to WEIXIN_STATE_DIR or ~/.psi-agent/channels/weixin-ilink."""

    long_poll_timeout_ms: int = LONG_POLL_TIMEOUT_MS
    """Long-poll getupdates timeout in milliseconds."""

    login_timeout_seconds: float = QR_LOGIN_TIMEOUT_SECONDS
    """Seconds to wait for QR login confirmation."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    qr: bool = False
    """Start QR login and save credentials for future channel runs."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        base_url = (self.base_url or os.environ.get("WEIXIN_BASE_URL", ILINK_BASE_URL)).rstrip("/")
        state_dir = resolve_weixin_ilink_state_dir(self.state_dir)
        if self.qr:
            credentials = await login_weixin_ilink_by_qr(
                state_dir=state_dir,
                base_url=base_url,
                timeout_seconds=self.login_timeout_seconds,
            )
            sys.stdout.write(
                f"Weixin iLink login saved: account={_safe_id(credentials.account_id)} state={state_dir}\n"
            )
            return

        credentials = resolve_weixin_ilink_credentials(
            token=self.token,
            account_id=self.account_id,
            base_url=base_url,
            state_dir=state_dir,
        )
        if not self.session_socket:
            raise UserFacingError("Missing session socket.", "Pass --session-socket for the Weixin iLink channel.")

        await run_weixin_ilink_channel(
            session_socket=self.session_socket,
            client=credentials.client(),
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

    messages = await extract_weixin_ilink_messages_with_media(
        response,
        account_id=client.account_id,
        session=session,
        base_url=client.base_url,
        token=client.token,
    )
    if messages:
        logger.info(f"weixin-ilink received messages: count={len(messages)}")
    for message in messages:
        logger.info(
            "weixin-ilink message received: "
            f"peer={_safe_id(message.peer_id)} sender={_safe_id(message.sender_id)} "
            f"message={_safe_id(message.message_id)} text_len={len(message.text)}"
        )
        if message.context_token:
            state.context_tokens[message.peer_id] = message.context_token
        reply = await collect_session_reply(session_socket=session_socket, message=message.text)
        if reply:
            await send_weixin_ilink_reply(
                session=session,
                client=client,
                to_user_id=message.peer_id,
                reply=reply,
                context_token=state.context_tokens.get(message.peer_id, ""),
            )
            logger.info(
                "weixin-ilink reply sent: "
                f"peer={_safe_id(message.peer_id)} message={_safe_id(message.message_id)} "
                f"reply_len={len(reply)}"
            )
    return messages


async def send_weixin_ilink_reply(
    *,
    session: ClientSession,
    client: WeixinIlinkClient,
    to_user_id: str,
    reply: str,
    context_token: str = "",
) -> None:
    text, media = extract_weixin_reply_media(reply)
    if text.strip():
        send_response = await client.send_text(
            session,
            to_user_id=to_user_id,
            text=text,
            context_token=context_token,
        )
        _raise_for_ilink_error(send_response, "sendmessage")

    for attachment in media:
        send_response = await client.send_file(
            session,
            to_user_id=to_user_id,
            path=attachment.path,
            context_token=context_token,
        )
        _raise_for_ilink_error(send_response, "sendmessage")


def extract_weixin_reply_media(reply: str, *, roots: list[Path] | None = None) -> tuple[str, list[WeixinReplyMedia]]:
    text_lines: list[str] = []
    media: list[WeixinReplyMedia] = []
    allowed_roots = roots or _default_weixin_media_roots()
    allowed_extensions = _allowed_weixin_file_extensions()

    for line in reply.splitlines():
        match = MEDIA_LINE_RE.match(line)
        if match is None:
            text_lines.append(line)
            continue

        raw_path = match.group("path").strip().strip('"')
        try:
            path = _resolve_weixin_media_path(raw_path, roots=allowed_roots)
            if path.suffix.lower() not in allowed_extensions:
                text_lines.append(f"Weixin cannot send this file type: {path.suffix.lower()} ({path.name})")
                continue
            media.append(WeixinReplyMedia(path=path, display_name=path.name))
        except UserFacingError as exc:
            text_lines.append(str(exc))

    return "\n".join(line for line in text_lines if line.strip()).strip(), media


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


async def extract_weixin_ilink_messages_with_media(
    response: dict[str, Any],
    *,
    account_id: str,
    session: ClientSession,
    base_url: str,
    token: str,
) -> list[WeixinIlinkMessage]:
    messages: list[WeixinIlinkMessage] = []
    for raw_message in _iter_dicts(response.get("msgs")):
        sender_id = _first_string(raw_message, "from_user_id", "fromUserId")
        if not sender_id or sender_id == account_id:
            continue

        item_list = raw_message.get("item_list")
        if not isinstance(item_list, list):
            continue

        peer_id = _peer_id(raw_message, account_id=account_id)
        if not peer_id:
            continue

        message_id = _first_string(raw_message, "msg_id", "msgid", "message_id", "client_id")
        text_parts: list[str] = []
        text = _extract_text(cast(list[dict[str, Any]], item_list)).strip()
        if text:
            text_parts.append(text)

        for media in _extract_weixin_inbound_media(cast(list[dict[str, Any]], item_list)):
            try:
                path = await _download_weixin_inbound_media(
                    session,
                    media=media,
                    peer_id=peer_id,
                    message_id=message_id or "message",
                    base_url=base_url,
                    token=token,
                )
                text_parts.append(_format_weixin_inbound_media(path=path, media=media))
            except Exception as exc:
                logger.warning(f"weixin-ilink inbound file download failed: {exc}")
                text_parts.append(_format_weixin_inbound_media_failure(media=media, error=str(exc)))

        message_text = "\n".join(part for part in text_parts if part.strip()).strip()
        if not message_text:
            continue

        context_token = _first_string(raw_message, "context_token", "contextToken")
        messages.append(
            WeixinIlinkMessage(
                peer_id=peer_id,
                text=message_text,
                sender_id=sender_id,
                message_id=message_id,
                context_token=context_token,
                raw=raw_message,
            )
        )
    return messages


def resolve_weixin_ilink_credentials(
    *,
    token: str = "",
    account_id: str = "",
    base_url: str = ILINK_BASE_URL,
    state_dir: Path | None = None,
) -> WeixinIlinkCredentials:
    env_token = os.environ.get("WEIXIN_TOKEN", "")
    env_account_id = os.environ.get("WEIXIN_ACCOUNT_ID", "")
    resolved_token = token or env_token
    resolved_account_id = account_id or env_account_id
    if resolved_token and resolved_account_id:
        return WeixinIlinkCredentials(
            token=resolved_token,
            account_id=resolved_account_id,
            base_url=base_url,
            storage_id=normalize_weixin_ilink_account_id(resolved_account_id),
        )

    credentials = load_weixin_ilink_credentials(state_dir or resolve_weixin_ilink_state_dir(""))
    if credentials is not None:
        return credentials

    raise UserFacingError(
        "Missing Weixin iLink credentials.",
        "Run `psi-agent channel weixin-ilink --qr --session-socket <socket>` to scan login, "
        "or pass --token/--account-id.",
    )


async def login_weixin_ilink_by_qr(
    *,
    state_dir: Path,
    base_url: str = ILINK_BASE_URL,
    timeout_seconds: float = QR_LOGIN_TIMEOUT_SECONDS,
    poll_interval_seconds: float = QR_LOGIN_POLL_INTERVAL_SECONDS,
) -> WeixinIlinkCredentials:
    await anyio.Path(state_dir).mkdir(parents=True, exist_ok=True)
    timeout = ClientTimeout(total=None)
    async with ClientSession(connector=TCPConnector(ssl=base_url.startswith("https://")), timeout=timeout) as session:
        local_tokens = recent_weixin_ilink_tokens(state_dir)
        current_base_url = base_url.rstrip("/")
        qrcode = ""
        refresh_count = 0
        deadline = anyio.current_time() + timeout_seconds

        while True:
            if not qrcode:
                qr_response = await request_weixin_ilink_qrcode(
                    session,
                    base_url=current_base_url,
                    local_tokens=local_tokens,
                )
                _raise_for_ilink_error(qr_response, "get_bot_qrcode")
                qrcode = _first_string(qr_response, "qrcode", "qrCode", "qr_code")
                qrcode_url = _first_string(
                    qr_response,
                    "qrcode_img_content",
                    "qrcodeImgContent",
                    "qrcode_url",
                    "qrDataUrl",
                    "qrcodeUrl",
                )
                if not qrcode or not qrcode_url:
                    raise UserFacingError(f"Weixin iLink QR response missing qrcode fields: {_redact(qr_response)}")
                sys.stdout.write("Scan this Weixin QR code URL with WeChat:\n")
                sys.stdout.write(f"{qrcode_url}\n")
                sys.stdout.flush()

            if anyio.current_time() >= deadline:
                raise UserFacingError("Weixin iLink QR login timed out.", "Run the --qr command again.")

            status = await get_weixin_ilink_qrcode_status(
                session,
                base_url=current_base_url,
                qrcode=qrcode,
            )
            _raise_for_ilink_error(status, "get_qrcode_status")
            status_name = str(status.get("status") or "").lower()

            if status_name in QR_SUCCESS_STATUSES:
                credentials = credentials_from_weixin_ilink_status(status, base_url=current_base_url)
                save_weixin_ilink_credentials(state_dir, credentials)
                return credentials
            if status_name in QR_NOOP_SUCCESS_STATUSES:
                existing = load_weixin_ilink_credentials(state_dir)
                if existing is None:
                    raise UserFacingError(
                        "Weixin iLink reported an existing binding but no local account state was found.",
                        "Remove the stale remote binding or scan with a fresh account.",
                    )
                return existing
            if status_name in QR_REDIRECT_STATUSES:
                redirect_host = _first_string(status, "redirect_host", "redirectHost")
                if redirect_host:
                    current_base_url = _normalize_weixin_redirect_base_url(redirect_host)
                    logger.info(f"Weixin iLink QR login redirected to {current_base_url}")
                await anyio.sleep(poll_interval_seconds)
                continue
            if status_name in QR_REFRESH_STATUSES:
                refresh_count += 1
                if refresh_count > QR_LOGIN_MAX_REFRESHES:
                    raise UserFacingError(
                        "Weixin iLink QR login expired too many times.",
                        "Run the --qr command again.",
                    )
                qrcode = ""
                continue
            if status_name in QR_VERIFY_STATUSES:
                verify_code = (await asyncio.to_thread(input, "Enter WeChat verification code: ")).strip()
                verify_status = await get_weixin_ilink_qrcode_status(
                    session,
                    base_url=current_base_url,
                    qrcode=qrcode,
                    verify_code=verify_code,
                )
                _raise_for_ilink_error(verify_status, "get_qrcode_status")
                if str(verify_status.get("status") or "").lower() in QR_SUCCESS_STATUSES:
                    credentials = credentials_from_weixin_ilink_status(verify_status, base_url=current_base_url)
                    save_weixin_ilink_credentials(state_dir, credentials)
                    return credentials
                await anyio.sleep(poll_interval_seconds)
                continue

            if status_name and status_name not in QR_WAIT_STATUSES:
                logger.info(f"Weixin iLink QR login status ignored: {status_name}")
            await anyio.sleep(poll_interval_seconds)


def credentials_from_weixin_ilink_status(status: dict[str, Any], *, base_url: str) -> WeixinIlinkCredentials:
    token = _first_string(status, "bot_token", "botToken", "token")
    account_id = _first_string(status, "ilink_bot_id", "ilinkBotId", "account_id", "accountId")
    if not token:
        raise UserFacingError(f"Weixin iLink QR login did not return bot_token: {_redact(status)}")
    if not account_id:
        raise UserFacingError(f"Weixin iLink QR login did not return ilink_bot_id: {_redact(status)}")

    status_base_url = _first_string(status, "baseurl", "baseUrl", "base_url") or base_url
    user_id = _first_string(status, "ilink_user_id", "ilinkUserId", "user_id", "userId")
    return WeixinIlinkCredentials(
        token=token,
        account_id=account_id,
        base_url=status_base_url.rstrip("/"),
        user_id=user_id,
        storage_id=normalize_weixin_ilink_account_id(account_id),
        saved_at=dt.datetime.now(dt.UTC).isoformat(),
    )


def resolve_weixin_ilink_state_dir(raw: str) -> Path:
    state_dir = raw or os.environ.get("WEIXIN_STATE_DIR", "") or os.environ.get("OPENCLAW_STATE_DIR", "")
    if state_dir:
        return Path(state_dir).expanduser().resolve()
    return Path(DEFAULT_STATE_DIR).expanduser().resolve()


def load_weixin_ilink_credentials(state_dir: Path) -> WeixinIlinkCredentials | None:
    account_dir = state_dir / "accounts"
    accounts = _read_json_object(state_dir / "accounts.json")
    candidates: list[str] = []
    if accounts is not None:
        raw_accounts = accounts.get("accounts")
        if isinstance(raw_accounts, list):
            for item in raw_accounts:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    account_id = _first_string(item, "account_id", "accountId", "id")
                    if account_id:
                        candidates.append(account_id)

    if account_dir.exists():
        candidates.extend(
            path.stem for path in sorted(account_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        )

    seen: set[str] = set()
    for candidate in candidates:
        storage_id = normalize_weixin_ilink_account_id(candidate)
        if storage_id in seen:
            continue
        seen.add(storage_id)
        credentials = _load_weixin_ilink_account_file(account_dir / f"{storage_id}.json", storage_id=storage_id)
        if credentials is not None:
            return credentials

    return _load_openclaw_legacy_credentials(state_dir)


def save_weixin_ilink_credentials(state_dir: Path, credentials: WeixinIlinkCredentials) -> None:
    storage_id = credentials.storage_id or normalize_weixin_ilink_account_id(credentials.account_id)
    account_dir = state_dir / "accounts"
    account_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": credentials.token,
        "accountId": credentials.account_id,
        "savedAt": credentials.saved_at or dt.datetime.now(dt.UTC).isoformat(),
        "baseUrl": credentials.base_url,
        "userId": credentials.user_id,
    }
    (account_dir / f"{storage_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    accounts_path = state_dir / "accounts.json"
    accounts = _read_json_object(accounts_path) or {}
    raw_accounts = accounts.get("accounts")
    existing = [item for item in raw_accounts if isinstance(item, str)] if isinstance(raw_accounts, list) else []
    ordered = [storage_id, *[item for item in existing if normalize_weixin_ilink_account_id(item) != storage_id]]
    accounts_path.write_text(json.dumps({"accounts": ordered[:10]}, ensure_ascii=False, indent=2), encoding="utf-8")


def recent_weixin_ilink_tokens(state_dir: Path, limit: int = 10) -> list[str]:
    tokens: list[str] = []
    account_dir = state_dir / "accounts"
    if account_dir.exists():
        for path in sorted(account_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = _read_json_object(path)
            if data is None:
                continue
            token = _first_string(data, "token", "bot_token", "botToken")
            if token:
                tokens.append(token)
            if len(tokens) >= limit:
                break
    return tokens


def normalize_weixin_ilink_account_id(account_id: str) -> str:
    normalized = account_id.strip().replace("@", "-").replace(".", "-")
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized)
    return normalized.strip("-") or "default"


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


async def _upload_weixin_encrypted_file(
    session: ClientSession,
    *,
    upload_url: str,
    payload: bytes,
) -> dict[str, Any]:
    async def request() -> dict[str, Any]:
        async with session.post(
            upload_url, data=payload, headers={"Content-Type": "application/octet-stream"}
        ) as response:
            raw = await response.text()
            if response.status >= 400:
                raise UserFacingError(f"Weixin iLink file upload failed: HTTP {response.status}: {raw[:300]}")
            result: dict[str, Any] = {}
            encrypted_param = response.headers.get("x-encrypted-param")
            if encrypted_param:
                result["encrypt_query_param"] = encrypted_param
            if not raw.strip():
                return result
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return result
            if isinstance(data, dict):
                result.update(cast(dict[str, Any], data))
            return result

    return await asyncio.wait_for(request(), timeout=API_TIMEOUT_MS / 1000)


def _aes_128_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    padding = 16 - (len(data) % 16)
    padded = data + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


async def _api_get(
    session: ClientSession,
    *,
    base_url: str,
    endpoint: str,
    token: str,
    timeout_ms: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    async def request() -> dict[str, Any]:
        async with session.get(url, headers=_headers(token, "")) as response:
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


def _extract_weixin_inbound_media(item_list: list[dict[str, Any]]) -> list[WeixinInboundMedia]:
    media: list[WeixinInboundMedia] = []
    for item in item_list:
        file_item = item.get("file_item")
        image_item = item.get("image_item") or item.get("imageItem")
        raw_media = file_item if isinstance(file_item, dict) else image_item if isinstance(image_item, dict) else None
        if raw_media is None:
            continue

        file_name = _first_string(
            raw_media,
            "file_name",
            "fileName",
            "name",
            "title",
        )
        download_url = _first_string(
            raw_media,
            "download_url",
            "downloadUrl",
            "file_url",
            "fileUrl",
            "url",
        )
        nested_media = raw_media.get("media")
        nested = nested_media if isinstance(nested_media, dict) else {}
        if not download_url:
            download_url = _first_string(
                nested,
                "download_url",
                "downloadUrl",
                "file_url",
                "fileUrl",
                "url",
                "full_url",
                "fullUrl",
            )
        if not file_name:
            file_name = _file_name_from_url(download_url) or ("image.jpg" if raw_media is image_item else "file")

        raw_size = raw_media.get("len", raw_media.get("size", raw_media.get("file_size", raw_media.get("fileSize", 0))))
        try:
            size = int(raw_size)
        except TypeError, ValueError:
            size = 0
        raw_encrypt_type = nested.get("encrypt_type", nested.get("encryptType", raw_media.get("encrypt_type", 0)))
        try:
            encrypt_type = int(raw_encrypt_type)
        except TypeError, ValueError:
            encrypt_type = 0

        media.append(
            WeixinInboundMedia(
                file_name=_sanitize_weixin_file_name(file_name),
                size=size,
                md5=_first_string(raw_media, "md5", "file_md5", "fileMd5"),
                download_url=download_url,
                encrypt_query_param=_first_string(nested, "encrypt_query_param", "encryptQueryParam"),
                aes_key=_first_string(nested, "aes_key", "aesKey", "key"),
                encrypt_type=encrypt_type,
                kind="image" if raw_media is image_item else "file",
            )
        )
    return media


async def _download_weixin_inbound_media(
    session: ClientSession,
    *,
    media: WeixinInboundMedia,
    peer_id: str,
    message_id: str,
    base_url: str,
    token: str,
) -> Path:
    if not media.download_url and not media.encrypt_query_param:
        raise UserFacingError("missing download url")

    url = _resolve_weixin_download_url(
        media.download_url,
        encrypted_query_param=media.encrypt_query_param,
        base_url=base_url,
    )
    async with session.get(url, headers=_download_headers(token)) as response:
        raw = await response.read()
        if response.status >= 400:
            raise UserFacingError(f"download HTTP {response.status}: {raw[:120]!r}")

    data = _maybe_decrypt_weixin_media(raw, media)
    path = _weixin_inbound_media_path(peer_id=peer_id, message_id=message_id, file_name=media.file_name)
    await anyio.Path(path.parent).mkdir(parents=True, exist_ok=True)
    await anyio.Path(path).write_bytes(data)
    return path


def _format_weixin_inbound_media(*, path: Path, media: WeixinInboundMedia) -> str:
    lines = [
        "用户发送了文件:",
        f"FILE:{path}",
        f"文件名: {media.file_name}",
    ]
    if media.size > 0:
        lines.append(f"大小: {media.size} bytes")
    if media.md5:
        lines.append(f"MD5: {media.md5}")
    return "\n".join(lines)


def _format_weixin_inbound_media_failure(*, media: WeixinInboundMedia, error: str) -> str:
    return f"用户发送了文件, 但微信入站下载失败: {media.file_name}\n原因: {error}"


def _weixin_inbound_media_path(*, peer_id: str, message_id: str, file_name: str) -> Path:
    root = _weixin_download_root()
    safe_peer = normalize_weixin_ilink_account_id(peer_id) or "peer"
    safe_message = normalize_weixin_ilink_account_id(message_id) or "message"
    return root / safe_peer / safe_message / _sanitize_weixin_file_name(file_name)


def _weixin_download_root() -> Path:
    raw = os.environ.get("WEIXIN_DOWNLOAD_DIR", "")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(DEFAULT_STATE_DIR).expanduser() / "files").resolve()


def _resolve_weixin_download_url(
    raw_url: str,
    *,
    encrypted_query_param: str = "",
    base_url: str,
) -> str:
    if raw_url.startswith(("http://", "https://")):
        return raw_url
    if raw_url.startswith("/"):
        return f"{base_url.rstrip('/')}{raw_url}"
    if encrypted_query_param:
        cdn_base_url = os.environ.get("WEIXIN_CDN_BASE_URL", "").strip() or WEIXIN_CDN_BASE_URL
        return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"
    return f"{base_url.rstrip('/')}/{raw_url.lstrip('/')}"


def _download_headers(token: str) -> dict[str, str]:
    headers = _headers(token, "")
    headers.pop("Content-Length", None)
    return headers


def _maybe_decrypt_weixin_media(data: bytes, media: WeixinInboundMedia) -> bytes:
    if media.encrypt_type != 1 or not media.aes_key:
        return data
    try:
        key = _parse_weixin_aes_key(media.aes_key)
    except (ValueError, binascii.Error) as exc:
        raise UserFacingError("invalid inbound media AES key") from exc
    return _aes_128_ecb_decrypt(data, key)


def _parse_weixin_aes_key(aes_key: str) -> bytes:
    decoded = base64.b64decode(aes_key)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        decoded_text = decoded.decode("ascii")
        if re.fullmatch(r"[0-9a-fA-F]{32}", decoded_text):
            return bytes.fromhex(decoded_text)
    raise ValueError("invalid AES key length")


def _aes_128_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    if not padded:
        return padded
    padding = padded[-1]
    if padding < 1 or padding > 16:
        raise UserFacingError("invalid inbound media AES padding")
    return padded[:-padding]


def _sanitize_weixin_file_name(file_name: str) -> str:
    cleaned = Path(file_name.strip().replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", cleaned).strip(" .")
    return cleaned or "file"


def _file_name_from_url(url: str) -> str:
    if not url:
        return ""
    return Path(url.split("?", 1)[0].rstrip("/")).name


def _resolve_weixin_media_path(raw_path: str, *, roots: list[Path]) -> Path:
    if not raw_path:
        raise UserFacingError("Weixin MEDIA marker is missing a file path.")

    candidate = Path(raw_path).expanduser()
    candidates = [candidate] if candidate.is_absolute() else [root / candidate for root in roots]
    resolved_roots = [_resolve_existing_root(root) for root in roots]
    for item in candidates:
        try:
            resolved = item.resolve(strict=True)
        except FileNotFoundError:
            continue
        if not resolved.is_file():
            raise UserFacingError(f"Weixin MEDIA path is not a file: {raw_path}")
        if not _is_relative_to_any(resolved, resolved_roots):
            raise UserFacingError(f"Weixin MEDIA path is outside allowed roots: {raw_path}")
        return resolved

    raise UserFacingError(f"Weixin MEDIA file does not exist: {raw_path}")


def _default_weixin_media_roots() -> list[Path]:
    raw = os.environ.get("WEIXIN_MEDIA_ROOTS") or os.environ.get("WEIXIN_SEND_FILE_ROOTS", "")
    roots = [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()]
    workspace = os.environ.get("PSI_WORKSPACE_DIR") or os.environ.get("WORKSPACE_DIR")
    if workspace:
        roots.append(Path(workspace).expanduser())
    roots.append(Path.cwd())
    return roots


def _allowed_weixin_file_extensions() -> set[str]:
    raw = os.environ.get("WEIXIN_MEDIA_ALLOWED_EXTENSIONS", "")
    if not raw.strip():
        return DEFAULT_FILE_EXTENSIONS
    return {item.strip().lower() for item in raw.split(",") if item.strip().startswith(".")}


def _weixin_upload_file_name_candidates(original_name: str) -> list[str]:
    if Path(original_name).suffix.lower() not in {".md", ".markdown"}:
        return [original_name]
    return [original_name, f"{Path(original_name).stem}.txt"]


def _weixin_cdn_upload_url(upload_url_response: dict[str, Any], *, filekey: str) -> str:
    upload_full_url = _first_string(
        upload_url_response,
        "upload_full_url",
        "uploadFullUrl",
        "upload_url",
        "uploadUrl",
        "uploadurl",
        "url",
    )
    if upload_full_url.strip():
        return upload_full_url.strip()

    upload_param = _first_string(upload_url_response, "upload_param", "uploadParam")
    if not upload_param:
        raise UserFacingError("Weixin iLink getuploadurl response did not include upload_full_url or upload_param.")

    cdn_base_url = (
        os.environ.get("WEIXIN_CDN_BASE_URL", "").strip()
        or _first_string(upload_url_response, "cdn_base_url", "cdnBaseUrl", "cdn_url", "cdnUrl")
        or WEIXIN_CDN_BASE_URL
    )
    return (
        f"{cdn_base_url.rstrip('/')}/upload?"
        f"encrypted_query_param={quote(upload_param, safe='')}&filekey={quote(filekey, safe='')}"
    )


def _weixin_encrypt_query_param(*, upload_response: dict[str, Any]) -> str:
    value = _first_string(
        upload_response,
        "encrypt_query_param",
        "encrypted_query_param",
        "encryptQueryParam",
        "encryptedQueryParam",
    )
    if value:
        return value
    return ""


def _should_retry_weixin_upload_name(
    response: dict[str, Any],
    *,
    original_name: str,
    display_name: str,
) -> bool:
    if display_name != original_name:
        return False
    if Path(original_name).suffix.lower() not in {".md", ".markdown"}:
        return False
    return response.get("ret") == -2 or response.get("errcode") == -2


def _resolve_existing_root(root: Path) -> Path:
    try:
        return root.resolve(strict=True)
    except FileNotFoundError:
        return root.resolve(strict=False)


def _is_relative_to_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


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


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _load_weixin_ilink_account_file(path: Path, *, storage_id: str) -> WeixinIlinkCredentials | None:
    data = _read_json_object(path)
    if data is None:
        return None
    token = _first_string(data, "token", "bot_token", "botToken")
    if not token:
        return None
    account_id = _first_string(data, "accountId", "account_id", "ilink_bot_id", "ilinkBotId") or storage_id
    return WeixinIlinkCredentials(
        token=token,
        account_id=account_id,
        base_url=_first_string(data, "baseUrl", "baseurl", "base_url") or ILINK_BASE_URL,
        user_id=_first_string(data, "userId", "user_id", "ilink_user_id", "ilinkUserId"),
        storage_id=storage_id,
        saved_at=_first_string(data, "savedAt", "saved_at"),
    )


def _load_openclaw_legacy_credentials(state_dir: Path) -> WeixinIlinkCredentials | None:
    legacy_path = state_dir / "credentials" / "openclaw-weixin" / "credentials.json"
    data = _read_json_object(legacy_path)
    if data is None:
        return None
    token = _first_string(data, "token", "bot_token", "botToken")
    account_id = _first_string(data, "accountId", "account_id", "ilink_bot_id", "ilinkBotId")
    if not token or not account_id:
        return None
    return WeixinIlinkCredentials(
        token=token,
        account_id=account_id,
        base_url=_first_string(data, "baseUrl", "baseurl", "base_url") or ILINK_BASE_URL,
        user_id=_first_string(data, "userId", "user_id", "ilink_user_id", "ilinkUserId"),
        storage_id=normalize_weixin_ilink_account_id(account_id),
    )


def _normalize_weixin_redirect_base_url(redirect_host: str) -> str:
    if redirect_host.startswith(("http://", "https://")):
        return redirect_host.rstrip("/")
    return f"https://{redirect_host.strip('/')}"


def _redact(data: dict[str, Any]) -> str:
    redacted = dict(data)
    for key in ("token", "bot_token", "botToken", "Authorization", "authorization"):
        if key in redacted:
            redacted[key] = "***"
    return json.dumps(redacted, ensure_ascii=False)[:500]
