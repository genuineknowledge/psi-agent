from __future__ import annotations

from uuid import UUID

import pytest

from psi_agent._trace import (
    TRACE_ID_HEADER,
    ensure_trace_id,
    normalize_trace_id,
    resolve_trace_id,
    trace_id_from_routing,
)

TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


def test_normalize_trace_id_returns_canonical_uuid() -> None:
    assert normalize_trace_id(f"  {TRACE_ID.upper()}  ") == TRACE_ID


@pytest.mark.parametrize("value", [None, 1, "", "not-a-uuid"])
def test_normalize_trace_id_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="UUID string"):
        normalize_trace_id(value)


def test_ensure_trace_id_creates_uuid() -> None:
    generated = ensure_trace_id()
    assert str(UUID(generated)) == generated


def test_trace_id_from_routing_is_optional() -> None:
    assert trace_id_from_routing(None) is None
    assert trace_id_from_routing({}) is None
    assert trace_id_from_routing({"trace_id": TRACE_ID}) == TRACE_ID


def test_resolve_trace_id_accepts_matching_header_and_routing() -> None:
    assert (
        resolve_trace_id(
            headers={TRACE_ID_HEADER: TRACE_ID.upper()},
            routing={"trace_id": TRACE_ID},
        )
        == TRACE_ID
    )


def test_resolve_trace_id_rejects_conflicting_sources() -> None:
    with pytest.raises(ValueError, match="must match"):
        resolve_trace_id(
            headers={TRACE_ID_HEADER: TRACE_ID},
            routing={"trace_id": "123e4567-e89b-12d3-a456-426614174001"},
        )
