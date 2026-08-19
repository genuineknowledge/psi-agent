"""Validate evidence-based hiring conclusions before any final Feishu write."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter
from typing import Any

_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_REVISION = re.compile(r"^[0-9a-f]{16}$")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY_NUMBER = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_FORBIDDEN_PII_KEYS = {
    "age",
    "email",
    "ethnicity",
    "gender",
    "health",
    "id_number",
    "identity_number",
    "marital_status",
    "mobile",
    "phone",
    "photo",
    "religion",
    "邮箱",
    "手机号",
    "电话",
    "身份证",
    "年龄",
    "性别",
    "婚姻",
    "民族",
    "宗教",
    "健康",
    "照片",
}
_PERFORMANCE_BANDS = {"S": "远超预期", "A": "符合预期", "B": "低于预期", "C": "明显不足"}
_MATRIX = {
    "S": {"A": "直接录用", "B": "直接录用", "C": "强烈推荐", "D": "推荐录用", "E": "重新评估", "F": "重新评估"},
    "A": {"A": "直接录用", "B": "推荐录用", "C": "可录用", "D": "待定", "E": "不推荐", "F": "不推荐"},
    "B": {"A": "推荐录用", "B": "可录用", "C": "待定", "D": "不推荐", "E": "淘汰", "F": "淘汰"},
    "C": {"A": "待定", "B": "待定", "C": "不推荐", "D": "淘汰", "E": "淘汰", "F": "淘汰"},
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
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _text_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _privacy_violations(value: Any, path: str = "hiring_conclusion") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in _FORBIDDEN_PII_KEYS:
                violations.append(f"{child_path} is a forbidden personal-data field")
            violations.extend(_privacy_violations(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            violations.extend(_privacy_violations(item, f"{path}[{index}]"))
    elif isinstance(value, str) and (
        _EMAIL.search(value) is not None
        or _PHONE.search(value) is not None
        or _IDENTITY_NUMBER.search(value) is not None
    ):
        violations.append(f"{path} contains disallowed contact or identity data")
    return violations


def _requirement_matrix(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("requirement"), str)
        and bool(item["requirement"].strip())
        and item.get("result") in {"pass", "fail", "unknown"}
        and _text_list(item.get("evidence"))
        and item.get("source") in {"resume", "interview", "both"}
        for item in value
    )


def _hire_reasons(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("reason"), str)
        and bool(item["reason"].strip())
        and _text_list(item.get("evidence"))
        and bool(item["evidence"])
        and item.get("source") in {"resume", "interview", "both"}
        for item in value
    )


def _risk_closure(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("risk"), str)
        and bool(item["risk"].strip())
        and item.get("status") in {"resolved", "unresolved", "confirmed"}
        and _text_list(item.get("evidence"))
        for item in value
    )


def _valid_interview_scoring(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    scores: list[int] = []
    for name in ("reliability", "professional", "learning_action", "ai_native"):
        item = value.get(name)
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("score"), int)
            or isinstance(item.get("score"), bool)
            or not 1 <= item["score"] <= 5
            or not _text_list(item.get("evidence"))
            or not item["evidence"]
        ):
            return False
        scores.append(item["score"])
    weighted = round(scores[0] * 0.3 + scores[1] * 0.3 + scores[2] * 0.2 + scores[3] * 0.2, 2)
    stored = value.get("weighted_total")
    if (
        not isinstance(stored, int | float)
        or isinstance(stored, bool)
        or not math.isfinite(stored)
        or abs(float(stored) - weighted) > 1e-9
    ):
        return False
    if weighted >= 4.5:
        grade = "S"
    elif weighted >= 3.5:
        grade = "A"
    elif weighted >= 2.5:
        grade = "B"
    else:
        grade = "C"
    if min(scores) <= 2:
        grade = {"S": "A", "A": "B", "B": "C", "C": "C"}[grade]
    if scores[3] <= 2 and scores[1] <= 2:
        grade = "C"
    return (
        value.get("grade") == grade
        and value.get("smart_level") in {"T1", "T2", "T3", "T4", "T5"}
        and _text_list(value.get("smart_level_evidence"))
        and bool(value["smart_level_evidence"])
        and _text_list(value.get("open_questions"))
    )


def _valid_decision_matrix(value: Any, assessment: Any, interview_scoring: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(assessment, dict) or not isinstance(interview_scoring, dict):
        return False
    resume_grade = assessment.get("grade")
    interview_grade = interview_scoring.get("grade")
    return (
        resume_grade in {"A", "B", "C", "D", "E", "F"}
        and interview_grade in _PERFORMANCE_BANDS
        and value.get("resume_grade") == resume_grade
        and value.get("interview_grade") == interview_grade
        and value.get("performance_band") == _PERFORMANCE_BANDS[interview_grade]
        and value.get("matrix_result") == _MATRIX[interview_grade][resume_grade]
    )


def _validate(value: Any, index: int) -> list[str]:
    prefix = f"hiring_conclusions[{index}]"
    if not isinstance(value, dict):
        return [f"{prefix} must be a structured object"]
    errors: list[str] = []
    errors.extend(_privacy_violations(value, prefix))
    if value.get("schema_version") != "2.0" or value.get("status") != "concluded":
        errors.append(f"{prefix} must be a concluded schema 2.0 object")
    candidate_id = value.get("candidate_id")
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        errors.append(f"{prefix}.candidate_id is invalid")
    interview_revision = value.get("interview_revision")
    if not isinstance(interview_revision, str) or _REVISION.fullmatch(interview_revision) is None:
        errors.append(f"{prefix}.interview_revision is invalid")
    for name in ("candidate_name", "interview_record_id", "talent_record_id", "interview_summary"):
        if not isinstance(value.get(name), str) or not value[name].strip():
            errors.append(f"{prefix}.{name} must be non-empty text")
    if value.get("recommendation") not in {"hire", "no_hire", "hold"}:
        errors.append(f"{prefix}.recommendation is invalid")
    requirement_matrix = value.get("requirement_evidence_matrix")
    if not _requirement_matrix(requirement_matrix):
        errors.append(f"{prefix}.requirement_evidence_matrix is invalid")
    if not _hire_reasons(value.get("hire_reasons")):
        errors.append(f"{prefix}.hire_reasons is invalid")
    if value.get("recommendation") == "hire" and not value.get("hire_reasons"):
        errors.append(f"{prefix}.hire_reasons must be non-empty for hire")
    if value.get("recommendation") == "hire" and not requirement_matrix:
        errors.append(f"{prefix}.requirement_evidence_matrix must be non-empty for hire")
    if (
        value.get("recommendation") == "hire"
        and isinstance(requirement_matrix, list)
        and any(not isinstance(item, dict) or item.get("result") != "pass" for item in requirement_matrix)
    ):
        errors.append(f"{prefix} cannot recommend hire unless every requirement passes")
    if (
        value.get("recommendation") == "hire"
        and isinstance(requirement_matrix, list)
        and any(not isinstance(item, dict) or not item.get("evidence") for item in requirement_matrix)
    ):
        errors.append(f"{prefix} cannot recommend hire without evidence for every requirement")
    if not _risk_closure(value.get("risk_closure")):
        errors.append(f"{prefix}.risk_closure is invalid")
    if (
        value.get("recommendation") == "hire"
        and isinstance(value.get("risk_closure"), list)
        and any(not isinstance(item, dict) or item.get("status") != "resolved" for item in value["risk_closure"])
    ):
        errors.append(f"{prefix} cannot recommend hire with unresolved or confirmed risks")
    if not _text_list(value.get("remaining_unknowns")):
        errors.append(f"{prefix}.remaining_unknowns must be a text list")
    confidence = value.get("confidence")
    if (
        not isinstance(confidence, int | float)
        or isinstance(confidence, bool)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        errors.append(f"{prefix}.confidence must be finite and between 0 and 1")
    if not isinstance(value.get("assessment"), dict):
        errors.append(f"{prefix}.assessment must be preserved")
    if not isinstance(value.get("matched_role"), dict):
        errors.append(f"{prefix}.matched_role must be preserved")
    interview_scoring = value.get("interview_scoring")
    if not _valid_interview_scoring(interview_scoring):
        errors.append(f"{prefix}.interview_scoring is invalid")
    decision_matrix = value.get("decision_matrix")
    if not _valid_decision_matrix(decision_matrix, value.get("assessment"), interview_scoring):
        errors.append(f"{prefix}.decision_matrix does not match the formal matrix")
    if (
        value.get("recommendation") == "hire"
        and isinstance(decision_matrix, dict)
        and decision_matrix.get("matrix_result") in {"待定", "重新评估", "不推荐", "淘汰"}
    ):
        errors.append(f"{prefix} cannot recommend hire against a non-positive formal matrix result")
    return errors


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    del workspace_root
    values = _decode(inputs.get("hiring_conclusions"))
    errors: list[str] = []
    if not isinstance(values, list) or not values:
        errors.append("hiring_conclusions must be a non-empty list")
        values = []
    decoded = [_decode(value) for value in values]
    evidence_values = _decode(inputs.get("validated_interview_items"))
    evidence_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(evidence_values, list) or not evidence_values:
        errors.append("validated_interview_items must be a non-empty list")
    else:
        for item in evidence_values:
            decoded_item = _decode(item)
            record_id = decoded_item.get("interview_record_id") if isinstance(decoded_item, dict) else None
            if not isinstance(record_id, str) or record_id in evidence_by_id:
                errors.append("validated_interview_items contain an invalid or duplicate interview record id")
            else:
                evidence_by_id[record_id] = decoded_item
    candidates: set[str] = set()
    conclusion_record_ids: set[str] = set()
    for index, value in enumerate(decoded):
        errors.extend(_validate(value, index))
        if isinstance(value, dict):
            interview_record_id = value.get("interview_record_id")
            source_item = evidence_by_id.get(interview_record_id)
            if source_item is None:
                errors.append(f"hiring_conclusions[{index}] has no matching validated interview item")
            else:
                if (
                    value.get("interview_scoring") != source_item.get("interview_scoring")
                    or value.get("assessment") != source_item.get("assessment")
                    or value.get("matched_role") != source_item.get("matched_role")
                ):
                    errors.append(f"hiring_conclusions[{index}] did not preserve validated evidence")
                for name in (
                    "candidate_id",
                    "candidate_name",
                    "talent_record_id",
                    "interview_revision",
                ):
                    if value.get(name) != source_item.get(name):
                        errors.append(f"hiring_conclusions[{index}].{name} does not match validated interview evidence")
                matched_role = source_item.get("matched_role")
                hard_requirements = matched_role.get("hard_requirements") if isinstance(matched_role, dict) else None
                expected_requirements: Counter[str] = (
                    Counter(item for item in hard_requirements if isinstance(item, str))
                    if isinstance(hard_requirements, list)
                    else Counter()
                )
                matrix = value.get("requirement_evidence_matrix")
                actual_requirements: Counter[str] = (
                    Counter(
                        item.get("requirement")
                        for item in matrix
                        if isinstance(item, dict) and isinstance(item.get("requirement"), str)
                    )
                    if isinstance(matrix, list)
                    else Counter()
                )
                if actual_requirements != expected_requirements:
                    errors.append(
                        f"hiring_conclusions[{index}].requirement_evidence_matrix must cover "
                        "every assessed hard requirement exactly once"
                    )
            if isinstance(interview_record_id, str):
                if interview_record_id in conclusion_record_ids:
                    errors.append(f"hiring_conclusions[{index}].interview_record_id is duplicated")
                conclusion_record_ids.add(interview_record_id)
        if isinstance(value, dict) and isinstance(value.get("candidate_id"), str):
            candidate_id = value["candidate_id"]
            if candidate_id in candidates:
                errors.append(f"hiring_conclusions[{index}].candidate_id is duplicated")
            candidates.add(candidate_id)
    missing_ids = sorted(set(evidence_by_id) - conclusion_record_ids)
    unexpected_ids = sorted(conclusion_record_ids - set(evidence_by_id))
    if missing_ids:
        errors.append(f"hiring_conclusions are missing validated interviews: {', '.join(missing_ids)}")
    if unexpected_ids:
        errors.append(f"hiring_conclusions contain unvalidated interviews: {', '.join(unexpected_ids)}")
    conclusion_revision = ""
    if not errors:
        canonical = json.dumps(
            sorted(decoded, key=lambda item: item["interview_record_id"]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        conclusion_revision = hashlib.sha256(canonical).hexdigest()[:16]
    return {
        "schema_version": "2.0",
        "status": "blocked" if errors else "complete",
        "conclusions": [] if errors else decoded,
        "conclusion_revision": conclusion_revision,
        "errors": errors,
    }


def _program_outputs(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "validated_hiring_conclusions": result,
        "hiring_validation_manifest": {
            "schema_version": "1.0",
            "status": result["status"],
            "conclusion_count": len(result["conclusions"]),
            "conclusion_revision": result["conclusion_revision"],
            "error_count": len(result["errors"]),
        },
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    result = run(_load_inputs())
    sys.stdout.write(json.dumps(_program_outputs(result), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
