from __future__ import annotations

from psi_agent.ai.server import _resolve_model


def test_resolve_model_uses_request_override() -> None:
    body = {"model": "gpt-4o-mini", "stream": True, "messages": []}

    model, request_model = _resolve_model(body, "default-model")

    assert model == "gpt-4o-mini"
    assert request_model == "gpt-4o-mini"
    assert "model" not in body
    assert "stream" not in body


def test_resolve_model_falls_back_to_default() -> None:
    body = {"messages": []}

    model, request_model = _resolve_model(body, "default-model")

    assert model == "default-model"
    assert request_model is None
