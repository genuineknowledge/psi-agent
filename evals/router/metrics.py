"""Pure grading and summary functions for Router evaluations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class GradeResult:
    """The automatic score and contamination result for one response."""

    score: float | None
    contaminated: bool


def grade_content(*, content: str, grader: object) -> GradeResult:
    """Grade response content against one validated grader definition."""

    if not isinstance(content, str):
        raise TypeError("content must be a string")
    if not isinstance(grader, dict):
        raise ValueError("grader must be an object")
    grader_dict = cast(dict[str, Any], grader)

    grader_type = grader_dict.get("type")
    if grader_type == "exact":
        _require_keys(grader=grader_dict, required={"type", "answer"}, optional=set())
        answer = grader_dict["answer"]
        if not isinstance(answer, str):
            raise ValueError("exact grader answer must be a string")
        score = float(_normalize_exact(content) == _normalize_exact(answer))
        return GradeResult(score=score, contaminated=False)

    if grader_type == "contains":
        _require_keys(
            grader=grader_dict,
            required={"type"},
            optional={"required", "forbidden"},
        )
        required = _string_list(grader=grader_dict, key="required")
        forbidden = _string_list(grader=grader_dict, key="forbidden")
        matched = sum(item in content for item in required)
        score = matched / len(required) if required else 1.0
        return GradeResult(
            score=score,
            contaminated=any(item in content for item in forbidden),
        )

    if grader_type == "manual":
        _require_keys(grader=grader_dict, required={"type"}, optional={"forbidden"})
        forbidden = _string_list(grader=grader_dict, key="forbidden")
        return GradeResult(
            score=None,
            contaminated=any(item in content for item in forbidden),
        )

    raise ValueError("grader type must be 'exact', 'contains', or 'manual'")


def summarize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize evaluation records by condition in first-seen order."""

    if not isinstance(records, list):
        raise ValueError("records must be a list")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("records must contain only objects")
        condition = record.get("condition")
        if not isinstance(condition, str):
            raise ValueError("each record condition must be a string")
        grouped.setdefault(condition, []).append(record)

    summaries: list[dict[str, Any]] = []
    for condition, group in grouped.items():
        scores = _optional_numbers(records=group, key="score")
        protocol_success = _bool_values(records=group, key="protocol_success")
        clean_success = _bool_values(records=group, key="clean_success")
        contaminated = _bool_values(records=group, key="contaminated")
        ttft_values = _optional_numbers(records=group, key="ttft_ms")
        latency_values = _optional_numbers(records=group, key="latency_ms")
        summaries.append(
            {
                "condition": condition,
                "n": len(group),
                "protocol_success_rate": _mean(protocol_success),
                "mean_score": _mean(scores),
                "clean_success_rate": _mean(clean_success),
                "contamination_rate": _mean(contaminated),
                "ttft_ms_p50": _nearest_rank(values=ttft_values, percentile=50),
                "ttft_ms_p95": _nearest_rank(values=ttft_values, percentile=95),
                "latency_ms_p50": _nearest_rank(values=latency_values, percentile=50),
                "latency_ms_p95": _nearest_rank(values=latency_values, percentile=95),
                "visible_input_tokens": _token_sum(records=group, key="visible_input_tokens"),
                "visible_output_tokens": _token_sum(records=group, key="visible_output_tokens"),
            }
        )
    return summaries


def _normalize_exact(value: str) -> str:
    return " ".join(value.split()).casefold()


def _require_keys(*, grader: dict[str, Any], required: set[str], optional: set[str]) -> None:
    keys = set(grader)
    if not required <= keys:
        missing = ", ".join(sorted(required - keys))
        raise ValueError(f"grader is missing required field(s): {missing}")
    unexpected = keys - required - optional
    if unexpected:
        names = ", ".join(sorted(repr(key) for key in unexpected))
        raise ValueError(f"grader contains unexpected field(s): {names}")


def _string_list(*, grader: dict[str, Any], key: str) -> list[str]:
    value = grader.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"grader {key} must be a list of strings")
    return cast(list[str], value)


def _bool_values(*, records: list[dict[str, Any]], key: str) -> list[bool]:
    return [value for record in records if isinstance((value := record.get(key)), bool)]


def _optional_numbers(*, records: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get(key)
        if value is None:
            continue
        if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"record {key} must be a finite number or None")
        values.append(float(value))
    return values


def _token_sum(*, records: list[dict[str, Any]], key: str) -> int:
    total = 0
    for record in records:
        value = record.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"record {key} must be a non-negative integer or None")
        total += value
    return total


def _mean(values: list[float] | list[bool]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _nearest_rank(*, values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[rank - 1]


__all__ = ["GradeResult", "grade_content", "summarize_records"]
