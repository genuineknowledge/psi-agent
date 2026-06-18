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
from psi_agent.channel.qqbot import (
    QQBOT_DEFAULT_API_BASE_URL,
    QQBOT_DEFAULT_AUTH_BASE_URL,
    QQBOT_DEFAULT_GATEWAY_INTENTS,
    QQBotAdapter,
    fetch_qqbot_gateway_url,
    serve_qqbot_gateway_channel,
)
from psi_agent.channel.session_client import collect_session_reply
from psi_agent.channel.weixin_ilink import (
    ILINK_BASE_URL,
    LONG_POLL_TIMEOUT_MS,
    WeixinIlinkClient,
    WeixinIlinkState,
    extract_weixin_ilink_messages,
    resolve_weixin_ilink_credentials,
    resolve_weixin_ilink_state_dir,
)


def _real_channel_enabled() -> bool:
    return os.environ.get("PSI_RUN_REAL_CHANNEL_TESTS", "").lower() in {"1", "true", "yes", "on"}


def _real_discord_gateway_enabled() -> bool:
    return os.environ.get("PSI_RUN_REAL_DISCORD_GATEWAY_TESTS", "").lower() in {"1", "true", "yes", "on"}


def _real_qqbot_gateway_enabled() -> bool:
    return os.environ.get("PSI_RUN_REAL_QQBOT_GATEWAY_TESTS", "").lower() in {"1", "true", "yes", "on"}


def _real_weixin_ilink_enabled() -> bool:
    return os.environ.get("PSI_RUN_REAL_WEIXIN_ILINK_TESTS", "").lower() in {"1", "true", "yes", "on"}


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


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_qqbot_real_credentials_fetch_gateway_url() -> None:
    app_id, client_secret = _require_env("QQ_APP_ID", "QQ_CLIENT_SECRET")
    api_base_url = os.environ.get("QQ_API_BASE_URL", QQBOT_DEFAULT_API_BASE_URL)
    auth_base_url = os.environ.get("QQ_AUTH_BASE_URL", QQBOT_DEFAULT_AUTH_BASE_URL)

    adapter = QQBotAdapter(
        app_id=app_id,
        client_secret=client_secret,
        api_base_url=api_base_url,
        auth_base_url=auth_base_url,
    )
    async with ClientSession() as session:
        access_token = await adapter.get_access_token(session)
        gateway_url = await fetch_qqbot_gateway_url(
            session=session,
            api_base_url=api_base_url,
            access_token=access_token,
        )

    assert access_token
    assert gateway_url.startswith(("ws://", "wss://"))


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_qqbot_gateway_manual_receives_real_user_message() -> None:
    if not _real_qqbot_gateway_enabled():
        pytest.skip("Set PSI_RUN_REAL_QQBOT_GATEWAY_TESTS=1 for the manual QQBot Gateway test")
    app_id, client_secret, trigger_text = _require_env("QQ_APP_ID", "QQ_CLIENT_SECRET", "QQ_GATEWAY_TEST_TEXT")
    api_base_url = os.environ.get("QQ_API_BASE_URL", QQBOT_DEFAULT_API_BASE_URL)
    auth_base_url = os.environ.get("QQ_AUTH_BASE_URL", QQBOT_DEFAULT_AUTH_BASE_URL)

    gateway_url = os.environ.get("QQ_GATEWAY_URL", "")
    if not gateway_url:
        adapter = QQBotAdapter(
            app_id=app_id,
            client_secret=client_secret,
            api_base_url=api_base_url,
            auth_base_url=auth_base_url,
        )
        async with ClientSession() as session:
            access_token = await adapter.get_access_token(session)
            gateway_url = await fetch_qqbot_gateway_url(
                session=session,
                api_base_url=api_base_url,
                access_token=access_token,
            )

    outbound_requests: list[dict[str, Any]] = []
    outbound_seen = anyio.Event()

    async def qqbot_api_handler(request: web.Request) -> web.Response:
        body = await request.json()
        outbound_requests.append(
            {
                "path": request.path,
                "body": body,
            }
        )
        outbound_seen.set()
        return web.json_response({"code": 0})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", _mock_session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/v2/users/{target_id}/messages", qqbot_api_handler),
                ("POST", "/v2/groups/{target_id}/messages", qqbot_api_handler),
            ]
        ) as qqbot_api_mock_base_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_qqbot_gateway_channel,
            f"{session_base_url}/v1",
            app_id,
            client_secret,
            qqbot_api_mock_base_url,
            auth_base_url,
            gateway_url,
        )
        try:
            with anyio.fail_after(float(os.environ.get("QQ_GATEWAY_TEST_TIMEOUT", "90"))):
                while True:
                    await outbound_seen.wait()
                    for item in outbound_requests:
                        content = str(item["body"].get("content") or "")
                        if trigger_text in content:
                            tg.cancel_scope.cancel()
                            return
                    outbound_seen = anyio.Event()
        except TimeoutError:
            raise AssertionError(
                f"Timed out waiting for QQBot Gateway message. "
                f"Send this exact text to the bot or @ it in a group: {trigger_text}"
            ) from None


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_weixin_ilink_real_getupdates_smoke() -> None:
    if not _real_channel_enabled():
        pytest.skip("Set PSI_RUN_REAL_CHANNEL_TESTS=1 to run real channel tests")
    base_url = os.environ.get("WEIXIN_BASE_URL", ILINK_BASE_URL)
    credentials = resolve_weixin_ilink_credentials(
        base_url=base_url,
        state_dir=resolve_weixin_ilink_state_dir(os.environ.get("WEIXIN_STATE_DIR", "")),
    )
    timeout_ms = int(os.environ.get("WEIXIN_ILINK_SMOKE_TIMEOUT_MS", "1000"))

    async with ClientSession() as session:
        response = await credentials.client().get_updates(
            session,
            sync_buf="",
            timeout_ms=timeout_ms,
        )

    assert response.get("ret") in {None, 0}
    assert response.get("errcode") in {None, 0}


