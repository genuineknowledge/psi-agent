"""Contracts for aggregation configuration and feedback compaction."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, cast

import pytest

from psi_agent.router.aggregation.models import (
    AggregationConfig,
    AggregationFeedback,
    compact_feedback,
)
from psi_agent.router.models import RouterTarget


def _target(candidate_id: str = "candidate-1", socket: str = "target-1.sock") -> RouterTarget:
    return RouterTarget(candidate_id, socket, "coding")


def _success_feedback(**changes: object) -> AggregationFeedback:
    fields: dict[str, Any] = {
        "candidate_id": "candidate-1",
        "description": "coding",
        "status": "success",
    }
    fields.update(changes)
    return AggregationFeedback(**fields)


def test_aggregation_config_normalizes_socket_fields_and_targets() -> None:
    config = AggregationConfig(
        session_socket=" router.sock ",
        aggregator_socket=" aggregate.sock ",
        targets=[_target()],
    )

    assert config.session_socket == "router.sock"
    assert config.aggregator_socket == "aggregate.sock"
    assert config.targets == (_target(),)


@pytest.mark.parametrize(
    ("aggregator_socket", "targets", "match"),
    [
        ("router.sock", [_target()], "aggregator_socket"),
        ("target-1.sock", [_target()], "aggregator_socket"),
        ("aggregate.sock", [_target(socket="router.sock")], "session_socket"),
        ("aggregate.sock", [_target(), _target()], "candidate_id"),
        (
            "aggregate.sock",
            [_target(), _target(candidate_id="candidate-2")],
            "socket",
        ),
    ],
)
def test_aggregation_config_rejects_socket_collisions_and_duplicate_targets(
    aggregator_socket: str, targets: list[RouterTarget], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        AggregationConfig(
            session_socket="router.sock",
            aggregator_socket=aggregator_socket,
            targets=targets,
        )


@pytest.mark.parametrize("timeout", [0, -1, math.inf, -math.inf, math.nan, True, "30"])
@pytest.mark.parametrize("field", ["aggregator_timeout", "target_timeout"])
def test_aggregation_config_rejects_invalid_timeouts(field: str, timeout: object) -> None:
    with pytest.raises(ValueError, match=field):
        if field == "aggregator_timeout":
            AggregationConfig(
                session_socket="router.sock",
                aggregator_socket="aggregate.sock",
                targets=[_target()],
                aggregator_timeout=cast(float | None, timeout),
            )
        else:
            AggregationConfig(
                session_socket="router.sock",
                aggregator_socket="aggregate.sock",
                targets=[_target()],
                target_timeout=cast(float | None, timeout),
            )


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "12000"])
def test_aggregation_config_requires_positive_integer_context_budget(budget: object) -> None:
    with pytest.raises(ValueError, match="max_context_chars"):
        AggregationConfig(
            session_socket="router.sock",
            aggregator_socket="aggregate.sock",
            targets=[_target()],
            max_context_chars=cast(int, budget),
        )


def test_compaction_splits_budget_in_field_order_and_preserves_metadata() -> None:
    feedback = [
        _success_feedback(
            finish_reason="tool_calls",
            content="abcdefgh",
            tool_calls=(
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "12345678"},
                },
            ),
        )
    ]

    payload = compact_feedback(feedback=feedback, max_context_chars=5)

    assert payload == [
        {
            "candidate_id": "candidate-1",
            "description": "coding",
            "status": "success",
            "finish_reason": "tool_calls",
            "content": "ab…<truncated>…h",
            "tool_calls": (
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "1…<truncated>…8"},
                },
            ),
            "error_type": "",
            "error": "",
        }
    ]


def test_compaction_does_not_redistribute_unused_quota() -> None:
    feedback = [
        _success_feedback(
            content="a",
            tool_calls=({"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "12345678"}},),
        )
    ]

    payload = compact_feedback(feedback=feedback, max_context_chars=5)

    assert payload[0]["content"] == "a"
    assert payload[0]["tool_calls"][0]["function"]["arguments"] == "1…<truncated>…8"


def test_compaction_uses_marker_for_a_zero_quota_field() -> None:
    feedback = [
        _success_feedback(
            content="abcdefgh",
            tool_calls=({"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "12345678"}},),
        )
    ]

    payload = compact_feedback(feedback=feedback, max_context_chars=1)

    assert payload[0]["content"] == "a…<truncated>…"
    assert payload[0]["tool_calls"][0]["function"]["arguments"] == "…<truncated>…"


def test_compaction_preserves_failure_metadata_without_budgeting_it() -> None:
    feedback = [
        AggregationFeedback(
            candidate_id="candidate-1",
            description="coding",
            status="error",
            finish_reason="error",
            content="unmodified failure body",
            tool_calls=(
                {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "unmodified"}},
            ),
            error_type="TimeoutError",
            error="upstream timed out",
        )
    ]

    assert compact_feedback(feedback=feedback, max_context_chars=1) == [
        {
            "candidate_id": "candidate-1",
            "description": "coding",
            "status": "error",
            "finish_reason": "error",
            "content": "unmodified failure body",
            "tool_calls": (
                {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "unmodified"}},
            ),
            "error_type": "TimeoutError",
            "error": "upstream timed out",
        }
    ]


def test_compaction_does_not_mutate_feedback_or_tool_dictionaries() -> None:
    tool_call = {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "12345678"}}
    feedback = [_success_feedback(content="abcdefgh", tool_calls=(tool_call,))]
    original_tool = deepcopy(tool_call)

    payload = compact_feedback(feedback=feedback, max_context_chars=2)
    payload[0]["tool_calls"][0]["function"]["arguments"] = "changed"

    assert tool_call == original_tool
    assert feedback[0].content == "abcdefgh"
    assert feedback[0].tool_calls[0] == original_tool
