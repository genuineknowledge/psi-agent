from __future__ import annotations

import socket

import pytest
from aiohttp import web

from psi_agent.session.memory_client import FusionMemoryClient, MemoryClientConfig, build_memory_client_config


def test_build_memory_client_config_reads_expected_env_keys() -> None:
    config = build_memory_client_config(
        {
            "PSI_MEMORY_BASE_URL": "http://127.0.0.1:8700",
            "PSI_MEMORY_TIMEOUT_SECONDS": "3.5",
            "PSI_MEMORY_WORKSPACE_ID": "ws",
            "PSI_MEMORY_USER_ID": "u",
            "PSI_MEMORY_AGENT_ID": "agent",
            "PSI_MEMORY_SESSION_ID": "session-1",
        }
    )

    assert config.base_url == "http://127.0.0.1:8700"
    assert config.timeout_seconds == 3.5
    assert config.workspace_id == "ws"
    assert config.user_id == "u"
    assert config.agent_id == "agent"
    assert config.session_id == "session-1"


@pytest.mark.anyio
async def test_ingest_turn_posts_messages_scope_and_error_flag() -> None:
    seen: dict[str, object] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(await request.json())
        return web.json_response({"span_ids": ["span-1"]})

    app = web.Application()
    app.router.add_post("/ingest-turn", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    client = FusionMemoryClient(
        MemoryClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=2.0,
            workspace_id="ws",
            user_id="u",
            agent_id="agent",
            session_id="session-1",
        )
    )

    try:
        await client.ingest_turn(
            [{"role": "user", "content": "remember my aisle seat preference"}],
            turn_id="turn-1",
            turn_index=1,
            ended_with_error=False,
        )
        assert seen["messages"] == [{"role": "user", "content": "remember my aisle seat preference"}]
        assert seen["scope"]["workspace_id"] == "ws"
        assert seen["scope"]["session_id"] == "session-1"
        assert seen["metadata"]["ended_with_error"] is False
    finally:
        await runner.cleanup()
