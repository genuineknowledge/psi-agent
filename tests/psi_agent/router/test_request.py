from __future__ import annotations

from typing import Any

import pytest

from psi_agent.router.errors import InvalidRouterRequestError
from psi_agent.router.models import RouterTarget
from psi_agent.router.request import (
    copy_public_request_body,
    copy_target_request_body,
    ensure_routing_trace_id,
    routing_scope_from_body,
    routing_trace_id_from_body,
)

_TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


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


def test_copy_public_request_overrides_are_shallow_and_detached() -> None:
    source: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.2,
        "future_parameter": {"preserved": True, "nested": {"source": True}},
        "stream": False,
    }
    overrides: dict[str, Any] = {
        "temperature": 0.8,
        "future_parameter": {"replacement": True},
    }

    copied = copy_public_request_body(body=source, request_overrides=overrides)

    assert copied == {
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.8,
        "future_parameter": {"replacement": True},
        "stream": True,
    }
    copied["future_parameter"]["replacement"] = False
    assert overrides == {
        "temperature": 0.8,
        "future_parameter": {"replacement": True},
    }
    assert source["future_parameter"] == {"preserved": True, "nested": {"source": True}}


@pytest.mark.parametrize("field", ["messages", "model", "routing", "stream"])
def test_copy_public_request_overrides_reject_protected_fields(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        copy_public_request_body(
            body={"messages": [{"role": "user", "content": "hello"}]},
            request_overrides={field: "blocked"},
        )


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
        "routing": {"session_id": " session-a ", "path": ["parent"], "trace_id": _TRACE_ID},
    }
    target = RouterTarget("candidate-2", "router.sock", "nested", backend_type="router")

    copied = copy_target_request_body(body=source, target=target)

    assert copied["routing"] == {
        "session_id": "session-a",
        "path": ["parent", "candidate-2"],
        "trace_id": _TRACE_ID,
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


def test_routing_trace_id_is_generated_once_and_preserved_for_nested_routers() -> None:
    source: dict[str, Any] = {"messages": [], "routing": {"session_id": "session-a"}}

    trace_id = ensure_routing_trace_id(body=source)
    copied = copy_target_request_body(
        body=source,
        target=RouterTarget("candidate-1", "router.sock", "nested", backend_type="router"),
    )

    assert ensure_routing_trace_id(body=source) == trace_id
    assert routing_trace_id_from_body(body=source) == trace_id
    assert copied["routing"] == {
        "session_id": "session-a",
        "path": ["candidate-1"],
        "trace_id": trace_id,
    }


@pytest.mark.parametrize("trace_id", [123, "", "not-a-uuid"])
def test_routing_trace_id_rejects_malformed_private_metadata(trace_id: object) -> None:
    with pytest.raises(InvalidRouterRequestError, match="trace_id"):
        routing_trace_id_from_body(body={"routing": {"trace_id": trace_id}})
