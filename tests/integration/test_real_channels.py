from __future__ import annotations

import json
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, web

from psi_agent.channel.platform import (
    DiscordAdapter,
    SlackAdapter,
    TelegramAdapter,
    WhatsAppAdapter,
    serve_discord_gateway_channel,
    serve_platform_channel,
)


def _real_channel_enabled() -> bool:
    return os.environ.get("PSI_RUN_REAL_CHANNEL_TESTS", "").lower() in {"1", "true", "yes", "on"}


def _real_discord_gateway_enabled() -> bool:
    return os.environ.get("PSI_RUN_REAL_DISCORD_GATEWAY_TESTS", "").lower() in {"1", "true", "yes", "on"}


def _require_env(*names: str) -> tuple[str, ...]:
    if not _real_channel_enabled():
        pytest.skip("Set PSI_RUN_REAL_CHANNEL_TESTS=1 to run real channel tests")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")
    return tuple(os.environ[name] for name in names)


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_telegram_real_channel_sends_reply() -> None:
    token, chat_id = _require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_TEST_CHAT_ID")
    await _run_real_channel_smoke(
        adapter=TelegramAdapter(token=token),
        payload={"message": {"chat": {"id": chat_id}, "from": {"id": "psi-agent-real-test"}, "text": "ping"}},
    )


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_whatsapp_real_channel_sends_reply() -> None:
    token, phone_number_id, recipient = _require_env(
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_TEST_RECIPIENT",
    )
    await _run_real_channel_smoke(
        adapter=WhatsAppAdapter(token=token, phone_number_id=phone_number_id),
        payload={"entry": [{"changes": [{"value": {"messages": [{"from": recipient, "text": {"body": "ping"}}]}}]}]},
    )


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_slack_real_channel_sends_reply() -> None:
    token, channel_id = _require_env("SLACK_BOT_TOKEN", "SLACK_TEST_CHANNEL_ID")
    await _run_real_channel_smoke(
        adapter=SlackAdapter(bot_token=token),
        payload={
            "type": "event_callback",
            "event": {"type": "message", "channel": channel_id, "user": "psi-agent-real-test", "text": "ping"},
        },
    )


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_discord_real_relay_sends_reply() -> None:
    token, channel_id = _require_env("DISCORD_BOT_TOKEN", "DISCORD_TEST_CHANNEL_ID")
    await _run_real_channel_smoke(
        adapter=DiscordAdapter(bot_token=token),
        payload={"channel_id": channel_id, "author": {"id": "psi-agent-real-test"}, "content": "ping"},
    )


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_discord_gateway_manual_receives_real_user_message() -> None:
    if not _real_discord_gateway_enabled():
        pytest.skip("Set PSI_RUN_REAL_DISCORD_GATEWAY_TESTS=1 for the manual Discord Gateway test")
    token, channel_id, trigger_text = _require_env(
        "DISCORD_BOT_TOKEN",
        "DISCORD_TEST_CHANNEL_ID",
        "DISCORD_GATEWAY_TEST_TEXT",
    )

    outbound_requests: list[dict[str, Any]] = []
    outbound_seen = anyio.Event()

    async def discord_api_handler(request: web.Request) -> web.Response:
        outbound_requests.append(
            {
                "channel_id": request.match_info["channel_id"],
                "body": await request.json(),
            }
        )
        outbound_seen.set()
        return web.json_response({"id": "mock-message-id"})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", _mock_session_handler)]) as session_base_url,
        _TcpServer([("POST", "/channels/{channel_id}/messages", discord_api_handler)]) as discord_api_base_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_discord_gateway_channel,
            f"{session_base_url}/v1",
            token,
            discord_api_base_url,
        )
        expected_body = {"content": f"psi-agent reply: {trigger_text}"}
        try:
            with anyio.fail_after(float(os.environ.get("DISCORD_GATEWAY_TEST_TIMEOUT", "60"))):
                while True:
                    await outbound_seen.wait()
                    for item in outbound_requests:
                        if item["channel_id"] == channel_id and item["body"] == expected_body:
                            tg.cancel_scope.cancel()
                            return
                    outbound_seen = anyio.Event()
        except TimeoutError:
            raise AssertionError(
                f"Timed out waiting for Discord Gateway message. "
                f"Send this exact text in channel {channel_id}: {trigger_text}"
            ) from None


async def _run_real_channel_smoke(*, adapter: Any, payload: dict[str, Any]) -> None:
    observed_adapter = _ObservedAdapter(adapter)
    async with (
        _TcpServer([("POST", "/v1/chat/completions", _mock_session_handler)]) as session_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(_serve_platform_channel, f"{session_base_url}/v1", channel_url, observed_adapter)
        await anyio.sleep(0.2)

        async with (
            ClientSession() as session,
            session.post(f"{channel_url}/webhook", json=payload) as response,
        ):
            body = await response.text()
            assert response.status == 200, body
            assert json.loads(body) == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

        with anyio.fail_after(float(os.environ.get("REAL_CHANNEL_SEND_TIMEOUT", "30"))):
            await observed_adapter.reply_sent.wait()
        if observed_adapter.reply_error is not None:
            raise AssertionError(
                f"Platform reply failed: {observed_adapter.reply_error}"
            ) from observed_adapter.reply_error

        tg.cancel_scope.cancel()


class _ObservedAdapter:
    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.reply_sent = anyio.Event()
        self.reply_error: BaseException | None = None

    @property
    def provider(self):
        return self._adapter.provider

    async def validate_post(self, request: web.Request, raw_body: bytes) -> None:
        await self._adapter.validate_post(request, raw_body)

    async def handle_get(self, request: web.Request) -> web.StreamResponse | None:
        return await self._adapter.handle_get(request)

    async def handle_control(self, body: dict[str, Any]) -> web.StreamResponse | None:
        return await self._adapter.handle_control(body)

    def extract_messages(self, body: dict[str, Any]):
        return self._adapter.extract_messages(body)

    async def send_reply(self, session: ClientSession, message: Any, text: str) -> None:
        try:
            await self._adapter.send_reply(session, message, text)
        except BaseException as e:
            self.reply_error = e
            raise
        finally:
            self.reply_sent.set()


async def _mock_session_handler(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    message = body["messages"][-1]["content"]
    resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    chunk = {"choices": [{"delta": {"content": f"psi-agent reply: {message}"}}]}
    await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
    await resp.write(b"data: [DONE]\n\n")
    return resp


async def _serve_platform_channel(session_socket: str, listen: str, adapter: Any) -> None:
    await serve_platform_channel(
        session_socket=session_socket,
        listen=listen,
        webhook_path="/webhook",
        adapter=adapter,
    )


async def _serve_discord_gateway_channel(session_socket: str, bot_token: str, api_base_url: str) -> None:
    await serve_discord_gateway_channel(
        session_socket=session_socket,
        bot_token=bot_token,
        api_base_url=api_base_url,
        gateway_url=os.environ.get("DISCORD_GATEWAY_URL", "wss://gateway.discord.gg/?v=10&encoding=json"),
        gateway_intents=int(os.environ.get("DISCORD_GATEWAY_INTENTS", str(1 | 512 | 4096 | 32768))),
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
