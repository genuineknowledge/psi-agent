from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from aiohttp import ClientSession, web

from psi_agent.channel.weixin_ilink import (
    WeixinIlinkClient,
    WeixinIlinkState,
    extract_weixin_ilink_messages,
    poll_weixin_ilink_once,
)


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
