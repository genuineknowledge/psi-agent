from __future__ import annotations

from psi_agent.router.request import copy_public_request_body


def test_copy_public_request_body_is_deep_and_strips_only_private_fields() -> None:
    source = {
        "model": "client-model",
        "routing": {"session_id": "private"},
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "future_parameter": {"enabled": True},
        "stream": False,
    }

    copied = copy_public_request_body(body=source)

    assert copied == {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "temperature": 0.2,
        "future_parameter": {"enabled": True},
        "stream": True,
    }
    copied["messages"][0]["content"] = "changed"
    assert source["messages"][0]["content"] == "hello"
    assert source["stream"] is False
