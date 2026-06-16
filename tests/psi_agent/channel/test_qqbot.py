from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, cast

import anyio
import pytest
from aiohttp import ClientSession, web

from psi_agent.channel.qqbot import QQBotAdapter, fetch_qqbot_gateway_url, serve_qqbot_gateway_channel


def test_qqbot_extracts_c2c_message() -> None:
    adapter = QQBotAdapter(app_id="app-id", client_secret="client-secret")

    messages = adapter.extract_messages(
        {
            "op": 0,
            "s": 3,
            "t": "C2C_MESSAGE_CREATE",
            "id": "event-1",
            "d": {
                "id": "msg-1",
                "openid": "user-openid",
                "content": " hello c2c ",
                "msg_seq": 7,
            },
        }
    )

    assert len(messages) == 1
    assert messages[0].provider == "qq"
    assert messages[0].target_id == "user-openid"
    assert messages[0].user_id == "user-openid"
    assert messages[0].text == "hello c2c"
    assert messages[0].metadata == {
        "event_id": "event-1",
        "message_id": "msg-1",
        "message_kind": "c2c",
        "msg_seq": "7",
    }


def test_qqbot_extracts_group_at_message() -> None:
    adapter = QQBotAdapter(app_id="app-id", client_secret="client-secret")

    messages = adapter.extract_messages(
        {
            "op": 0,
            "s": 4,
            "t": "GROUP_AT_MESSAGE_CREATE",
            "d": {
                "id": "group-msg-1",
                "group_openid": "group-openid",
                "author": {"member_openid": "member-openid"},
                "content": "hello group",
            },
        }
    )

    assert len(messages) == 1
    assert messages[0].target_id == "group-openid"
    assert messages[0].user_id == "member-openid"
    assert messages[0].text == "hello group"
    assert messages[0].metadata["message_kind"] == "group"
    assert messages[0].metadata["message_id"] == "group-msg-1"


@pytest.mark.anyio
async def test_qqbot_fetches_access_token_and_gateway_url() -> None:
    token_requests: list[dict[str, object]] = []
    gateway_authorizations: list[str | None] = []

    async def token_handler(request: web.Request) -> web.Response:
        token_requests.append(await request.json())
        return web.json_response({"access_token": "access-token", "expires_in": 7200})

    async def gateway_handler(request: web.Request) -> web.Response:
        gateway_authorizations.append(request.headers.get("Authorization"))
        return web.json_response({"url": "ws://127.0.0.1/gateway"})

    async with _TcpServer(
        [
            ("POST", "/app/getAppAccessToken", token_handler),
            ("GET", "/gateway", gateway_handler),
        ]
    ) as base_url:
        adapter = QQBotAdapter(
            app_id="app-id",
            client_secret="client-secret",
            api_base_url=base_url,
            auth_base_url=base_url,
        )
        async with ClientSession() as session:
            token = await adapter.get_access_token(session)
            gateway_url = await fetch_qqbot_gateway_url(
                session=session,
                api_base_url=base_url,
                access_token=token,
            )

    assert token == "access-token"
    assert gateway_url == "ws://127.0.0.1/gateway"
    assert token_requests == [{"appId": "app-id", "clientSecret": "client-secret"}]
    assert gateway_authorizations == ["QQBot access-token"]


@pytest.mark.anyio
async def test_qqbot_gateway_c2c_calls_session_and_user_message_api() -> None:
    result = await _run_qqbot_gateway_case(
        dispatch_event={
            "op": 0,
            "s": 1,
            "t": "C2C_MESSAGE_CREATE",
            "d": {
                "id": "msg-1",
                "openid": "user-openid",
                "content": "hello qq",
                "msg_seq": 9,
            },
        },
        expected_platform_path="/v2/users/user-openid/messages",
    )

    assert result.session_payloads[0]["messages"] == [{"role": "user", "content": "hello qq"}]
    assert result.platform_requests == [
        {
            "authorization": "QQBot access-token",
            "path": "/v2/users/user-openid/messages",
            "body": {"content": "reply text", "msg_type": 0, "msg_seq": 9, "msg_id": "msg-1"},
        }
    ]
    assert result.identify_payloads[0]["op"] == 2
    identify_data = cast(dict[str, Any], result.identify_payloads[0]["d"])
    assert identify_data["token"] == "QQBot access-token"
    assert identify_data["intents"] == (1 << 25) | (1 << 26)


