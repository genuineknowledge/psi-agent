from __future__ import annotations

import pytest

from psi_agent.router.errors import InvalidRouterRequestError
from psi_agent.router.models import RouterTarget
from psi_agent.router.request import copy_public_request_body, copy_target_request_body, routing_scope_from_body


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


def test_ai_target_strips_private_routing_metadata() -> None:
    source = {
        "messages": [{"role": "user", "content": "hello"}],
        "routing": {"session_id": "session-a", "path": ["parent"]},
    }
    target = RouterTarget("candidate-1", "ai.sock", "direct AI")

    copied = copy_target_request_body(body=source, target=target)

    assert "routing" not in copied
    assert source["routing"]["path"] == ["parent"]


def test_router_target_appends_candidate_to_an_independent_path_copy() -> None:
    source = {
        "messages": [{"role": "user", "content": "hello"}],
        "routing": {"session_id": " session-a ", "path": ["parent"]},
    }
    target = RouterTarget("candidate-2", "router.sock", "nested", backend_type="router")

    copied = copy_target_request_body(body=source, target=target)

    assert copied["routing"] == {
        "session_id": "session-a",
        "path": ["parent", "candidate-2"],
    }
    copied["routing"]["path"].append("changed")
    assert source["routing"]["path"] == ["parent"]


@pytest.mark.parametrize(
    "routing",
    [
        "not-an-object",
        {"session_id": ""},
        {"session_id": "session-a", "path": "not-a-list"},
        {"session_id": "session-a", "path": ["bad candidate"]},
        {"path": ["candidate-1"]},
    ],
)
def test_routing_scope_rejects_malformed_private_metadata(routing: object) -> None:
    with pytest.raises(InvalidRouterRequestError):
        routing_scope_from_body(body={"routing": routing})


def test_routing_scope_distinguishes_paths_for_the_same_session() -> None:
    assert routing_scope_from_body(body={"routing": {"session_id": "session-a", "path": ["left"]}}) == (
        "session-a",
        ("left",),
    )
    assert routing_scope_from_body(body={"routing": {"session_id": "session-a", "path": ["right"]}}) == (
        "session-a",
        ("right",),
    )
