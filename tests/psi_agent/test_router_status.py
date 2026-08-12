from __future__ import annotations

from typing import Any

import pytest

from psi_agent._router_status import RouterStatus, router_status_from_event

_TRACE_ID = "123e4567-e89b-12d3-a456-426614174000"


def test_router_status_round_trips_through_the_single_choice_wire_shape() -> None:
    status = RouterStatus(
        trace_id=_TRACE_ID,
        mode="aggregation",
        phase="collecting",
        depth=1,
        completed=1,
        total=2,
        degraded=True,
    )

    assert status.to_dict() == {
        "version": 1,
        "trace_id": _TRACE_ID,
        "mode": "aggregation",
        "phase": "collecting",
        "depth": 1,
        "completed": 1,
        "total": 2,
        "degraded": True,
    }
    assert RouterStatus.from_dict(status.to_dict() | {"future_field": "ignored"}) == status
    assert router_status_from_event(status.to_event()) == status
    assert status.to_event()["choices"] == [
        {
            "index": 0,
            "delta": {"router_status": status.to_dict()},
            "finish_reason": None,
        }
    ]


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"trace_id": "not-a-uuid"}, "trace_id"),
        ({"mode": [], "phase": "collecting"}, "mode"),
        ({"mode": "routing", "phase": "collecting"}, "phase"),
        ({"depth": -1}, "depth"),
        ({"completed": True, "total": 2}, "completed"),
        ({"completed": 3, "total": 2}, "completed"),
        ({"attempt": 3, "total": 2}, "attempt"),
        ({"mode": "routing", "phase": "selecting", "completed": 0, "total": 1}, "completed"),
        ({"mode": "aggregation", "phase": "collecting", "attempt": 1, "total": 1}, "attempt"),
        ({"mode": "fallback", "phase": "attempting", "degraded": True}, "degraded"),
    ],
)
def test_router_status_rejects_invalid_or_contradictory_fields(
    changes: dict[str, Any],
    match: str,
) -> None:
    values: dict[str, Any] = {
        "trace_id": _TRACE_ID,
        "mode": "aggregation",
        "phase": "collecting",
        "depth": 0,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=match):
        RouterStatus(**values)


def test_router_status_parser_rejects_unknown_schema_versions_and_malformed_events() -> None:
    with pytest.raises(ValueError, match="version"):
        RouterStatus.from_dict(
            {
                "version": 2,
                "trace_id": _TRACE_ID,
                "mode": "routing",
                "phase": "selecting",
                "depth": 0,
            }
        )
    with pytest.raises(ValueError, match="version"):
        RouterStatus.from_dict(
            {
                "version": True,
                "trace_id": _TRACE_ID,
                "mode": "routing",
                "phase": "selecting",
                "depth": 0,
            }
        )
    with pytest.raises(ValueError, match="single choice"):
        router_status_from_event({"choices": []})
    assert (
        router_status_from_event({"choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": None}]})
        is None
    )


@pytest.mark.parametrize(
    "event",
    [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "router_status": RouterStatus(
                            trace_id=_TRACE_ID,
                            mode="routing",
                            phase="selecting",
                        ).to_dict(),
                        "content": "mixed",
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "router_status": RouterStatus(
                            trace_id=_TRACE_ID,
                            mode="routing",
                            phase="selecting",
                        ).to_dict()
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    ],
)
def test_router_status_parser_requires_an_independent_non_terminal_delta(event: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="router_status"):
        router_status_from_event(event)
