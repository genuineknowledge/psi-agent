from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import anyio
import pytest
from aiohttp import ClientSession, web
from aiohttp.web_response import Response

from psi_agent.channel.platform import (
    DingTalkAdapter,
    DiscordAdapter,
    FeishuAdapter,
    PlatformAdapter,
    PlatformMessage,
    PlatformProvider,
    QQBridgeAdapter,
    SlackAdapter,
    TelegramAdapter,
    WeChatBridgeAdapter,
    WhatsAppAdapter,
    post_platform_json,
    serve_discord_gateway_channel,
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


@pytest.mark.parametrize(
    ("adapter", "payload", "target_id", "text"),
    [
        (
            TelegramAdapter(token="telegram-token"),
            {"message": {"chat": {"id": 123}, "from": {"id": 456}, "text": "hello tg"}},
            "123",
            "hello tg",
        ),
        (
            WhatsAppAdapter(token="wa-token", phone_number_id="phone-id"),
            {
                "entry": [
                    {"changes": [{"value": {"messages": [{"from": "15551234567", "text": {"body": "hello wa"}}]}}]}
                ]
            },
            "15551234567",
            "hello wa",
        ),
        (
            DiscordAdapter(bot_token="discord-token"),
            {"channel_id": "channel-1", "author": {"id": "user-1"}, "content": "hello discord"},
            "channel-1",
            "hello discord",
        ),
        (
            SlackAdapter(bot_token="slack-token"),
            {
                "type": "event_callback",
                "event": {"type": "message", "channel": "C1", "user": "U1", "text": "hello slack"},
            },
            "C1",
            "hello slack",
        ),
        (
            WeChatBridgeAdapter(),
            {
                "type": "message",
                "message": {
                    "conversation_id": "room-1",
                    "user_id": "user-1",
                    "message_id": "msg-1",
                    "text": "hello wechat",
                },
            },
            "room-1",
            "hello wechat",
        ),
        (
            QQBridgeAdapter(),
            {
                "type": "message",
                "message": {
                    "channel_id": "channel-1",
                    "user_id": "user-1",
                    "message_id": "msg-1",
                    "text": "hello qq",
                },
            },
            "channel-1",
            "hello qq",
        ),
        (
            FeishuAdapter(tenant_access_token="tenant-token"),
            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_1"}},
                    "message": {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "message_type": "text",
                        "content": '{"text":"hello feishu"}',
                    },
                },
            },
            "oc_1",
            "hello feishu",
        ),
        (
            DingTalkAdapter(),
            {
                "conversationId": "cid-1",
                "senderId": "sender-1",
                "msgId": "msg-1",
                "msgtype": "text",
                "text": {"content": "hello dingtalk"},
            },
            "cid-1",
            "hello dingtalk",
        ),
    ],
)
def test_platform_adapters_extract_messages(
    adapter,
    payload: dict[str, object],
    target_id: str,
    text: str,
) -> None:
    messages = adapter.extract_messages(payload)

    assert len(messages) == 1
    assert messages[0].target_id == target_id
    assert messages[0].text == text


@pytest.mark.anyio
async def test_slack_url_verification() -> None:
    response = await SlackAdapter(bot_token="slack-token").handle_control(
        {"type": "url_verification", "challenge": "challenge-value"}
    )

    assert isinstance(response, Response)
    assert response.text == "challenge-value"


@pytest.mark.anyio
async def test_discord_ping_control_response() -> None:
    response = await DiscordAdapter(bot_token="discord-token").handle_control({"type": 1})

    assert isinstance(response, Response)
    assert response.text is not None
    assert json.loads(response.text)["type"] == 1


@pytest.mark.anyio
async def test_telegram_webhook_calls_session_and_platform_api() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=TelegramAdapter(token="telegram-token"),
        platform_route="/bottelegram-token/sendMessage",
        incoming_payload={"message": {"chat": {"id": 123}, "from": {"id": 456}, "text": "hello"}},
        expected_session_message="hello",
        expected_platform_payload={"chat_id": "123", "text": "reply text"},
    )


