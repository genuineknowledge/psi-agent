from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from psi_agent.errors import UserFacingError
from psi_agent.run.config import load_run_profile_config
from psi_agent.setup import Setup


def _feed_inputs(monkeypatch: pytest.MonkeyPatch, text_answers: list[str], secret_answers: list[str]) -> None:
    text_iter: Iterator[str] = iter(text_answers)
    secret_iter: Iterator[str] = iter(secret_answers)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: next(text_iter))
    monkeypatch.setattr("getpass.getpass", lambda *_args, **_kwargs: next(secret_iter))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


@pytest.mark.anyio
async def test_setup_writes_api_key_into_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    # AI backend, model, base URL, channel choice (skip)
    _feed_inputs(monkeypatch, ["openai", "", "", "skip"], ["sk-test-123"])

    await Setup(
        config=str(config_path),
        workspace=str(workspace),
        gateway_home=str(tmp_path / "gateway"),
    ).run()

    profile = load_run_profile_config(config_path=str(config_path), require_api_key_env=False)
    assert profile.ai == "openai-completions"
    assert profile.model == "gpt-4o-mini"
    assert profile.api_key == "sk-test-123"
    assert (workspace / "systems" / "system.py").exists()


@pytest.mark.anyio
async def test_setup_restricts_config_permissions_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform.startswith("win"):
        pytest.skip("POSIX file permissions only")
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    _feed_inputs(monkeypatch, ["openai", "", "", "skip"], ["sk-test-123"])

    await Setup(
        config=str(config_path),
        workspace=str(workspace),
        gateway_home=str(tmp_path / "gateway"),
    ).run()

    assert (config_path.stat().st_mode & 0o777) == 0o600


@pytest.mark.anyio
async def test_setup_generates_feishu_gateway_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    gateway_home = tmp_path / "gateway"
    # backend, model, base_url, channel=feishu, app_id, verification_token
    _feed_inputs(
        monkeypatch,
        ["anthropic", "", "", "feishu", "cli_app_123", "verify_tok"],
        ["sk-test-123", "app-secret-xyz"],
    )

    await Setup(
        config=str(config_path),
        workspace=str(workspace),
        gateway_home=str(gateway_home),
    ).run()

    profile_yaml = gateway_home / "profiles" / "fusion" / "profile.yaml"
    data = yaml.safe_load(profile_yaml.read_text(encoding="utf-8"))
    assert data["ai"] == "anthropic-messages"
    assert data["api_key"] == "sk-test-123"
    channel = data["channels"][0]
    assert channel["type"] == "feishu"
    assert channel["app_id"] == "cli_app_123"
    assert channel["app_secret"] == "app-secret-xyz"
    assert channel["verification_token"] == "verify_tok"
    assert channel["webhook_path"] == "/feishu/webhook"


@pytest.mark.anyio
async def test_setup_generates_wechat_gateway_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    gateway_home = tmp_path / "gateway"
    # backend, model, base_url, channel=wechat, reply_url, bridge_secret
    _feed_inputs(
        monkeypatch,
        ["openai", "", "", "wechat", "https://bridge.example/reply", "bridge-secret"],
        ["sk-test-123"],
    )

    await Setup(
        config=str(config_path),
        workspace=str(workspace),
        gateway_home=str(gateway_home),
    ).run()

    data = yaml.safe_load((gateway_home / "profiles" / "fusion" / "profile.yaml").read_text(encoding="utf-8"))
    channel = data["channels"][0]
    assert channel["type"] == "wechat-bridge"
    assert channel["reply_url"] == "https://bridge.example/reply"
    assert channel["bridge_secret"] == "bridge-secret"


@pytest.mark.anyio
async def test_setup_requires_a_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(UserFacingError, match="interactive terminal"):
        await Setup(config=str(tmp_path / "config.toml"), workspace=str(tmp_path / "ws")).run()
