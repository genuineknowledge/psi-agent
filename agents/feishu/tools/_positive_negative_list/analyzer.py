"""Deterministic, read-only aggregation for positive-negative ledger records."""

# ruff: noqa: RUF001

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from _positive_negative_list.models import LedgerRecord


def _counts(values: Sequence[str]) -> dict[str, int]:
    return dict(sorted(Counter(value for value in values if value).items()))


def _identity_parts(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.replace("，", ",").split(",") if part.strip())


def _person_counts(values: Sequence[str]) -> dict[str, int]:
    identities = (identity for value in values for identity in _identity_parts(value))
    return _counts(tuple(identities))


def _duplicate_candidates(records: Sequence[LedgerRecord]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in records:
        if record.case_id:
            groups[("exact_case_id", record.case_id)].append(record.record_id)
        if record.source_key:
            groups[("exact_source_key", record.source_key)].append(record.record_id)
        if record.canonical_incident_id:
            groups[("exact_canonical_incident_id", record.canonical_incident_id)].append(record.record_id)
        if record.cross_source_fingerprint:
            groups[("possible_cross_source", record.cross_source_fingerprint)].append(record.record_id)
    result: list[dict[str, Any]] = []
    for (kind, key), record_ids in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        unique_ids = sorted(set(record_ids))
        if len(unique_ids) > 1:
            result.append({"kind": kind, "key": key, "record_ids": unique_ids})
    return result


def analyze_records(records: Sequence[LedgerRecord], focus: str = "") -> dict[str, Any]:
    ordered = sorted(records, key=lambda record: (record.occurred_at, record.record_id))
    evidence_gaps = sorted(
        record.record_id
        for record in ordered
        if (
            not record.record_id
            or not record.subject_user_key
            or not record.occurred_at
            or not record.fact_summary
            or not (record.observed_behavior or record.fact_summary)
            or not record.context
            or not record.evidence_sources
            or (
                record.nature == "negative"
                and (
                    not record.correct_behavior.strip()
                    or not record.immediate_remedy.strip()
                    or not record.prevention.strip()
                )
            )
        )
    )
    review_gaps = sorted(
        record.record_id for record in ordered if record.nature == "negative" and not record.review_status.strip()
    )
    attention_ids = set(evidence_gaps) | set(review_gaps)
    attention_records = [record.to_mapping() for record in ordered if record.record_id in attention_ids]
    return {
        "count": len(ordered),
        "nature_counts": _counts([record.nature for record in ordered]),
        "category_counts": _counts([record.category for record in ordered]),
        "subject_counts": _person_counts([record.subject_user_key for record in ordered]),
        "reporter_counts": _person_counts([record.reporter_user_key for record in ordered]),
        "time_counts": _counts([record.occurred_at for record in ordered]),
        "duplicate_candidates": _duplicate_candidates(ordered),
        "evidence_gaps": evidence_gaps,
        "review_gaps": review_gaps,
        "attention_records": attention_records,
        "focus": focus,
        "non_performance_statement": "本分析仅用于理解记录和后续沟通，不产生分数、排名或绩效结论。",
    }


def user_summary(records: Sequence[LedgerRecord], focus: str = "") -> dict[str, Any]:
    """Return the stable, human-facing shape exposed by the analysis tool.

    The aggregation above intentionally keeps implementation data for callers
    inside this package.  Tool results are consumed directly by the language
    model, so exposing that mapping would invite it to repeat rule IDs,
    pagination cursors, or storage field names.  This projection contains
    only Chinese business labels and counts.
    """
    internal = analyze_records(records, focus)
    nature_counts = internal["nature_counts"]
    category_counts = internal["category_counts"]
    attention_records = internal["attention_records"]
    attention: list[dict[str, str]] = []
    for item in attention_records:
        issues: list[str] = []
        record_id = str(item.get("record_id") or "")
        if record_id in set(internal["evidence_gaps"]):
            issues.append("证据或事件要素未补齐")
        if record_id in set(internal["review_gaps"]):
            issues.append("负面行为尚未完成复盘")
        attention.append({"记录编号": record_id, "需要补充": "；".join(issues)})
    return {
        "摘要": {
            "记录总数": internal["count"],
            "正面记录数": nature_counts.get("positive", 0),
            "负面记录数": nature_counts.get("negative", 0),
            "中性记录数": nature_counts.get("neutral", 0),
            "证据不足记录数": nature_counts.get("insufficient_evidence", 0),
            "证据来源未填写": sum(1 for record in records if not record.evidence_sources),
            "负面记录待复盘": len(internal["review_gaps"]),
            "疑似重复记录数": len(internal["duplicate_candidates"]),
        },
        "分类统计": [{"分类": category, "记录数": count} for category, count in sorted(category_counts.items())],
        "需要关注的记录": attention,
        "分析范围": focus,
        "说明": "本分析仅用于理解记录和后续沟通，不产生分数、排名或绩效结论。",
    }


__all__ = ["analyze_records", "user_summary"]