@pytest.mark.anyio
async def test_whatsapp_webhook_calls_session_and_platform_api() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=WhatsAppAdapter(token="wa-token", phone_number_id="phone-id"),
        platform_route="/phone-id/messages",
        incoming_payload={
            "entry": [{"changes": [{"value": {"messages": [{"from": "15551234567", "text": {"body": "hello wa"}}]}}]}]
        },
        expected_session_message="hello wa",
        expected_platform_payload={
            "messaging_product": "whatsapp",
            "to": "15551234567",
            "type": "text",
            "text": {"body": "reply text"},
        },
    )


@pytest.mark.anyio
async def test_discord_relay_calls_session_and_platform_api() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=DiscordAdapter(bot_token="discord-token"),
        platform_route="/channels/channel-1/messages",
        incoming_payload={"channel_id": "channel-1", "author": {"id": "user-1"}, "content": "hello discord"},
        expected_session_message="hello discord",
        expected_platform_payload={"content": "reply text"},
    )


@pytest.mark.anyio
async def test_discord_gateway_calls_session_and_platform_api() -> None:
    session_payloads: list[dict[str, object]] = []
    platform_payloads: list[dict[str, object]] = []
    gateway_identifies: list[dict[str, object]] = []
    reply_sent = anyio.Event()

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "gateway reply"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def platform_handler(request: web.Request) -> web.Response:
        platform_payloads.append(await request.json())
        reply_sent.set()
        return web.json_response({"ok": True})

    async def gateway_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 45000}})
        gateway_identifies.append(await ws.receive_json(timeout=5))
        await ws.send_json(
            {
                "op": 0,
                "s": 1,
                "t": "MESSAGE_CREATE",
                "d": {
                    "channel_id": "channel-1",
                    "author": {"id": "user-1"},
                    "content": "hello gateway",
                },
            }
        )
        with anyio.fail_after(5):
            await reply_sent.wait()
        await ws.close()
        return ws

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer([("POST", "/channels/channel-1/messages", platform_handler)]) as platform_base_url,
        _TcpServer([("GET", "/gateway", gateway_handler)]) as gateway_base_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_discord_gateway_channel,
            f"{session_base_url}/v1",
            "discord-token",
            platform_base_url,
            f"{gateway_base_url}/gateway",
        )
        with anyio.fail_after(5):
            await reply_sent.wait()
        tg.cancel_scope.cancel()

    assert session_payloads[0]["messages"] == [{"role": "user", "content": "hello gateway"}]
    assert platform_payloads == [{"content": "gateway reply"}]
    assert gateway_identifies[0]["op"] == 2
    identify_data = gateway_identifies[0]["d"]
    assert isinstance(identify_data, dict)
    assert cast(dict[str, Any], identify_data)["token"] == "discord-token"


@pytest.mark.anyio
async def test_slack_webhook_calls_session_and_platform_api() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=SlackAdapter(bot_token="slack-token"),
        platform_route="/chat.postMessage",
        incoming_payload={
            "type": "event_callback",
            "event": {"type": "message", "channel": "C1", "user": "U1", "text": "hello slack"},
        },
        expected_session_message="hello slack",
        expected_platform_payload={"channel": "C1", "text": "reply text"},
    )


@pytest.mark.anyio
async def test_wechat_bridge_webhook_calls_session_and_reply_url() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=WeChatBridgeAdapter(reply_url="/wechat/reply"),
        platform_route="/wechat/reply",
        incoming_payload={
            "type": "message",
            "message": {
                "conversation_id": "room-1",
                "user_id": "user-1",
                "message_id": "msg-1",
                "text": "hello wechat",
            },
        },
        expected_session_message="hello wechat",
        expected_platform_payload={
            "conversation_id": "room-1",
            "user_id": "user-1",
            "text": "reply text",
            "in_reply_to": "msg-1",
        },
    )


