from __future__ import annotations

import pytest

from psi_agent.gateway._ai_defaults import (
    DEFAULT_MODEL,
    DEFAULT_REMOTE_API_KEY,
    DEFAULT_REMOTE_BASE_URL,
    DEFAULT_REMOTE_PROVIDER,
    ai_defaults_public_dict,
    resolve_ai_defaults,
)


def _clear_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PSI_AI_PROVIDER",
        "PSI_AI_MODEL",
        "PSI_AI_BASE_URL",
        "PSI_AI_API_KEY",
        "ZHIPUAI_API_KEY",
        "BIGMODEL_API_KEY",
        "ZAI_API_KEY",
        "PSI_HAITUN_AI_URL",
        "HAITUN_DEFAULT_AI_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_resolve_ai_defaults_always_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)

    resolved = resolve_ai_defaults()
    assert resolved.source == "remote_default"
    assert resolved.provider == DEFAULT_REMOTE_PROVIDER
    assert resolved.model == DEFAULT_MODEL
    assert resolved.base_url == DEFAULT_REMOTE_BASE_URL
    assert resolved.api_key == DEFAULT_REMOTE_API_KEY


def test_resolve_ai_defaults_ignores_local_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("PSI_AI_API_KEY", "sk-should-be-ignored")
    monkeypatch.setenv("PSI_AI_PROVIDER", "zai")
    monkeypatch.setenv("PSI_AI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

    resolved = resolve_ai_defaults()
    assert resolved.source == "remote_default"
    assert resolved.provider == DEFAULT_REMOTE_PROVIDER
    assert resolved.base_url == DEFAULT_REMOTE_BASE_URL
    assert resolved.api_key == DEFAULT_REMOTE_API_KEY


def test_resolve_ai_defaults_remote_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("PSI_HAITUN_AI_URL", "https://ai.example.test")
    monkeypatch.setenv("PSI_AI_MODEL", "glm-custom")

    resolved = resolve_ai_defaults()
    assert resolved.base_url == "https://ai.example.test"
    assert resolved.model == "glm-custom"


def test_ai_defaults_public_dict_hides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_ai_env(monkeypatch)
    resolved = resolve_ai_defaults()
    pub = ai_defaults_public_dict(resolved)
    assert pub["source"] == "remote_default"
    assert pub["api_key_configured"] is False
    assert "api_key" not in pub
    assert pub["base_url"] == DEFAULT_REMOTE_BASE_URL
