from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from psi_agent.session import Session
from psi_agent.session._routing import (
    build_effective_model_ai_sockets,
    build_model_ai_sockets,
    select_ai_socket_for_model,
)
from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice


def test_select_ai_socket_for_model_uses_mapping() -> None:
    assert select_ai_socket_for_model(
        "qwen3.6-chat",
        default_ai_socket="http://default-ai",
        model_ai_sockets={"qwen3.6-chat": "http://qwen-ai"},
    ) == "http://qwen-ai"


def test_select_ai_socket_for_model_falls_back_to_default() -> None:
    assert select_ai_socket_for_model(
        "unknown-model",
        default_ai_socket="http://default-ai",
        model_ai_sockets={"qwen3.6-chat": "http://qwen-ai"},
    ) == "http://default-ai"


def test_build_model_ai_sockets_uses_model_names() -> None:
    assert build_model_ai_sockets(
        ["qwen3.6-chat", "deepseek-v4-pro", "gpt-4o"],
        socket_dir="/tmp",
    ) == {
        "qwen3.6-chat": "/tmp/qwen3.6-chat.sock",
        "deepseek-v4-pro": "/tmp/deepseek-v4-pro.sock",
        "gpt-4o": "/tmp/gpt-4o.sock",
    }


def test_build_model_ai_sockets_preserves_explicit_mapping() -> None:
    assert build_model_ai_sockets(
        ["qwen3.6-chat", "deepseek-v4-pro"],
        socket_dir="/tmp",
        explicit_model_ai_sockets={"deepseek-v4-pro": "/custom/deepseek.sock"},
    ) == {
        "qwen3.6-chat": "/tmp/qwen3.6-chat.sock",
        "deepseek-v4-pro": "/custom/deepseek.sock",
    }


def test_build_effective_model_ai_sockets_expands_local_socket_dir() -> None:
    assert build_effective_model_ai_sockets(
        "/tmp/ai.sock",
        ["qwen3.6-chat", "deepseek-v4-pro"],
        explicit_model_ai_sockets={"deepseek-v4-pro": "/custom/deepseek.sock"},
    ) == {
        "qwen3.6-chat": "/tmp/qwen3.6-chat.sock",
        "deepseek-v4-pro": "/custom/deepseek.sock",
    }


def test_build_effective_model_ai_sockets_keeps_explicit_mapping_for_remote_backend() -> None:
    assert build_effective_model_ai_sockets(
        "http://default-ai",
        ["qwen3.6-chat", "deepseek-v4-pro"],
        explicit_model_ai_sockets={"deepseek-v4-pro": "http://deepseek-ai"},
    ) == {
        "deepseek-v4-pro": "http://deepseek-ai",
    }


def test_session_defaults_include_routing_fields() -> None:
    first_session = Session(
        ai_socket="http://default-ai",
        channel_socket="http://channel",
    )
    second_session = Session(
        ai_socket="http://default-ai",
        channel_socket="http://channel",
    )

    assert first_session.model_names == []
    assert first_session.model_ai_sockets == {}

    first_session.model_names.append("qwen3.6-chat")
    first_session.model_ai_sockets["qwen3.6-chat"] = "http://qwen-ai"

    assert second_session.model_names == []
    assert second_session.model_ai_sockets == {}


@pytest.mark.anyio
async def test_agent_routes_request_to_matching_ai_socket() -> None:
    seen_sockets: list[str | None] = []

    class _RoutingSessionAgent(SessionAgent):
        async def _stream_ai_request(
            self,
            request_body: dict,
            *,
            ai_socket: str | None = None,
        ) -> AsyncIterator[ChatCompletionChunk]:
            del request_body
            seen_sockets.append(ai_socket)
            yield ChatCompletionChunk(
                choices=[
                    StreamChoice(
                        index=0,
                        delta=DeltaMessage(content="ok"),
                        finish_reason="stop",
                    )
                ],
            )

    agent = _RoutingSessionAgent(
        ai_socket="http://default-ai",
        model_ai_sockets={"qwen3.6-chat": "http://qwen-ai"},
        tools={},
    )

    async for _ in agent.run({"role": "user", "content": "hi"}, extra_params={"model": "qwen3.6-chat"}):
        pass

    assert seen_sockets == ["http://qwen-ai"]


@pytest.mark.anyio
async def test_agent_uses_default_ai_socket_when_model_not_mapped() -> None:
    seen_sockets: list[str | None] = []

    class _RoutingSessionAgent(SessionAgent):
        async def _stream_ai_request(
            self,
            request_body: dict,
            *,
            ai_socket: str | None = None,
        ) -> AsyncIterator[ChatCompletionChunk]:
            del request_body
            seen_sockets.append(ai_socket)
            yield ChatCompletionChunk(
                choices=[
                    StreamChoice(
                        index=0,
                        delta=DeltaMessage(content="ok"),
                        finish_reason="stop",
                    )
                ],
            )

    agent = _RoutingSessionAgent(
        ai_socket="http://default-ai",
        model_ai_sockets={"qwen3.6-chat": "http://qwen-ai"},
        tools={},
    )

    async for _ in agent.run({"role": "user", "content": "hi"}, extra_params={"model": "unknown-model"}):
        pass

    assert seen_sockets == ["http://default-ai"]
