"""Validate the aggregate handoff from Feishu interview and talent-pool rows."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from typing import Any

_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_INTERVIEW_RECORD_ID = re.compile(r"^rec[A-Za-z0-9]{8,64}$")
_ASSESSMENT_REVISION = re.compile(r"^[0-9a-f]{64}$")
_HANDOFF_FIELDS = {
    "schema_version",
    "interview_record_id",
    "interview_table_id",
    "batch_id",
    "candidate_id",
    "candidate_name",
    "talent_record_id",
    "assessment_revision",
    "document_revisions",
    "matched_role",
    "assessment",
}


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_evidence(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty_text(item) for item in value)


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _load_handoff(
    value: dict[str, Any],
    index: int,
    workspace_root: str,
    interview_table_id: str,
) -> list[str]:
    prefix = f"interview_evidence_items[{index}]"
    record_id = value.get("interview_record_id")
    if not isinstance(record_id, str) or _INTERVIEW_RECORD_ID.fullmatch(record_id) is None:
        return []
    handoff_root = os.path.realpath(os.path.join(workspace_root, ".psi", "resume-approval", "interview-handoffs"))
    path = os.path.realpath(os.path.join(handoff_root, f"{record_id}.json"))
    if not _inside(handoff_root, workspace_root) or not _inside(path, handoff_root):
        return [f"{prefix} private handoff path is invalid"]
    try:
        with open(path, encoding="utf-8") as source:
            handoff = json.load(source)
    except OSError, UnicodeError, json.JSONDecodeError:
        return [f"{prefix} private handoff is missing or invalid"]
    if not isinstance(handoff, dict) or set(handoff) != _HANDOFF_FIELDS:
        return [f"{prefix} private handoff fields do not match schema 2.0"]
    errors: list[str] = []
    if handoff.get("schema_version") != "2.0":
        errors.append(f"{prefix} private handoff must use schema 2.0")
    if handoff.get("interview_table_id") != interview_table_id:
        errors.append(f"{prefix} private handoff interview table does not match configuration")
    for name in (
        "interview_record_id",
        "talent_record_id",
        "batch_id",
        "candidate_id",
        "candidate_name",
        "assessment_revision",
        "matched_role",
        "assessment",
    ):
        if value.get(name) != handoff.get(name):
            errors.append(f"{prefix}.{name} does not match the private handoff")
    return errors


def _final_interview_grade(scores: list[int], weighted_total: float) -> str:
    if weighted_total >= 4.5:
        grade = "S"
    elif weighted_total >= 3.5:
        grade = "A"
    elif weighted_total >= 2.5:
        grade = "B"
    else:
        grade = "C"
    if min(scores) <= 2:
        grade = {"S": "A", "A": "B", "B": "C", "C": "C"}[grade]
    if scores[3] <= 2 and scores[1] <= 2:
        grade = "C"
    return grade


def _with_interview_revision(value: dict[str, Any]) -> dict[str, Any]:
    canonical_value = {key: item for key, item in value.items() if key != "interview_revision"}
    canonical = json.dumps(
        canonical_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {**canonical_value, "interview_revision": hashlib.sha256(canonical).hexdigest()[:16]}


def _validate_interview_scoring(value: Any, prefix: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix}.interview_scoring must be an object"]
    errors: list[str] = []
    scores: list[int] = []
    for name in ("reliability", "professional", "learning_action", "ai_native"):
        item = value.get(name)
        score = item.get("score") if isinstance(item, dict) else None
        evidence = item.get("evidence") if isinstance(item, dict) else None
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
            errors.append(f"{prefix}.interview_scoring.{name}.score must be an integer from 1 to 5")
        else:
            scores.append(score)
        if not _text_evidence(evidence):
            errors.append(f"{prefix}.interview_scoring.{name}.evidence must be non-empty text evidence")
    weighted_total = value.get("weighted_total")
    if len(scores) == 4:
        expected_total = round(scores[0] * 0.3 + scores[1] * 0.3 + scores[2] * 0.2 + scores[3] * 0.2, 2)
        if (
            not isinstance(weighted_total, int | float)
            or isinstance(weighted_total, bool)
            or not math.isfinite(weighted_total)
            or abs(float(weighted_total) - expected_total) > 1e-9
        ):
            errors.append(f"{prefix}.interview_scoring.weighted_total must equal {expected_total}")
        elif value.get("grade") != _final_interview_grade(scores, expected_total):
            errors.append(f"{prefix}.interview_scoring.grade violates the formal grade/downgrade rules")
    if value.get("smart_level") not in {"T1", "T2", "T3", "T4", "T5"}:
        errors.append(f"{prefix}.interview_scoring.smart_level must be T1-T5")
    if not _text_evidence(value.get("smart_level_evidence")):
        errors.append(f"{prefix}.interview_scoring.smart_level_evidence must be non-empty")
    if not isinstance(value.get("open_questions"), list) or not all(
        _nonempty_text(item) for item in value["open_questions"]
    ):
        errors.append(f"{prefix}.interview_scoring.open_questions must be a text list")
    return errors


def _validate_item(value: Any, index: int) -> list[str]:
    prefix = f"interview_evidence_items[{index}]"
    if not isinstance(value, dict):
        return [f"{prefix} must be a structured object"]
    errors: list[str] = []
    if value.get("schema_version") != "2.0" or value.get("status") != "complete":
        errors.append(f"{prefix} must be a complete schema 2.0 object")
    for name in (
        "interview_record_id",
        "talent_record_id",
        "batch_id",
        "candidate_name",
    ):
        if not _nonempty_text(value.get(name)):
            errors.append(f"{prefix}.{name} must be non-empty text")
    candidate_id = value.get("candidate_id")
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        errors.append(f"{prefix}.candidate_id must be 16 lowercase hexadecimal characters")
    interview_record_id = value.get("interview_record_id")
    if not isinstance(interview_record_id, str) or _INTERVIEW_RECORD_ID.fullmatch(interview_record_id) is None:
        errors.append(f"{prefix}.interview_record_id is invalid")
    matched_role = value.get("matched_role")
    assessment = value.get("assessment")
    if (
        not isinstance(assessment, dict)
        or assessment.get("schema_version") != "3.0"
        or assessment.get("status") != "assessed"
        or assessment.get("candidate_id") != candidate_id
    ):
        errors.append(f"{prefix}.assessment is not the matching sanitized assessment")
    assessment_revision = assessment.get("assessment_revision") if isinstance(assessment, dict) else None
    if not isinstance(assessment_revision, str) or _ASSESSMENT_REVISION.fullmatch(assessment_revision) is None:
        errors.append(f"{prefix}.assessment.assessment_revision is invalid")
    if value.get("assessment_revision") != assessment_revision:
        errors.append(f"{prefix}.assessment_revision does not match the stored assessment")
    if isinstance(assessment, dict):
        for name in ("batch_id", "candidate_name"):
            if value.get(name) != assessment.get(name):
                errors.append(f"{prefix}.{name} does not match the stored assessment")
    if not isinstance(matched_role, dict):
        errors.append(f"{prefix}.matched_role must be an object")
    else:
        hard_requirements = matched_role.get("hard_requirements")
        if (
            not _nonempty_text(matched_role.get("role_key"))
            or not _nonempty_text(matched_role.get("name"))
            or matched_role.get("status") != "active"
            or not isinstance(hard_requirements, list)
            or not all(_nonempty_text(item) for item in hard_requirements)
        ):
            errors.append(f"{prefix}.matched_role is not a complete active role contract")
        elif isinstance(assessment, dict) and (
            assessment.get("matched_role_key") != matched_role.get("role_key")
            or assessment.get("matched_role_name") != matched_role.get("name")
        ):
            errors.append(f"{prefix}.matched_role does not match the stored assessment")
    if not any(_nonempty_text(value.get(name)) for name in ("notes", "supplement", "evidence")):
        errors.append(f"{prefix} has no Human interview notes, supplement, or evidence")
    for name in ("notes", "supplement", "evidence", "risk_verification", "interview_conclusion"):
        if not isinstance(value.get(name), str):
            errors.append(f"{prefix}.{name} must be text")
    errors.extend(_validate_interview_scoring(value.get("interview_scoring"), prefix))
    return errors


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    values = _decode(inputs.get("interview_evidence_items"))
    requested_values = _decode(inputs.get("interview_record_ids"))
    errors: list[str] = []
    feishu_config = _decode(inputs.get("feishu_config"))
    interview_table_id = feishu_config.get("interview_table_id") if isinstance(feishu_config, dict) else None
    if not _nonempty_text(interview_table_id):
        errors.append("feishu_config.interview_table_id must be non-empty text")
        interview_table_id = ""
    if not isinstance(values, list) or not values:
        errors.append("interview_evidence_items must be a non-empty list")
        values = []
    decoded = [_decode(value) for value in values]
    requested_ids: set[str] = set()
    if not isinstance(requested_values, list) or not requested_values:
        errors.append("interview_record_ids must be a non-empty list")
        requested_values = []
    for index, record_id in enumerate(requested_values):
        if not isinstance(record_id, str) or _INTERVIEW_RECORD_ID.fullmatch(record_id) is None:
            errors.append(f"interview_record_ids[{index}] is invalid")
        elif record_id in requested_ids:
            errors.append(f"interview_record_ids[{index}] is duplicated")
        else:
            requested_ids.add(record_id)
    record_ids: set[str] = set()
    for index, value in enumerate(decoded):
        errors.extend(_validate_item(value, index))
        if isinstance(value, dict) and interview_table_id:
            errors.extend(_load_handoff(value, index, resolved_workspace, interview_table_id))
        if isinstance(value, dict) and isinstance(value.get("interview_record_id"), str):
            record_id = value["interview_record_id"]
            if record_id in record_ids:
                errors.append(f"interview_evidence_items[{index}].interview_record_id is duplicated")
            record_ids.add(record_id)
    missing_ids = sorted(requested_ids - record_ids)
    unexpected_ids = sorted(record_ids - requested_ids)
    if missing_ids:
        errors.append(f"interview_evidence_items are missing requested ids: {', '.join(missing_ids)}")
    if unexpected_ids:
        errors.append(f"interview_evidence_items contain unrequested ids: {', '.join(unexpected_ids)}")
    validated_items = [] if errors else [_with_interview_revision(value) for value in decoded]
    return {
        "validated_interview_items": validated_items,
        "interview_validation_manifest": {
            "schema_version": "2.0",
            "status": "blocked" if errors else "complete",
            "expected_count": len(requested_values),
            "revisions": [
                {
                    "interview_record_id": value["interview_record_id"],
                    "interview_revision": value["interview_revision"],
                }
                for value in validated_items
            ],
            "errors": errors,
        },
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stdout.write(json.dumps(run(_load_inputs()), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
