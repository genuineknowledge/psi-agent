"""Shared public-request copying for Router strategies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from psi_agent._trace import ensure_trace_id, trace_id_from_routing

from .errors import InvalidRouterRequestError
from .models import RouterTarget, RoutingScopeKey, is_candidate_id, normalize_request_overrides


def copy_public_request_body(
    *,
    body: dict[str, Any],
    request_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deep-copy public completion fields and force streaming upstream."""

    forwarded = {key: deepcopy(value) for key, value in body.items() if key not in {"model", "routing"}}
    if request_overrides is not None:
        forwarded.update(
            normalize_request_overrides(
                value=request_overrides,
                label="request_overrides",
            )
        )
    forwarded["stream"] = True
    return forwarded


def copy_target_request_body(*, body: dict[str, Any], target: RouterTarget) -> dict[str, Any]:
    """Copy a request for one explicitly typed AI or Router target."""

    forwarded = copy_public_request_body(
        body=body,
        request_overrides=target.request_overrides,
    )
    if target.backend_type == "router":
        scope = routing_scope_from_body(body=body)
        trace_id = routing_trace_id_from_body(body=body)
        routing: dict[str, Any] = {}
        if scope is not None:
            session_id, path = scope
            routing.update(
                {
                    "session_id": session_id,
                    "path": [*path, target.candidate_id],
                }
            )
        if trace_id is not None:
            routing["trace_id"] = trace_id
        if routing:
            forwarded["routing"] = routing
    return forwarded


def routing_scope_from_body(*, body: dict[str, Any]) -> RoutingScopeKey | None:
    """Validate and normalize private Router composition metadata."""

    routing = body.get("routing")
    if routing is None:
        return None
    if not isinstance(routing, dict):
        raise InvalidRouterRequestError("routing must be an object when present")

    raw_session_id = routing.get("session_id")
    raw_path = routing.get("path", [])
    if raw_session_id is None:
        if raw_path not in (None, []):
            raise InvalidRouterRequestError("routing.path requires routing.session_id")
        return None
    if not isinstance(raw_session_id, str) or not raw_session_id.strip():
        raise InvalidRouterRequestError("routing.session_id must be a non-empty string")
    if not isinstance(raw_path, list) or any(not is_candidate_id(item) for item in raw_path):
        raise InvalidRouterRequestError("routing.path must be a list of Router candidate IDs")
    return raw_session_id.strip(), tuple(raw_path)


def routing_trace_id_from_body(*, body: dict[str, Any]) -> str | None:
    """Validate and normalize the private Router trace identifier."""

    try:
        return trace_id_from_routing(body.get("routing"))
    except ValueError as error:
        raise InvalidRouterRequestError(str(error).replace("trace_id", "routing.trace_id", 1)) from error


def ensure_routing_trace_id(*, body: dict[str, Any]) -> str:
    """Return this Router turn's trace ID, creating private metadata once."""

    trace_id = routing_trace_id_from_body(body=body)
    trace_id = ensure_trace_id(trace_id)
    routing = body.get("routing")
    detached = dict(routing) if isinstance(routing, dict) else {}
    detached["trace_id"] = trace_id
    body["routing"] = detached
    return trace_id


__all__ = [
    "copy_public_request_body",
    "copy_target_request_body",
    "ensure_routing_trace_id",
    "routing_scope_from_body",
    "routing_trace_id_from_body",
]
