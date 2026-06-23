from __future__ import annotations

import json
import socket
import textwrap
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, web

from psi_agent.gateway.profile import (
    GatewayProfile,
    ProfileChannelConfig,
    load_gateway_profile,
    serve_profile_gateway,
)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "systems").mkdir(parents=True)
    (workspace / "tools").mkdir()
    (workspace / "systems" / "system.py").write_text(
        textwrap.dedent(
            """\
            async def system_prompt_builder() -> str:
                return "You are a profile gateway runtime test agent."
            """
        ),
        encoding="utf-8",
    )
    return workspace


def test_load_gateway_profile_reads_profile_yaml(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    profile_dir = tmp_path / "gateway" / "profiles" / "colin"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.yaml").write_text(
        textwrap.dedent(
            f"""\
            name: colin
            workspace: {workspace.as_posix()}
            ai: openai-completions
            model: test-model
            base_url: http://model.example/v1
            api_key_env: TEST_MODEL_KEY

            channels:
              - name: wechat
                type: weixin
                listen: http://127.0.0.1:18080
                webhook_path: /wechat/webhook
                bridge_secret: bridge-secret
              - name: disabled-slack
                type: slack
                enabled: false
                bot_token: xoxb-secret
            """
        ),
        encoding="utf-8",
    )

    profile = load_gateway_profile(profile_dir=str(profile_dir))

    assert profile.name == "colin"
    assert profile.profile_dir == profile_dir.resolve()
    assert profile.workspace == workspace.as_posix()
    assert profile.ai == "openai-completions"
    assert profile.model == "test-model"
    assert len(profile.channels) == 2
    assert profile.channels[0].kind == "wechat-bridge"
    assert profile.channels[0].webhook_path == "/wechat/webhook"
    assert profile.channels[1].enabled is False


@pytest.mark.anyio
async def test_profile_gateway_serves_profile_channel_and_writes_state(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    profile_dir = tmp_path / "profile"
    reply_received = anyio.Event()
    replies: list[dict[str, object]] = []
    ai_requests: list[dict[str, Any]] = []

    async def ai_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        ai_requests.append(body)
        message = body["messages"][-1]["content"]
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"reply: {message}"}, "finish_reason": "stop"}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def reply_handler(request: web.Request) -> web.Response:
        replies.append(await request.json())
        reply_received.set()
        return web.json_response({"ok": True})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", ai_handler)]) as ai_base_url,
        _TcpServer([("POST", "/reply", reply_handler)]) as reply_base_url,
        _TcpListener() as session_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        profile = GatewayProfile(
            name="test",
            profile_dir=profile_dir,
            workspace=str(workspace),
            ai="openai-completions",
            model="test-model",
            ai_socket=f"{ai_base_url}/v1",
            session_socket=f"{session_base_url}/v1",
            channels=(
                _channel(
                    name="wechat",
                    kind="wechat-bridge",
                    listen=channel_url,
                    webhook_path="/wechat/webhook",
                    reply_url=f"{reply_base_url}/reply",
                    bridge_secret="bridge-secret",
                ),
            ),
        )
        tg.start_soon(serve_profile_gateway, profile)
        await _wait_http_ready(f"{channel_url}/wechat/webhook")

        async with (
            ClientSession() as session,
            session.post(
                f"{channel_url}/wechat/webhook",
                json={
                    "type": "message",
                    "message": {
                        "conversation_id": "room-1",
                        "user_id": "user-1",
                        "message_id": "msg-1",
                        "text": "hello",
                    },
                },
                headers={"Authorization": "Bearer bridge-secret"},
            ) as response,
        ):
            assert response.status == 200
            assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

        with anyio.fail_after(5):
            await reply_received.wait()
        tg.cancel_scope.cancel()

    assert ai_requests[0]["messages"][-1] == {"role": "user", "content": "hello"}
    assert replies == [
        {
            "conversation_id": "room-1",
            "user_id": "user-1",
            "text": "reply: hello",
            "in_reply_to": "msg-1",
        }
    ]

    channel_directory = json.loads((profile_dir / "channel_directory.json").read_text(encoding="utf-8"))
    gateway_state = json.loads((profile_dir / "gateway_state.json").read_text(encoding="utf-8"))
    assert channel_directory["channels"][0]["type"] == "wechat-bridge"
    assert gateway_state["profile"] == "test"
    assert "bridge-secret" not in json.dumps(channel_directory)
    assert "bridge-secret" not in json.dumps(gateway_state)


