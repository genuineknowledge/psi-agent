"""Shared request trace identifiers for internal HTTP/SSE boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

TRACE_ID_HEADER = "X-Psi-Trace-Id"


def normalize_trace_id(value: object) -> str:
    """Return one canonical UUID trace identifier or raise ``ValueError``."""

    if not isinstance(value, str):
        raise ValueError("trace_id must be a UUID string")
    try:
        return str(UUID(value.strip()))
    except ValueError as error:
        raise ValueError("trace_id must be a UUID string") from error


def ensure_trace_id(value: object | None = None) -> str:
    """Normalize an existing trace identifier or create a new one."""

    return str(uuid4()) if value is None else normalize_trace_id(value)


def trace_id_from_routing(value: object) -> str | None:
    """Read the optional trace identifier from private ``routing`` metadata."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("routing must be an object when present")
    raw_trace_id = value.get("trace_id")
    return None if raw_trace_id is None else normalize_trace_id(raw_trace_id)


def resolve_trace_id(*, headers: Mapping[str, str] | None = None, routing: object = None) -> str:
    """Resolve one trace from the internal header and Router metadata.

    Supplying both is allowed only when they identify the same request.  A
    missing identifier is created at the first boundary and then propagated.
    """

    header_trace_id: str | None = None
    if headers is not None:
        raw_header = headers.get(TRACE_ID_HEADER)
        if raw_header is not None:
            header_trace_id = normalize_trace_id(raw_header)
    routing_trace_id = trace_id_from_routing(routing)
    if header_trace_id is not None and routing_trace_id is not None and header_trace_id != routing_trace_id:
        raise ValueError("trace_id header and routing.trace_id must match")
    return ensure_trace_id(header_trace_id or routing_trace_id)


__all__ = [
    "TRACE_ID_HEADER",
    "ensure_trace_id",
    "normalize_trace_id",
    "resolve_trace_id",
    "trace_id_from_routing",
]
