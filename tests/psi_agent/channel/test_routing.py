from __future__ import annotations

import pytest

from psi_agent.channel.route import _routing
from psi_agent.channel.route.chat_with_ustc import (
    ROUTER_BASE_URL,
    ROUTER_MODEL,
    _build_router_messages,
    _extract_model_choice,
)


def test_router_constants_are_hardcoded() -> None:
    assert ROUTER_BASE_URL == "https://api.llm.ustc.edu.cn/v1"
    assert ROUTER_MODEL == "qwen-chat"


def test_build_router_messages_includes_models_and_message() -> None:
    messages = _build_router_messages("帮我分析这段代码", ["gpt-4o-mini", "gpt-4o"])
    assert messages[0]["role"] == "system"
    assert "中国大陆" in messages[0]["content"]
    assert "海外" in messages[0]["content"]
    assert "任务的具体信息" in messages[1]["content"]
    assert "候选模型" in messages[1]["content"]
    assert "gpt-4o-mini" in messages[1]["content"]
    assert "帮我分析这段代码" in messages[1]["content"]


def test_extract_model_choice_parses_json() -> None:
    assert _extract_model_choice('{"model": "gpt-4o"}', ["gpt-4o-mini", "gpt-4o"]) == "gpt-4o"


def test_extract_model_choice_falls_back_to_plain_model_name() -> None:
    assert _extract_model_choice("gpt-4o-mini", ["gpt-4o-mini", "gpt-4o"]) == "gpt-4o-mini"


@pytest.mark.anyio
async def test_select_model_for_message_uses_router_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_choose_model_via_ustc_api(
        message: str,
        *,
        models: tuple[str, ...],
        api_key: str = "",
    ) -> str:
        assert message == "请帮我分析这段代码"
        assert models == ("gpt-4o-mini", "gpt-4o")
        return "gpt-4o"

    monkeypatch.setattr(_routing, "choose_model_via_ustc_api", fake_choose_model_via_ustc_api)

    model = await _routing.select_model_for_message(
        "请帮我分析这段代码",
        models=["gpt-4o-mini", "gpt-4o"],
    )
    assert model == "gpt-4o"


@pytest.mark.anyio
async def test_select_model_for_message_falls_back_when_router_returns_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_choose_model_via_ustc_api(
        message: str,
        *,
        models: tuple[str, ...],
        api_key: str = "",
    ) -> str | None:
        return "unknown-model"

    monkeypatch.setattr(_routing, "choose_model_via_ustc_api", fake_choose_model_via_ustc_api)

    model = await _routing.select_model_for_message(
        "请帮我分析这段代码",
        models=["gpt-4o-mini", "gpt-4o"],
    )
    assert model == "gpt-4o-mini"
