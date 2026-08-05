"""Shared public-request copying for Router strategies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import InvalidRouterRequestError
from .models import RouterTarget, RoutingScopeKey, is_candidate_id


def copy_public_request_body(*, body: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy public completion fields and force streaming upstream."""

    forwarded = {key: deepcopy(value) for key, value in body.items() if key not in {"model", "routing"}}
    forwarded["stream"] = True
    return forwarded


def copy_target_request_body(*, body: dict[str, Any], target: RouterTarget) -> dict[str, Any]:
    """Copy a request for one explicitly typed AI or Router target."""

    forwarded = copy_public_request_body(body=body)
    if target.backend_type == "router":
        scope = routing_scope_from_body(body=body)
        if scope is not None:
            session_id, path = scope
            forwarded["routing"] = {
                "session_id": session_id,
                "path": [*path, target.candidate_id],
            }
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


__all__ = ["copy_public_request_body", "copy_target_request_body", "routing_scope_from_body"]