@pytest.mark.anyio
@pytest.mark.real_channel
async def test_weixin_ilink_manual_receives_real_user_message_and_replies() -> None:
    if not _real_weixin_ilink_enabled():
        pytest.skip("Set PSI_RUN_REAL_WEIXIN_ILINK_TESTS=1 for the manual Weixin iLink test")
    (trigger_text,) = _require_env("WEIXIN_ILINK_TEST_TEXT")
    base_url = os.environ.get("WEIXIN_BASE_URL", ILINK_BASE_URL)
    credentials = resolve_weixin_ilink_credentials(
        base_url=base_url,
        state_dir=resolve_weixin_ilink_state_dir(os.environ.get("WEIXIN_STATE_DIR", "")),
    )
    poll_timeout_ms = int(os.environ.get("WEIXIN_ILINK_POLL_TIMEOUT_MS", str(LONG_POLL_TIMEOUT_MS)))

    client = WeixinIlinkClient(
        token=credentials.token,
        account_id=credentials.account_id,
        base_url=credentials.base_url,
    )
    state = WeixinIlinkState()
    async with (
        _TcpServer([("POST", "/v1/chat/completions", _mock_session_handler)]) as session_base_url,
        ClientSession() as session,
    ):
        try:
            with anyio.fail_after(float(os.environ.get("WEIXIN_ILINK_TEST_TIMEOUT", "120"))):
                while True:
                    response = await client.get_updates(
                        session,
                        sync_buf=state.sync_buf,
                        timeout_ms=poll_timeout_ms,
                    )
                    next_sync_buf = response.get("get_updates_buf")
                    if isinstance(next_sync_buf, str):
                        state.sync_buf = next_sync_buf

                    for message in extract_weixin_ilink_messages(response, account_id=credentials.account_id):
                        if message.context_token:
                            state.context_tokens[message.peer_id] = message.context_token
                        if trigger_text not in message.text:
                            continue
                        reply = await collect_session_reply(
                            session_socket=f"{session_base_url}/v1",
                            message=message.text,
                        )
                        await client.send_text(
                            session,
                            to_user_id=message.peer_id,
                            text=reply,
                            context_token=state.context_tokens.get(message.peer_id, ""),
                        )
                        return
        except TimeoutError:
            raise AssertionError(
                f"Timed out waiting for Weixin iLink message. Send this exact text to the account: {trigger_text}"
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


async def _serve_qqbot_gateway_channel(
    session_socket: str,
    app_id: str,
    client_secret: str,
    api_base_url: str,
    auth_base_url: str,
    gateway_url: str,
) -> None:
    await serve_qqbot_gateway_channel(
        session_socket=session_socket,
        app_id=app_id,
        client_secret=client_secret,
        api_base_url=api_base_url,
        auth_base_url=auth_base_url,
        gateway_url=gateway_url,
        gateway_intents=int(os.environ.get("QQ_GATEWAY_INTENTS", str(QQBOT_DEFAULT_GATEWAY_INTENTS))),
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