@pytest.mark.anyio
async def test_qqbot_gateway_group_calls_session_and_group_message_api() -> None:
    result = await _run_qqbot_gateway_case(
        dispatch_event={
            "op": 0,
            "s": 2,
            "t": "GROUP_AT_MESSAGE_CREATE",
            "d": {
                "id": "group-msg-1",
                "group_openid": "group-openid",
                "author": {"member_openid": "member-openid"},
                "content": "hello group",
            },
        },
        expected_platform_path="/v2/groups/group-openid/messages",
    )

    assert result.session_payloads[0]["messages"] == [{"role": "user", "content": "hello group"}]
    assert result.platform_requests == [
        {
            "authorization": "QQBot access-token",
            "path": "/v2/groups/group-openid/messages",
            "body": {"content": "reply text", "msg_type": 0, "msg_seq": 1, "msg_id": "group-msg-1"},
        }
    ]


class QQBotGatewayResult:
    def __init__(
        self,
        *,
        session_payloads: list[dict[str, object]],
        platform_requests: list[dict[str, object]],
        identify_payloads: list[dict[str, object]],
    ) -> None:
        self.session_payloads = session_payloads
        self.platform_requests = platform_requests
        self.identify_payloads = identify_payloads


async def _run_qqbot_gateway_case(
    *,
    dispatch_event: dict[str, object],
    expected_platform_path: str,
) -> QQBotGatewayResult:
    session_payloads: list[dict[str, object]] = []
    token_requests: list[dict[str, object]] = []
    gateway_authorizations: list[str | None] = []
    identify_payloads: list[dict[str, object]] = []
    platform_requests: list[dict[str, object]] = []
    reply_sent = anyio.Event()
    gateway_base_url = ""

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        chunk = {"choices": [{"delta": {"content": "reply text"}}]}
        await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    async def token_handler(request: web.Request) -> web.Response:
        token_requests.append(await request.json())
        return web.json_response({"access_token": "access-token", "expires_in": 7200})

    async def gateway_meta_handler(request: web.Request) -> web.Response:
        gateway_authorizations.append(request.headers.get("Authorization"))
        return web.json_response({"url": f"{gateway_base_url}/gateway"})

    async def platform_handler(request: web.Request) -> web.Response:
        platform_requests.append(
            {
                "authorization": request.headers.get("Authorization"),
                "path": request.path,
                "body": await request.json(),
            }
        )
        reply_sent.set()
        return web.json_response({"code": 0})

    async def gateway_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 45000}})
        identify_payloads.append(await ws.receive_json(timeout=5))
        await ws.send_json(dispatch_event)
        with anyio.fail_after(5):
            await reply_sent.wait()
        await ws.close()
        return ws

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer([("GET", "/gateway", gateway_handler)]) as resolved_gateway_base_url,
        _TcpServer(
            [
                ("POST", "/app/getAppAccessToken", token_handler),
                ("GET", "/gateway", gateway_meta_handler),
                ("POST", expected_platform_path, platform_handler),
            ]
        ) as platform_base_url,
        anyio.create_task_group() as tg,
    ):
        gateway_base_url = resolved_gateway_base_url

        tg.start_soon(
            partial(
                serve_qqbot_gateway_channel,
                session_socket=f"{session_base_url}/v1",
                app_id="app-id",
                client_secret="client-secret",
                api_base_url=platform_base_url,
                auth_base_url=platform_base_url,
                reconnect_delay=0.01,
            )
        )
        with anyio.fail_after(5):
            await reply_sent.wait()
        tg.cancel_scope.cancel()

    assert token_requests == [{"appId": "app-id", "clientSecret": "client-secret"}]
    assert gateway_authorizations == ["QQBot access-token"]
    return QQBotGatewayResult(
        session_payloads=session_payloads,
        platform_requests=platform_requests,
        identify_payloads=identify_payloads,
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
