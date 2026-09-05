from __future__ import annotations

import os

import pytest
from aiohttp import web

from psi_agent.ai import Ai, _build_http_client, serve_ai
from psi_agent.protocol import DEFAULT_MAX_CONTEXT_TOKENS


def test_ai_backend_env_fallback(monkeypatch) -> None:
    """Empty fields should resolve from PSI_AI_* env vars."""
    monkeypatch.setenv("PSI_AI_PROVIDER", "openai")
    monkeypatch.setenv("PSI_AI_MODEL", "gpt-from-env")
    monkeypatch.setenv("PSI_AI_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setenv("PSI_AI_API_KEY", "sk-from-env")

    config = Ai(session_socket="/tmp/s.sock", provider="", model="", base_url="", api_key="")
    assert config.provider or os.environ.get("PSI_AI_PROVIDER", "") == "openai"
    assert config.model or os.environ.get("PSI_AI_MODEL", "") == "gpt-from-env"
    assert config.base_url or os.environ.get("PSI_AI_BASE_URL", "") == "https://env.example.com/v1"
    assert config.api_key or os.environ.get("PSI_AI_API_KEY", "") == "sk-from-env"


def test_ai_backend_cli_overrides_env(monkeypatch) -> None:
    """CLI args should take precedence over env vars."""
    monkeypatch.setenv("PSI_AI_PROVIDER", "openai")
    monkeypatch.setenv("PSI_AI_MODEL", "gpt-from-env")

    config = Ai(session_socket="/tmp/s.sock", provider="anthropic", model="claude-from-cli")
    assert config.provider == "anthropic"
    assert config.model == "claude-from-cli"


def test_ai_backend_defaults() -> None:
    """All fields default to empty string."""
    config = Ai(session_socket="/tmp/s.sock")
    assert config.provider == ""
    assert config.model == ""
    assert config.api_key == ""
    assert config.base_url == ""
    assert config.verbose is False


@pytest.mark.anyio
async def test_ai_layer_falls_back_to_the_shared_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env var set, the AI layer must land on ``protocol``'s number.

    This exercises ``Ai.run``'s own resolution rather than re-deriving it, so
    reintroducing a private literal here fails.  The AI and Session layers
    shipped 100000 and 200000 respectively, and production ran for a day against
    the lower one — below its own fixed overhead, so compaction fired on every
    turn and could never get under the ceiling.  The identity is asserted rather
    than the value, so raising the ceiling later does not touch this criterion.
    """
    monkeypatch.delenv("PSI_MAX_CONTEXT_TOKENS", raising=False)
    seen: list[object] = []

    async def _capture_serve_ai(*, max_context_tokens: int, **_rest: object) -> None:
        seen.append(max_context_tokens)

    monkeypatch.setattr("psi_agent.ai.serve_ai", _capture_serve_ai)

    await Ai(session_socket="/tmp/s.sock").run()

    assert seen == [DEFAULT_MAX_CONTEXT_TOKENS]


@pytest.mark.anyio
async def test_serve_ai_cleans_up_runner_on_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If site.start() fails after runner.setup(), the runner must be cleaned up."""
    cleanup_calls: list[bool] = []
    orig_cleanup = web.AppRunner.cleanup

    async def spy_cleanup(self: web.AppRunner) -> None:
        cleanup_calls.append(True)
        await orig_cleanup(self)

    monkeypatch.setattr(web.AppRunner, "cleanup", spy_cleanup)

    class _BadSite:
        async def start(self) -> None:
            raise RuntimeError("bind failed")

    monkeypatch.setattr("psi_agent.ai.create_site", lambda runner, addr: _BadSite())

    async def _handler(request: web.Request) -> web.StreamResponse:
        return web.StreamResponse()

    with pytest.raises(RuntimeError, match="bind failed"):
        await serve_ai(
            socket_path="/tmp/ignored.sock",
            provider="openai",
            model="m",
            api_key="k",
            base_url="b",
            handler=_handler,
        )
    assert cleanup_calls == [True]


@pytest.mark.anyio
async def test_serve_ai_app_accepts_large_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The forwarder app must lift aiohttp's 1 MiB default so big contexts aren't rejected."""
    captured: dict[str, web.Application] = {}

    class _Site:
        async def start(self) -> None:
            pass

    def _capture_site(runner: web.AppRunner, addr: str) -> _Site:
        captured["app"] = runner.app
        raise RuntimeError("stop after capture")  # abort before sleep_forever

    monkeypatch.setattr("psi_agent.ai.create_site", _capture_site)

    async def _handler(request: web.Request) -> web.StreamResponse:
        return web.StreamResponse()

    with pytest.raises(RuntimeError, match="stop after capture"):
        await serve_ai(
            socket_path="/tmp/ignored.sock",
            provider="openai",
            model="m",
            api_key="k",
            base_url="b",
            handler=_handler,
        )
    # 100 MiB, matching the gateway app — well above aiohttp's 1 MiB default.
    assert captured["app"]._client_max_size == 100 * 1024 * 1024


async def test_http_client_built_for_providers_that_accept_it() -> None:
    """OpenAI 系与 Anthropic 系要拿到自带 TLS 上下文的 client。

    这个 client 是绕开握手包被丢的唯一手段 (见 psi_agent._tls); 拿不到就是
    「登录成功了但一对话就超时」。
    """
    for provider in ("openai", "anthropic", "deepseek", "moonshot"):
        client = _build_http_client(provider)
        assert client is not None, provider
        await client.aclose()


def test_no_http_client_for_providers_that_reject_it() -> None:
    """Mistral 的 SDK 不收 http_client —— 传过去会 TypeError, 把好路弄断。"""
    assert _build_http_client("mistral") is None


def test_unknown_provider_does_not_block_startup() -> None:
    """provider 名字不认识不该拦住服务起来: 报错留给第一次请求, 那时信息更全。"""
    assert _build_http_client("no-such-provider") is None
    assert _build_http_client("") is None