@pytest.mark.anyio
async def test_wechat_bridge_secret_validation() -> None:
    async with _TcpListener() as channel_url, anyio.create_task_group() as tg:
        tg.start_soon(
            _serve_platform_channel,
            "http://127.0.0.1:9/v1",
            channel_url,
            WeChatBridgeAdapter(bridge_secret="bridge-secret"),
        )
        await anyio.sleep(0.2)

        async with ClientSession() as client:
            async with client.post(f"{channel_url}/webhook", json={"type": "ping"}) as response:
                assert response.status == 401

            async with client.post(
                f"{channel_url}/webhook",
                json={"type": "ping"},
                headers={"Authorization": "Bearer bridge-secret"},
            ) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True, "provider": "wechat", "type": "pong"}

        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_qq_bridge_webhook_calls_session_and_reply_url() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=QQBridgeAdapter(reply_url="/qq/reply"),
        platform_route="/qq/reply",
        incoming_payload={
            "type": "message",
            "message": {
                "channel_id": "channel-1",
                "user_id": "user-1",
                "message_id": "msg-1",
                "text": "hello qq",
            },
        },
        expected_session_message="hello qq",
        expected_platform_payload={
            "conversation_id": "channel-1",
            "user_id": "user-1",
            "text": "reply text",
            "in_reply_to": "msg-1",
        },
    )


@pytest.mark.anyio
async def test_feishu_webhook_calls_session_and_platform_api() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=FeishuAdapter(tenant_access_token="tenant-token"),
        platform_route="/open-apis/im/v1/messages/om_1/reply",
        incoming_payload={
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": '{"text":"hello feishu"}',
                },
            },
        },
        expected_session_message="hello feishu",
        expected_platform_payload={"msg_type": "text", "content": '{"text": "reply text"}'},
    )


@pytest.mark.anyio
async def test_feishu_webhook_fetches_tenant_token_from_app_credentials() -> None:
    session_payloads: list[dict[str, object]] = []
    token_requests: list[dict[str, object]] = []
    platform_payloads: list[dict[str, object]] = []
    reply_sent = anyio.Event()

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def token_handler(request: web.Request) -> web.Response:
        token_requests.append(await request.json())
        return web.json_response({"code": 0, "tenant_access_token": "tenant-from-app"})

    async def platform_handler(request: web.Request) -> web.Response:
        platform_payloads.append(
            {
                "authorization": request.headers.get("Authorization"),
                "body": await request.json(),
            }
        )
        reply_sent.set()
        return web.json_response({"code": 0})

    incoming_payload = {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "message_type": "text",
                "content": '{"text":"hello feishu"}',
            },
        },
    }

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer(
            [
                ("POST", "/open-apis/auth/v3/tenant_access_token/internal", token_handler),
                ("POST", "/open-apis/im/v1/messages/om_1/reply", platform_handler),
            ]
        ) as platform_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(
            _serve_platform_channel,
            f"{session_base_url}/v1",
            channel_url,
            FeishuAdapter(app_id="cli_test", app_secret="app-secret", api_base_url=platform_base_url),
        )
        await anyio.sleep(0.2)

        async with (
            ClientSession() as client,
            client.post(f"{channel_url}/webhook", json=incoming_payload) as response,
        ):
            assert response.status == 200
            assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

        with anyio.fail_after(5):
            await reply_sent.wait()
        tg.cancel_scope.cancel()

    assert token_requests == [{"app_id": "cli_test", "app_secret": "app-secret"}]
    assert platform_payloads == [
        {
            "authorization": "Bearer tenant-from-app",
            "body": {"msg_type": "text", "content": '{"text": "reply text"}'},
        }
    ]


@pytest.mark.anyio
async def test_feishu_url_verification() -> None:
    response = await FeishuAdapter(tenant_access_token="tenant-token", verification_token="verify-me").handle_control(
        {"type": "url_verification", "token": "verify-me", "challenge": "challenge-value"}
    )

    assert isinstance(response, Response)
    assert response.text is not None
    assert json.loads(response.text) == {"challenge": "challenge-value"}


@pytest.mark.anyio
async def test_dingtalk_webhook_calls_session_and_session_webhook() -> None:
    await _assert_webhook_calls_session_and_platform_api(
        adapter=DingTalkAdapter(session_webhook="/dingtalk/reply"),
        platform_route="/dingtalk/reply",
        incoming_payload={
            "conversationId": "cid-1",
            "senderId": "sender-1",
            "msgId": "msg-1",
            "msgtype": "text",
            "text": {"content": "hello dingtalk"},
        },
        expected_session_message="hello dingtalk",
        expected_platform_payload={"msgtype": "text", "text": {"content": "reply text"}},
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


@pytest.mark.anyio
async def test_whatsapp_webhook_verification() -> None:
    async with _TcpListener() as channel_url, anyio.create_task_group() as tg:
        tg.start_soon(
            _serve_platform_channel,
            "http://127.0.0.1:9/v1",
            channel_url,
            WhatsAppAdapter(token="wa-token", phone_number_id="phone-id", verify_token="verify-me"),
        )
        await anyio.sleep(0.2)

        async with (
            ClientSession() as client,
            client.get(
                f"{channel_url}/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "verify-me",
                    "hub.challenge": "challenge-value",
                },
            ) as response,
        ):
            assert response.status == 200
            assert await response.text() == "challenge-value"

        tg.cancel_scope.cancel()


