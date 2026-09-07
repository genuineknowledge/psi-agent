"""Pure structural validation for candidate cases."""

# ruff: noqa: RUF001

from __future__ import annotations

from _positive_negative_list.models import CaseDraft, allowed_evidence_sources


def is_negative_nature(nature: str) -> bool:
    return nature.casefold() == "negative"


def _identity_set(value: str) -> set[str]:
    return {part.strip() for part in value.replace("，", ",").split(",") if part.strip()}


def validate_case(case: CaseDraft) -> tuple[str, ...]:
    errors: list[str] = []
    for name in (
        "writer_user_key",
        "reporter_user_key",
        "subject_user_key",
        "occurred_at",
        "observed_behavior",
        "context",
        "impact",
        "nature",
        "category",
        "rule_version",
        "fact_summary",
    ):
        if not getattr(case, name).strip():
            errors.append(name)
    # Self-reporting is valid in the private-chat MVP (for example, a user
    # recording their own positive practice or a missed handoff).  The
    # notification layer already skips same-person notices, while the writer
    # identity is still required and bound to the trusted Feishu sender.
    if not case.evidence_sources or any(source not in allowed_evidence_sources() for source in case.evidence_sources):
        errors.append("evidence_sources")
    if case.nature not in {"positive", "negative", "neutral", "insufficient_evidence"}:
        errors.append("nature")
    if case.nature in {"positive", "negative"} and not case.primary_rule_id:
        errors.append("primary_rule_id")
    if is_negative_nature(case.nature):
        for name in ("correct_behavior", "immediate_remedy", "prevention"):
            if not getattr(case, name).strip():
                errors.append(name)
    # ``writing`` is a durable in-flight state.  A process can stop after the
    # state transition but before the public-table create call; confirmation
    # must be able to validate and resume that draft on the next callback.
    if case.workflow not in {"draft", "ready_for_confirmation", "writing", "confirmed", "possible_duplicate"}:
        errors.append("workflow")
    if case.red_line_candidate and case.workflow == "confirmed":
        errors.append("red_line_candidate.manual_review")
    return tuple(dict.fromkeys(errors))
