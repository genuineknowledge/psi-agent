from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from aiohttp import ClientSession, web

from psi_agent.channel.weixin_ilink import (
    WeixinIlinkClient,
    WeixinIlinkCredentials,
    WeixinIlinkState,
    credentials_from_weixin_ilink_status,
    extract_weixin_ilink_messages,
    load_weixin_ilink_credentials,
    login_weixin_ilink_by_qr,
    normalize_weixin_ilink_account_id,
    poll_weixin_ilink_once,
    recent_weixin_ilink_tokens,
    resolve_weixin_ilink_credentials,
    save_weixin_ilink_credentials,
)
from psi_agent.errors import UserFacingError


def test_extract_weixin_ilink_text_message_updates_peer_and_context() -> None:
    messages = extract_weixin_ilink_messages(
        {
            "msgs": [
                {
                    "from_user_id": "user-1",
                    "to_user_id": "account-1",
                    "msg_id": "msg-1",
                    "context_token": "ctx-1",
                    "item_list": [{"type": 1, "text_item": {"text": "hello weixin"}}],
                }
            ]
        },
        account_id="account-1",
    )

    assert len(messages) == 1
    assert messages[0].peer_id == "user-1"
    assert messages[0].sender_id == "user-1"
    assert messages[0].message_id == "msg-1"
    assert messages[0].context_token == "ctx-1"
    assert messages[0].text == "hello weixin"


def test_weixin_ilink_credentials_from_status_normalizes_storage_id() -> None:
    credentials = credentials_from_weixin_ilink_status(
        {
            "status": "confirmed",
            "bot_token": "token-1",
            "ilink_bot_id": "abc@im.bot",
            "baseurl": "https://redirect.example",
            "ilink_user_id": "user-1",
        },
        base_url="https://ilinkai.weixin.qq.com",
    )

    assert credentials.token == "token-1"
    assert credentials.account_id == "abc@im.bot"
    assert credentials.storage_id == "abc-im-bot"
    assert credentials.base_url == "https://redirect.example"
    assert credentials.user_id == "user-1"
    assert normalize_weixin_ilink_account_id("abc@im.wechat") == "abc-im-wechat"


def test_weixin_ilink_credentials_round_trip(tmp_path) -> None:
    credentials = WeixinIlinkCredentials(
        token="token-1",
        account_id="abc@im.bot",
        base_url="https://redirect.example",
        user_id="user-1",
        storage_id="abc-im-bot",
    )

    save_weixin_ilink_credentials(tmp_path, credentials)
    loaded = load_weixin_ilink_credentials(tmp_path)

    assert loaded == WeixinIlinkCredentials(
        token="token-1",
        account_id="abc@im.bot",
        base_url="https://redirect.example",
        user_id="user-1",
        storage_id="abc-im-bot",
        saved_at=loaded.saved_at if loaded else "",
    )
    assert recent_weixin_ilink_tokens(tmp_path) == ["token-1"]
    assert resolve_weixin_ilink_credentials(state_dir=tmp_path).token == "token-1"


@pytest.mark.anyio
async def test_weixin_ilink_qr_login_saves_credentials(tmp_path) -> None:
    qrcode_requests: list[dict[str, object]] = []
    status_queries: list[str] = []

    async def qrcode_handler(request: web.Request) -> web.Response:
        qrcode_requests.append(await request.json())
        assert request.query["bot_type"] == "3"
        return web.json_response(
            {
                "ret": 0,
                "qrcode": "qr-1",
                "qrcode_img_content": "https://qr.example/1",
            }
        )

    async def status_handler(request: web.Request) -> web.Response:
        status_queries.append(request.query_string)
        assert request.query["qrcode"] == "qr-1"
        return web.json_response(
            {
                "ret": 0,
                "status": "confirmed",
                "bot_token": "token-1",
                "ilink_bot_id": "abc@im.bot",
                "baseurl": "https://redirect.example",
                "ilink_user_id": "user-1",
            }
        )

    async with _TcpServer(
        [
            ("POST", "/ilink/bot/get_bot_qrcode", qrcode_handler),
            ("GET", "/ilink/bot/get_qrcode_status", status_handler),
        ]
    ) as ilink_base_url:
        credentials = await login_weixin_ilink_by_qr(
            state_dir=tmp_path,
            base_url=ilink_base_url,
            timeout_seconds=5,
            poll_interval_seconds=0.01,
        )

    assert qrcode_requests == [{"local_token_list": [], "base_info": {"channel_version": "2.2.0"}}]
    assert status_queries == ["qrcode=qr-1"]
    assert credentials.token == "token-1"
    assert credentials.account_id == "abc@im.bot"
    assert load_weixin_ilink_credentials(tmp_path) == credentials