async def _assert_webhook_calls_session_and_platform_api(
    *,
    adapter,
    platform_route: str,
    incoming_payload: dict[str, object],
    expected_session_message: str,
    expected_platform_payload: dict[str, object],
) -> None:
    session_payloads: list[dict[str, object]] = []
    platform_payloads: list[dict[str, object]] = []
    reply_sent = anyio.Event()

    async def session_handler(request: web.Request) -> web.StreamResponse:
        session_payloads.append(await request.json())
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"reasoning_content": "hidden", "content": "reply text"}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def platform_handler(request: web.Request) -> web.Response:
        platform_payloads.append(await request.json())
        reply_sent.set()
        return web.json_response({"ok": True})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", session_handler)]) as session_base_url,
        _TcpServer([("POST", platform_route, platform_handler)]) as platform_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        adapter = _with_api_base_url(adapter, platform_base_url)
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
    assert platform_payloads == [expected_platform_payload]


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


async def _serve_discord_gateway_channel(
    session_socket: str,
    bot_token: str,
    api_base_url: str,
    gateway_url: str,
) -> None:
    await serve_discord_gateway_channel(
        session_socket=session_socket,
        bot_token=bot_token,
        api_base_url=api_base_url,
        gateway_url=gateway_url,
        gateway_intents=1 | 512 | 4096 | 32768,
    )


def _with_api_base_url(adapter, api_base_url: str):
    if isinstance(adapter, TelegramAdapter):
        return TelegramAdapter(
            token=adapter.token,
            api_base_url=api_base_url,
            webhook_secret=adapter.webhook_secret,
        )
    if isinstance(adapter, WhatsAppAdapter):
        return WhatsAppAdapter(
            token=adapter.token,
            phone_number_id=adapter.phone_number_id,
            api_base_url=api_base_url,
            verify_token=adapter.verify_token,
        )
    if isinstance(adapter, DiscordAdapter):
        return DiscordAdapter(
            bot_token=adapter.bot_token,
            api_base_url=api_base_url,
            relay_secret=adapter.relay_secret,
        )
    if isinstance(adapter, SlackAdapter):
        return SlackAdapter(
            bot_token=adapter.bot_token,
            api_base_url=api_base_url,
            signing_secret=adapter.signing_secret,
        )
    if isinstance(adapter, WeChatBridgeAdapter):
        reply_url = adapter.reply_url
        if reply_url.startswith("/"):
            reply_url = f"{api_base_url}{reply_url}"
        return WeChatBridgeAdapter(
            reply_url=reply_url,
            bridge_secret=adapter.bridge_secret,
        )
    if isinstance(adapter, QQBridgeAdapter):
        reply_url = adapter.reply_url
        if reply_url.startswith("/"):
            reply_url = f"{api_base_url}{reply_url}"
        return QQBridgeAdapter(
            reply_url=reply_url,
            bridge_secret=adapter.bridge_secret,
        )
    if isinstance(adapter, FeishuAdapter):
        return FeishuAdapter(
            tenant_access_token=adapter.tenant_access_token,
            app_id=adapter.app_id,
            app_secret=adapter.app_secret,
            api_base_url=api_base_url,
            verification_token=adapter.verification_token,
        )
    if isinstance(adapter, DingTalkAdapter):
        session_webhook = adapter.session_webhook
        if session_webhook.startswith("/"):
            session_webhook = f"{api_base_url}{session_webhook}"
        return DingTalkAdapter(
            session_webhook=session_webhook,
            outgoing_token=adapter.outgoing_token,
        )
    raise AssertionError(f"unexpected adapter: {adapter!r}")


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
