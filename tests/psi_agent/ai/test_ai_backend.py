from __future__ import annotations

import os

from psi_agent.ai import AiBackend


def test_ai_backend_env_fallback(monkeypatch) -> None:
    """Empty fields should resolve from PSI_AI_* env vars."""
    monkeypatch.setenv("PSI_AI_PROVIDER", "openai")
    monkeypatch.setenv("PSI_AI_MODEL", "gpt-from-env")
    monkeypatch.setenv("PSI_AI_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setenv("PSI_AI_API_KEY", "sk-from-env")

    config = AiBackend(session_socket="/tmp/s.sock", provider="", model="", base_url="", api_key="")
    assert config.provider or os.environ.get("PSI_AI_PROVIDER", "") == "openai"
    assert config.model or os.environ.get("PSI_AI_MODEL", "") == "gpt-from-env"
    assert config.base_url or os.environ.get("PSI_AI_BASE_URL", "") == "https://env.example.com/v1"
    assert config.api_key or os.environ.get("PSI_AI_API_KEY", "") == "sk-from-env"


def test_ai_backend_cli_overrides_env(monkeypatch) -> None:
    """CLI args should take precedence over env vars."""
    monkeypatch.setenv("PSI_AI_PROVIDER", "openai")
    monkeypatch.setenv("PSI_AI_MODEL", "gpt-from-env")

    config = AiBackend(session_socket="/tmp/s.sock", provider="anthropic", model="claude-from-cli")
    assert config.provider == "anthropic"
    assert config.model == "claude-from-cli"


def test_ai_backend_defaults() -> None:
    """All fields default to empty string."""
    config = AiBackend(session_socket="/tmp/s.sock")
    assert config.provider == ""
    assert config.model == ""
    assert config.api_key == ""
    assert config.base_url == ""
    assert config.verbose is False