@pytest.mark.anyio
async def test_profile_gateway_serves_multiple_paths_on_one_listener(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    profile_dir = tmp_path / "profile"
    replies: list[dict[str, object]] = []
    reply_received = anyio.Event()

    async def ai_handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        message = body["messages"][-1]["content"]
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        chunk = {"choices": [{"delta": {"content": f"reply: {message}"}, "finish_reason": "stop"}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    async def reply_handler(request: web.Request) -> web.Response:
        replies.append(await request.json())
        reply_received.set()
        return web.json_response({"ok": True})

    async with (
        _TcpServer([("POST", "/v1/chat/completions", ai_handler)]) as ai_base_url,
        _TcpServer([("POST", "/reply", reply_handler)]) as reply_base_url,
        _TcpListener() as session_base_url,
        _TcpListener() as channel_url,
        anyio.create_task_group() as tg,
    ):
        profile = GatewayProfile(
            name="multi",
            profile_dir=profile_dir,
            workspace=str(workspace),
            ai="openai-completions",
            model="test-model",
            ai_socket=f"{ai_base_url}/v1",
            session_socket=f"{session_base_url}/v1",
            channels=(
                _channel(
                    name="wechat",
                    kind="wechat-bridge",
                    listen=channel_url,
                    webhook_path="/wechat/webhook",
                    reply_url=f"{reply_base_url}/reply",
                ),
                _channel(
                    name="qq",
                    kind="qq-bridge",
                    listen=channel_url,
                    webhook_path="/qq/webhook",
                    reply_url=f"{reply_base_url}/reply",
                ),
            ),
        )
        tg.start_soon(serve_profile_gateway, profile)
        await _wait_http_ready(f"{channel_url}/wechat/webhook")
        await _wait_http_ready(f"{channel_url}/qq/webhook")

        async with (
            ClientSession() as session,
            session.post(
                f"{channel_url}/qq/webhook",
                json={
                    "type": "message",
                    "message": {
                        "channel_id": "group-1",
                        "user_id": "user-1",
                        "message_id": "qq-msg-1",
                        "text": "hello qq",
                    },
                },
            ) as response,
        ):
            assert response.status == 200
            assert await response.json() == {"ok": True, "messages": 1, "queued": 1, "duplicates": 0}

        with anyio.fail_after(5):
            await reply_received.wait()
        tg.cancel_scope.cancel()

    assert replies == [
        {
            "conversation_id": "group-1",
            "user_id": "user-1",
            "text": "reply: hello qq",
            "in_reply_to": "qq-msg-1",
        }
    ]


def _channel(**kwargs):
    options = {
        key: str(value)
        for key, value in kwargs.items()
        if key not in {"name", "kind", "listen", "webhook_path"} and value is not None
    }
    return ProfileChannelConfig(
        name=kwargs["name"],
        kind=kwargs["kind"],
        listen=kwargs["listen"],
        webhook_path=kwargs["webhook_path"],
        options=options,
    )


async def _wait_http_ready(url: str, timeout_sec: float = 5.0) -> None:
    deadline = anyio.current_time() + timeout_sec
    async with ClientSession() as session:
        while anyio.current_time() < deadline:
            try:
                async with session.get(url) as response:
                    if response.status < 500:
                        return
            except OSError:
                await anyio.sleep(0.05)
                continue
            await anyio.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {url}")


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
    async def __aenter__(self) -> str:
        sock, url = _bind_localhost()
        sock.close()
        return url

    async def __aexit__(self, *_args: object) -> None:
        return None


def _bind_localhost() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, f"http://127.0.0.1:{port}"
