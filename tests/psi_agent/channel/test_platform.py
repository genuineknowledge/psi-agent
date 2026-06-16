from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, web

from psi_agent.channel.platform import (
    PlatformAdapter,
    PlatformMessage,
    PlatformProvider,
    post_platform_json,
    serve_platform_channel,
    serve_platform_channels,
)
from psi_agent.errors import UserFacingError


@pytest.mark.anyio
async def test_platform_webhook_calls_session_and_adapter_reply() -> None:
    await _assert_platform_webhook_calls_session_and_reply(
        adapter=_FakeAdapter(provider="telegram"),
        incoming_payload={"target_id": "target-1", "text": "hello", "message_id": "msg-1"},
        expected_session_message="hello",
        expected_reply={"target_id": "target-1", "text": "reply text", "in_reply_to": "msg-1"},
    )


@pytest.mark.anyio
async def test_platform_webhook_uses_control_response() -> None:
    async with _TcpListener() as channel_url, anyio.create_task_group() as tg:
        tg.start_soon(
            _serve_platform_channel,
            "http://127.0.0.1:9/v1",
            channel_url,
            _FakeAdapter(provider="slack"),
        )
        await anyio.sleep(0.2)

        async with (
            ClientSession() as client,
            client.post(f"{channel_url}/webhook", json={"type": "ping"}) as response,
        ):
            assert response.status == 200
            assert await response.json() == {"ok": True, "provider": "slack", "type": "pong"}

        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_platform_webhook_deduplicates_retried_message() -> None:
    session_payloads: list[dict[str, object]] = []
    replies: list[dict[str, str]] = []
    reply_sent = anyio.Event()

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    incoming_payload = {"target_id": "target-1", "text": "hello", "message_id": "msg-1"}

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_platform_channel,
            f"{session_base_url}/v1",
            channel_url,
            _FakeAdapter(provider="telegram", replies=replies, reply_sent=reply_sent),
        )
        await anyio.sleep(0.2)

        async with ClientSession() as client:
            async with client.post(f"{channel_url}/webhook", json=incoming_payload) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

            async with client.post(f"{channel_url}/webhook", json=incoming_payload) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True, "messages": 1, "queued": 0, "duplicates": 1}

        with anyio.fail_after(5):
            await reply_sent.wait()
        tg.cancel_scope.cancel()

    assert len(session_payloads) == 1
    assert replies == [{"target_id": "target-1", "text": "reply text", "in_reply_to": "msg-1"}]


@pytest.mark.anyio
async def test_platform_channel_group_serves_multiple_routes() -> None:
    telegram_replies: list[dict[str, str]] = []
    slack_replies: list[dict[str, str]] = []
    telegram_reply_sent = anyio.Event()
    slack_reply_sent = anyio.Event()

    async def session_handler(request: web.Request) -> web.StreamResponse:
        payload = await request.json()
        message = payload["messages"][0]["content"]
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"reply to {message}"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_platform_channels,
            f"{session_base_url}/v1",
            channel_url,
            [
                (
                    "/telegram/webhook",
                    _FakeAdapter(provider="telegram", replies=telegram_replies, reply_sent=telegram_reply_sent),
                ),
                (
                    "/slack/webhook",
                    _FakeAdapter(provider="slack", replies=slack_replies, reply_sent=slack_reply_sent),
                ),
            ],
        )
        await anyio.sleep(0.2)

        async with ClientSession() as client:
            async with client.post(
                f"{channel_url}/telegram/webhook",
                json={"target_id": "chat-1", "text": "telegram message"},
            ) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

            async with client.post(
                f"{channel_url}/slack/webhook",
                json={"target_id": "channel-1", "text": "slack message"},
            ) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

        with anyio.fail_after(5):
            await telegram_reply_sent.wait()
            await slack_reply_sent.wait()
        tg.cancel_scope.cancel()

    assert telegram_replies == [{"target_id": "chat-1", "text": "reply to telegram message", "in_reply_to": ""}]
    assert slack_replies == [{"target_id": "channel-1", "text": "reply to slack message", "in_reply_to": ""}]


@pytest.mark.anyio
async def test_platform_json_post_handles_provider_error() -> None:
    async def platform_handler(request: web.Request) -> web.Response:
        _ = await request.json()
        return web.json_response({"ok": False, "error": "bad request"})

    async with (
        _TcpServer([("POST", "/send", platform_handler)]) as platform_base_url,
        ClientSession() as session,
    ):
        with pytest.raises(UserFacingError, match="bad request"):
            await post_platform_json(session, f"{platform_base_url}/send", {"text": "hello"})


async def _assert_platform_webhook_calls_session_and_reply(
    *,
    adapter: _FakeAdapter,
    incoming_payload: dict[str, object],
    expected_session_message: str,
    expected_reply: dict[str, str],
) -> None:
    session_payloads: list[dict[str, object]] = []
    reply_sent = anyio.Event()
    adapter.reply_sent = reply_sent

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"reasoning_content": "hidden", "content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_platform_channel,
            f"{session_base_url}/v1",
            channel_url,
            adapter,
        )
        await anyio.sleep(0.2)

        async with (
            ClientSession() as client,
            client.post(
                f"{channel_url}/webhook",
                json=incoming_payload,
            ) as response,
        ):
            assert response.status == 200
            assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

        with anyio.fail_after(5):
            await reply_sent.wait()

        tg.cancel_scope.cancel()

    assert session_payloads[0]["messages"] == [{"role": "user", "content": expected_session_message}]
    assert adapter.replies == [expected_reply]


async def _serve_platform_channel(session_socket: str, listen: str, adapter: _FakeAdapter) -> None:
    await serve_platform_channel(
        session_socket=session_socket,
        listen=listen,
        webhook_path="/webhook",
        adapter=adapter,
    )


async def _serve_platform_channels(
    session_socket: str,
    listen: str,
    routes: list[tuple[str, PlatformAdapter]],
) -> None:
    await serve_platform_channels(
        session_socket=session_socket,
        listen=listen,
        routes=routes,
    )


@dataclass
class _FakeAdapter:
    provider: PlatformProvider
    replies: list[dict[str, str]] = field(default_factory=list)
    reply_sent: anyio.Event | None = None

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
        text = body.get("text")
        target_id = body.get("target_id")
        if not text or not target_id:
            return []
        message_id = body.get("message_id")
        metadata = {"message_id": str(message_id)} if message_id else {}
        return [
            PlatformMessage(
                provider=self.provider,
                target_id=str(target_id),
                user_id=str(body.get("user_id") or ""),
                text=str(text),
                metadata=metadata,
            )
        ]

    async def send_reply(self, session: ClientSession, message: PlatformMessage, text: str) -> None:
        _ = session
        self.replies.append(
            {
                "target_id": message.target_id,
                "text": text,
                "in_reply_to": message.metadata.get("message_id", ""),
            }
        )
        if self.reply_sent is not None:
            self.reply_sent.set()


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


class _TcpListener:
    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._url = ""

    async def __aenter__(self) -> str:
        self._sock, self._url = _bind_localhost()
        self._sock.close()
        self._sock = None
        return self._url

    async def __aexit__(self, *_args: object) -> None:
        if self._sock is not None:
            self._sock.close()


def _bind_localhost() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, f"http://127.0.0.1:{port}"
