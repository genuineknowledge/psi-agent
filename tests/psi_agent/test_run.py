from __future__ import annotations

import json
import socket
import textwrap
from pathlib import Path

import pytest
from aiohttp import web

from psi_agent.errors import UserFacingError
from psi_agent.run import _friendly_agent_error, _resolve_base_url, run_once
from psi_agent.run.config import load_run_profile_config


def _chunk(*, content: str = "", reasoning: str = "", finish_reason: str | None = None) -> str:
    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    data = {
        "id": "mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(data)}\n\n"


async def _start_tcp_ai_server(handler) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    return runner, f"http://127.0.0.1:{port}/v1"


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "tools").mkdir(parents=True)
    (workspace / "systems").mkdir(parents=True)
    (workspace / "systems" / "system.py").write_text(
        textwrap.dedent(
            """\
            async def system_prompt_builder() -> str:
                return "You are a test one-shot agent."
            """
        )
    )
    return workspace


def test_run_profile_config_accepts_utf8_bom(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            base_url = "https://example.test/v1"
            api_key = "sk-test"
            """
        ),
        encoding="utf-8-sig",
    )

    config = load_run_profile_config(config_path=str(config_path))

    assert config.ai == "openai-completions"
    assert config.model == "profile-model"
    assert config.base_url == "https://example.test/v1"
    assert config.api_key == "sk-test"


def test_run_profile_config_reports_missing_api_key_env(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            base_url = "https://example.test/v1"
            api_key_env = "PSI_TEST_MISSING_KEY"
            """
        )
    )

    with pytest.raises(UserFacingError, match="Environment variable is not set"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_preserves_api_key_env_when_not_required(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            api_key_env = "PSI_TEST_MISSING_KEY"
            """
        ),
        encoding="utf-8",
    )

    config = load_run_profile_config(config_path=str(config_path), require_api_key_env=False)

    assert config.api_key_env == "PSI_TEST_MISSING_KEY"
    assert config.api_key == ""


def test_run_profile_config_reports_invalid_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text('default_profile = "fusion"\n[profiles.fusion\n', encoding="utf-8")

    with pytest.raises(UserFacingError, match="not valid TOML"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_reports_non_utf8_config(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(UserFacingError, match="not UTF-8 text"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_reports_non_string_field(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = 123
            api_key = "sk-test"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserFacingError, match="field must be a string: model"):
        load_run_profile_config(config_path=str(config_path))


def test_run_profile_config_reports_invalid_ai(tmp_path: Path) -> None:
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            """\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "bad-backend"
            api_key = "sk-test"
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserFacingError, match='field "ai" must be one of'):
        load_run_profile_config(config_path=str(config_path))


def test_run_friendly_agent_error_classifies_common_failures() -> None:
    assert str(_friendly_agent_error("Upstream Error 401 unauthorized")).startswith(
        "Model service authentication failed."
    )
    assert str(_friendly_agent_error("Cannot connect to host")).startswith("Cannot connect to the model service.")
    assert str(_friendly_agent_error("request timed out")).startswith("The model service did not respond in time.")
    assert str(_friendly_agent_error("unexpected provider failure")).startswith("Agent run failed.")


def test_resolve_base_url_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    assert _resolve_base_url(ai="openai-completions", base_url="") == "https://api.openai.com/v1"
    assert _resolve_base_url(ai="anthropic-messages", base_url="") == "https://api.anthropic.com/v1"
    assert (
        _resolve_base_url(ai="openai-completions", base_url="https://custom.example/v1") == "https://custom.example/v1"
    )


@pytest.mark.anyio
async def test_run_once_reports_missing_workspace() -> None:
    with pytest.raises(UserFacingError, match="Workspace not found"):
        await run_once(
            workspace="does-not-exist",
            message="hello",
            ai="openai-completions",
            model="test",
            api_key="k",
        )


@pytest.mark.anyio
async def test_run_once_reports_missing_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(UserFacingError, match="API key is not configured"):
        await run_once(
            workspace=str(workspace),
            message="hello",
            ai="openai-completions",
            model="test",
        )


@pytest.mark.anyio
async def test_run_once_collects_final_text_and_uses_workspace(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    request_bodies: list[dict] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        request_bodies.append(await request.json())
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_chunk(reasoning="thinking").encode())
        await resp.write(_chunk(content="Hello").encode())
        await resp.write(_chunk(content=" world", finish_reason="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    runner, base_url = await _start_tcp_ai_server(handler)
    try:
        result = await run_once(
            workspace=str(workspace),
            message="hi",
            ai_socket=base_url,
            model="test-model",
        )
    finally:
        await runner.cleanup()

    assert result.text == "Hello world"
    assert result.reasoning == "thinking"
    assert result.had_error is False

    assert request_bodies
    assert request_bodies[0]["model"] == "test-model"
    assert request_bodies[0]["messages"][0] == {
        "role": "system",
        "content": "You are a test one-shot agent.",
    }
    assert request_bodies[0]["messages"][-1] == {"role": "user", "content": "hi"}


@pytest.mark.anyio
async def test_run_once_uses_profile_config_for_temporary_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _make_workspace(tmp_path)
    upstream_requests: list[dict] = []
    upstream_auth: list[str] = []

    async def upstream_handler(request: web.Request) -> web.StreamResponse:
        upstream_auth.append(request.headers.get("Authorization", ""))
        upstream_requests.append(await request.json())
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_chunk(content="profile works", finish_reason="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    monkeypatch.setenv("PSI_TEST_PROFILE_KEY", "sk-from-env")
    runner, upstream_base_url = await _start_tcp_ai_server(upstream_handler)
    config_path = tmp_path / "psi-agent-config.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""\
            default_profile = "fusion"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            base_url = "{upstream_base_url}"
            api_key_env = "PSI_TEST_PROFILE_KEY"
            """
        )
    )

    try:
        result = await run_once(
            workspace=str(workspace),
            message="hi from fusion",
            config=str(config_path),
        )
    finally:
        await runner.cleanup()

    assert result.text == "profile works"
    assert result.had_error is False
    assert upstream_auth == ["Bearer sk-from-env"]
    assert upstream_requests
    assert upstream_requests[0]["model"] == "profile-model"
    assert upstream_requests[0]["messages"][-1] == {"role": "user", "content": "hi from fusion"}


@pytest.mark.anyio
async def test_run_once_uses_default_workspace_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _make_workspace(tmp_path)
    request_bodies: list[dict] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        request_bodies.append(await request.json())
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(_chunk(content="default workspace works", finish_reason="stop").encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    runner, base_url = await _start_tcp_ai_server(handler)
    config_path = tmp_path / "psi-agent-config.toml"
    workspace_toml = str(workspace).replace("\\", "\\\\")
    config_path.write_text(
        textwrap.dedent(
            f"""\
            default_profile = "fusion"
            default_workspace = "{workspace_toml}"

            [profiles.fusion]
            ai = "openai-completions"
            model = "profile-model"
            base_url = "https://example.test/v1"
            api_key_env = "PSI_TEST_PROFILE_KEY"
            """
        )
    )
    monkeypatch.setenv("PSI_TEST_PROFILE_KEY", "sk-from-env")

    try:
        result = await run_once(
            workspace="",
            message="hi from default workspace",
            ai_socket=base_url,
            config=str(config_path),
        )
    finally:
        await runner.cleanup()

    assert result.text == "default workspace works"
    assert result.had_error is False
    assert request_bodies
    assert request_bodies[0]["messages"][0]["content"] == "You are a test one-shot agent."
