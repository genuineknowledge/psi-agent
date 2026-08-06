"""Tests for pure Router grading and summary functions."""

from __future__ import annotations

from typing import Any

import pytest

from evals.router.metrics import GradeResult, grade_content, summarize_records


def test_exact_grader_normalizes_whitespace_and_case() -> None:
    assert grade_content(
        content="  STRASSE\n\tanswer  ",
        grader={"type": "exact", "answer": "strasse answer"},
    ) == GradeResult(score=1.0, contaminated=False)
    assert grade_content(
        content="answer with suffix",
        grader={"type": "exact", "answer": "answer"},
    ) == GradeResult(score=0.0, contaminated=False)


def test_contains_grader_scores_required_items_and_flags_forbidden_items() -> None:
    result = grade_content(
        content="alpha and gamma; never include secret",
        grader={
            "type": "contains",
            "required": ["alpha", "beta", "gamma"],
            "forbidden": ["secret", "private socket"],
        },
    )

    assert result.score == pytest.approx(2 / 3)
    assert result.contaminated is True


def test_contains_grader_without_required_items_has_full_score() -> None:
    assert grade_content(
        content="ordinary answer",
        grader={"type": "contains", "forbidden": []},
    ) == GradeResult(score=1.0, contaminated=False)


def test_manual_grader_has_no_score_but_still_checks_contamination() -> None:
    assert grade_content(
        content="response exposes candidate-1",
        grader={"type": "manual", "forbidden": ["candidate-1"]},
    ) == GradeResult(score=None, contaminated=True)
    assert grade_content(content="clean", grader={"type": "manual"}) == GradeResult(
        score=None,
        contaminated=False,
    )


@pytest.mark.parametrize(
    "grader",
    [
        None,
        {},
        {"type": "unknown"},
        {"type": "exact"},
        {"type": "exact", "answer": 42},
        {"type": "exact", "answer": "ok", "forbidden": []},
        {"type": "contains", "required": "answer"},
        {"type": "contains", "forbidden": [1]},
        {"type": "manual", "forbidden": ()},
        {"type": "manual", "required": []},
    ],
)
def test_invalid_grader_schema_raises_value_error(grader: object) -> None:
    with pytest.raises(ValueError):
        grade_content(content="answer", grader=grader)


def test_summarize_records_groups_and_aggregates_only_observed_values() -> None:
    records: list[dict[str, Any]] = [
        {
            "condition": "aggregation",
            "protocol_success": True,
            "score": 1.0,
            "clean_success": True,
            "contaminated": False,
            "ttft_ms": 100,
            "latency_ms": 1_000,
            "visible_input_tokens": 100,
            "visible_output_tokens": 10,
        },
        {
            "condition": "single",
            "protocol_success": False,
            "score": None,
            "clean_success": None,
            "contaminated": True,
            "ttft_ms": None,
            "latency_ms": None,
            "visible_input_tokens": None,
            "visible_output_tokens": None,
        },
        {
            "condition": "aggregation",
            "protocol_success": False,
            "score": 0.5,
            "clean_success": False,
            "contaminated": True,
            "ttft_ms": 200,
            "latency_ms": 1_200,
            "visible_input_tokens": 200,
            "visible_output_tokens": 20,
        },
        {
            "condition": "aggregation",
            "protocol_success": None,
            "score": None,
            "clean_success": None,
            "contaminated": False,
            "ttft_ms": None,
            "latency_ms": 1_400,
            "visible_input_tokens": 300,
            "visible_output_tokens": 30,
        },
    ]

    assert summarize_records(records) == [
        {
            "condition": "aggregation",
            "n": 3,
            "protocol_success_rate": 0.5,
            "mean_score": 0.75,
            "clean_success_rate": 0.5,
            "contamination_rate": pytest.approx(1 / 3),
            "ttft_ms_p50": 100.0,
            "ttft_ms_p95": 200.0,
            "latency_ms_p50": 1_200.0,
            "latency_ms_p95": 1_400.0,
            "visible_input_tokens": 600,
            "visible_output_tokens": 60,
        },
        {
            "condition": "single",
            "n": 1,
            "protocol_success_rate": 0.0,
            "mean_score": None,
            "clean_success_rate": None,
            "contamination_rate": 1.0,
            "ttft_ms_p50": None,
            "ttft_ms_p95": None,
            "latency_ms_p50": None,
            "latency_ms_p95": None,
            "visible_input_tokens": 0,
            "visible_output_tokens": 0,
        },
    ]


def test_summarize_records_uses_nearest_rank_percentiles() -> None:
    records = [
        {
            "condition": "condition-a",
            "ttft_ms": value,
            "latency_ms": value * 10,
        }
        for value in [40, 10, 30, 20]
    ]

    summary = summarize_records(records)[0]

    assert summary["ttft_ms_p50"] == 20
    assert summary["ttft_ms_p95"] == 40
    assert summary["latency_ms_p50"] == 200
    assert summary["latency_ms_p95"] == 400


def test_summarize_records_returns_empty_list_for_no_records() -> None:
    assert summarize_records([]) == []


@pytest.mark.parametrize(
    "records",
    [
        [{}],
        [{"condition": 1}],
        [{"condition": "x", "score": float("nan")}],
        [{"condition": "x", "ttft_ms": True}],
        [{"condition": "x", "visible_input_tokens": -1}],
    ],
)
def test_summarize_records_rejects_invalid_record_values(records: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        summarize_records(records)