@pytest.mark.anyio
async def test_weixin_ilink_poll_calls_session_and_sendmessage() -> None:
    session_payloads: list[dict[str, object]] = []
    getupdates_payloads: list[dict[str, object]] = []
    sendmessage_payloads: list[dict[str, object]] = []

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"reasoning_content": "hidden", "content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        getupdates_payloads.append(await request.json())
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "context_token": "ctx-1",
                        "item_list": [{"type": 1, "text_item": {"text": "hello weixin"}}],
                    }
                ],
            }
        )

    async def sendmessage_handler(request: web.Request) -> web.Response:
        sendmessage_payloads.append(await request.json())
        return web.json_response({"ret": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        state = WeixinIlinkState(sync_buf="sync-prev")
        messages = await poll_weixin_ilink_once(
            session_socket=f"{session_base_url}/v1",
            client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
            state=state,
            session=http_session,
            timeout_ms=1000,
        )

    assert [message.text for message in messages] == ["hello weixin"]
    assert state.sync_buf == "sync-next"
    assert state.context_tokens == {"user-1": "ctx-1"}
    assert session_payloads[0]["messages"] == [{"role": "user", "content": "hello weixin"}]
    assert getupdates_payloads[0]["get_updates_buf"] == "sync-prev"
    assert getupdates_payloads[0]["base_info"] == {"channel_version": "2.2.0"}

    sent_msg = cast(dict[str, Any], sendmessage_payloads[0]["msg"])
    assert isinstance(sent_msg, dict)
    assert sent_msg["to_user_id"] == "user-1"
    assert sent_msg["message_type"] == 2
    assert sent_msg["message_state"] == 2
    assert sent_msg["context_token"] == "ctx-1"
    assert sent_msg["item_list"] == [{"type": 1, "text_item": {"text": "reply text"}}]


@pytest.mark.anyio
async def test_weixin_ilink_poll_raises_on_sendmessage_error() -> None:
    async def session_handler(request: web.Request) -> web.StreamResponse:
        _ = await request.json()
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def getupdates_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response(
            {
                "ret": 0,
                "get_updates_buf": "sync-next",
                "msgs": [
                    {
                        "from_user_id": "user-1",
                        "to_user_id": "account-1",
                        "msg_id": "msg-1",
                        "item_list": [{"type": 1, "text_item": {"text": "hello weixin"}}],
                    }
                ],
            }
        )

    async def sendmessage_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response({"ret": 1, "errmsg": "send failed"})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/ilink/bot/getupdates", getupdates_handler),
                ("POST", "/ilink/bot/sendmessage", sendmessage_handler),
            ]
        ) as ilink_base_url,
        ClientSession() as http_session,
    ):
        with pytest.raises(UserFacingError, match="send failed"):
            await poll_weixin_ilink_once(
                session_socket=f"{session_base_url}/v1",
                client=WeixinIlinkClient(token="token-1", account_id="account-1", base_url=ilink_base_url),
                state=WeixinIlinkState(sync_buf="sync-prev"),
                session=http_session,
                timeout_ms=1000,
            )


class _TcpServer:
    def __init__(self, routes: list[tuple[str, str, Callable[[web.Request], Awaitable[web.StreamResponse]]]]) -> None:
        self._routes = routes
        self._runner: web.AppRunner | None = None
        self._url = ""

    async def __aenter__(self) -> str:
        app = web.Application()
        for method, path, handler in self._routes:
            app.router.add_route(method, path, handler)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock, self._url = _bind_localhost()
        site = web.SockSite(self._runner, sock)
        await site.start()
        return self._url

    async def __aexit__(self, *_args: object) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


def _bind_localhost() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, f"http://127.0.0.1:{port}"
