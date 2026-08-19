"""Build bounded dynamic-assessment repairs and merge without changing authority identity."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from typing import Any

import validate_candidate_assessments as validator

_INDEXED_ERROR = re.compile(r"^candidate_assessments\[(?P<index>\d+)\](?P<suffix>.*)$")
_IMMUTABLE_ERROR_PARTS = (
    ".source.sha256",
    ".candidate_id",
    ".batch_id",
    ".document_revisions.",
    ".matched_role_key",
    " is duplicated",
)


def load_inputs() -> dict[str, Any]:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("inputs"), dict):
        raise TypeError("Program stdin must contain an inputs object")
    return payload["inputs"]


def write_outputs(outputs: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    json.dump(outputs, sys.stdout, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _safe_active_roles(role_catalog: Any) -> list[dict[str, Any]]:
    if not isinstance(role_catalog, dict) or not isinstance(role_catalog.get("roles"), list):
        return []
    result: list[dict[str, Any]] = []
    for role in role_catalog["roles"]:
        if not isinstance(role, dict) or role.get("status") != "active":
            continue
        result.append(
            {
                "role_key": role.get("role_key"),
                "name": role.get("name"),
                "responsibilities": deepcopy(role.get("responsibilities", [])),
                "hard_requirements": deepcopy(role.get("hard_requirements", [])),
                "preferences": deepcopy(role.get("preferences", [])),
            }
        )
    return result


def _expected_contract(inputs: dict[str, Any]) -> dict[str, Any]:
    scoring, errors = validator.parse_scoring_contract(inputs.get("reference_documents"))
    if errors:
        raise ValueError("cannot build a repair contract from invalid scoring authorities")
    return {
        "schema_version": "3.0",
        "required_fields": sorted(validator._ASSESSMENT_FIELDS),
        "forbidden_legacy_fields": [
            "target_role",
            "standard_versions",
            "candidate_profile",
            "role_match",
            "scoring_profile",
            "weights_used",
            "resume_scores",
            "capability_model",
            "ability_level",
            "strengths",
            "risks",
            "interview_plan",
            "model_recommendation",
        ],
        "scoring_rules": scoring,
        "recommendation_rules": {
            "eligible_grades": list(validator._INTERVIEW_RECOMMENDATION_GRADES),
            "positive_hard_requirement_evidence": "required for every selected-role hard requirement",
            "hard_requirement_mismatch": "forces 不建议面试",
            "other_grades": "C、D、E、F default to 不建议面试; Human may override through 初审状态",
        },
        "education_rules": {
            "education_levels": list(validator._EDUCATION_LEVELS),
            "single_institution_format": "本科\uff1a院校名称",
            "multiple_institutions_format": "本科\uff1a院校名称\uff1b硕士\uff1a院校名称",
            "institution_stages": list(validator._EDUCATION_STAGES),
            "institution_content": "仅限院校名称\uff1b不含专业、届别、学历、排名标签或个人背景摘要",
        },
        "resume_summary_rules": {
            "type": "array",
            "min_items": 1,
            "max_items": 5,
            "item_format": "non-empty string starting with '- '",
            "content": "resume-supported strengths only",
            "example": ["- 独立交付生产级系统", "- 核心指标有量化提升"],
        },
        "point_rules": {
            "type": "array of {requirement, resume_evidence} objects",
            "min_items_each": 1,
            "match_content": "resume-supported role matches",
            "mismatch_content": "resume-supported shortfalls, contradictions, or material evidence gaps",
            "table_format": "one '- ' bullet line per point",
        },
        "verification_question_rules": {
            "type": "array of {question, category, evidence_anchor, purpose, positive_signal, risk_signal} objects",
            "min_items": 3,
            "max_items": 6,
            "categories": list(validator._QUESTION_CATEGORIES),
            "required_coverage": ["真实性核验", "岗位匹配", "风险澄清 when mismatch_points is non-empty"],
            "evidence_anchor": (
                "exact selected-role requirement or exact resume evidence from match_points/mismatch_points"
            ),
            "public_rendering": "<index>. [<category>] <question> in source order",
            "safety": "exclude protected attributes and unsupported negative claims",
        },
        "active_roles": _safe_active_roles(inputs.get("role_catalog")),
        "recommendations": ["建议面试", "不建议面试"],
    }


def _error_payload(message: str, match: re.Match[str]) -> dict[str, str]:
    suffix = match.group("suffix").lstrip(".")
    path = suffix.split(" ", maxsplit=1)[0] if suffix else ""
    return {"path": path, "message": message}


def _immutable_identity(assessment: dict[str, Any]) -> dict[str, Any] | None:
    source = assessment.get("source")
    revisions = assessment.get("document_revisions")
    source_sha = source.get("sha256") if isinstance(source, dict) else None
    values = {
        "batch_id": assessment.get("batch_id"),
        "candidate_id": assessment.get("candidate_id"),
        "source_sha256": source_sha,
        "document_revisions": deepcopy(revisions),
        "matched_role_key": assessment.get("matched_role_key"),
    }
    if (
        not isinstance(values["batch_id"], str)
        or not isinstance(values["candidate_id"], str)
        or not isinstance(source_sha, str)
        or validator._SHA256.fullmatch(source_sha) is None
        or not isinstance(revisions, dict)
        or not isinstance(values["matched_role_key"], str)
    ):
        return None
    return values


def build_repair_requests(
    inputs: dict[str, Any],
    *,
    assessments_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assessments = inputs.get(assessments_key)
    if not isinstance(assessments, list):
        raise TypeError(f"{assessments_key} must be a list")
    validation = validator.run(
        {
            "candidate_assessments": assessments,
            "reference_documents": inputs.get("reference_documents"),
            "role_catalog": inputs.get("role_catalog"),
            "batch_id": inputs.get("batch_id"),
        }
    )
    indexed_errors: dict[int, list[dict[str, str]]] = {}
    unrepairable_errors: list[str] = []
    for message in validation["errors"]:
        match = _INDEXED_ERROR.match(message)
        if match is None or any(part in message for part in _IMMUTABLE_ERROR_PARTS):
            unrepairable_errors.append(message)
            continue
        index = int(match.group("index"))
        if index >= len(assessments):
            unrepairable_errors.append(message)
            continue
        indexed_errors.setdefault(index, []).append(_error_payload(message, match))

    expected = (
        _expected_contract(inputs) if not any(not _INDEXED_ERROR.match(e) for e in validation["errors"]) else None
    )
    requests: list[dict[str, Any]] = []
    repairable_error_count = 0
    for index, candidate_errors in sorted(indexed_errors.items()):
        assessment = validator._normalize_assessment(assessments[index])
        if not isinstance(assessment, dict) or assessment.get("status") != "assessed":
            unrepairable_errors.extend(item["message"] for item in candidate_errors)
            continue
        immutable = _immutable_identity(assessment)
        if immutable is None or expected is None:
            unrepairable_errors.extend(item["message"] for item in candidate_errors)
            continue
        repairable_error_count += len(candidate_errors)
        requests.append(
            {
                "schema_version": "2.0",
                "candidate_index": index,
                "immutable_identity": immutable,
                "original_assessment": deepcopy(assessment),
                "validation_errors": candidate_errors,
                "expected_contract": deepcopy(expected),
            }
        )

    error_count = len(validation["errors"])
    if not error_count:
        status = "complete"
    elif requests and unrepairable_errors:
        status = "partially_repairable"
    elif requests:
        status = "repair_required"
    else:
        status = "blocked"
    return requests, {
        "schema_version": "2.0",
        "status": status,
        "assessment_count": len(assessments),
        "validation_error_count": error_count,
        "repair_request_count": len(requests),
        "repairable_error_count": repairable_error_count,
        "unrepairable_error_count": len(unrepairable_errors),
        "unrepairable_errors": unrepairable_errors,
    }


def _assert_repair_identity(request: dict[str, Any], original: dict[str, Any], repaired: dict[str, Any]) -> None:
    immutable = request.get("immutable_identity")
    if not isinstance(immutable, dict):
        raise ValueError("repair request immutable_identity is invalid")
    repaired_source = repaired.get("source")
    original_source = original.get("source")
    if repaired_source != original_source:
        raise ValueError("repair must preserve source.sha256 and all source metadata")
    if repaired.get("batch_id") != immutable.get("batch_id"):
        raise ValueError("repair must preserve batch_id")
    if repaired.get("candidate_id") != immutable.get("candidate_id"):
        raise ValueError("repair must preserve candidate_id")
    if repaired.get("document_revisions") != immutable.get("document_revisions"):
        raise ValueError("repair must preserve document_revisions")
    if repaired.get("matched_role_key") != immutable.get("matched_role_key"):
        raise ValueError("repair must preserve matched_role_key")
    if repaired.get("candidate_name") != original.get("candidate_name"):
        raise ValueError("repair must preserve candidate_name")


def _assert_question_only_repair_scope(
    request: dict[str, Any], original: dict[str, Any], repaired: dict[str, Any]
) -> None:
    diagnostics = request.get("validation_errors")
    if not isinstance(diagnostics, list) or not diagnostics:
        return
    paths = [item.get("path") for item in diagnostics if isinstance(item, dict)]
    if len(paths) != len(diagnostics) or not all(
        isinstance(path, str)
        and (
            path == "verification_questions"
            or path == "missing_fields:verification_questions"
            or path.startswith("verification_questions[")
        )
        for path in paths
    ):
        return
    original_without_questions = {key: value for key, value in original.items() if key != "verification_questions"}
    repaired_without_questions = {key: value for key, value in repaired.items() if key != "verification_questions"}
    if repaired_without_questions != original_without_questions:
        raise ValueError("question-only repair must preserve every unrelated assessment field")


def merge_repairs(
    inputs: dict[str, Any],
    *,
    assessments_key: str,
    requests_key: str,
    repairs_key: str,
) -> tuple[list[Any], dict[str, Any]]:
    assessments = inputs.get(assessments_key)
    requests = inputs.get(requests_key)
    repairs = inputs.get(repairs_key)
    if not isinstance(assessments, list):
        raise TypeError(f"{assessments_key} must be a list")
    if not isinstance(requests, list) or not isinstance(repairs, list):
        raise TypeError("repair requests and results must be lists")
    if len(requests) != len(repairs):
        raise ValueError("repair count must equal repair request count")

    merged = deepcopy(assessments)
    modified_indices: list[int] = []
    seen_indices: set[int] = set()
    for request, raw_repaired in zip(requests, repairs, strict=True):
        if not isinstance(request, dict):
            raise TypeError("repair request must be an object")
        repaired = validator._normalize_assessment(raw_repaired)
        if not isinstance(repaired, dict):
            raise TypeError("repair result must be an assessment object")
        index = request.get("candidate_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(merged):
            raise ValueError("repair request candidate_index is invalid")
        if index in seen_indices:
            raise ValueError("repair request candidate_index is duplicated")
        seen_indices.add(index)
        original = validator._normalize_assessment(merged[index])
        if not isinstance(original, dict):
            raise ValueError("original assessment is invalid")
        _assert_repair_identity(request, original, repaired)
        _assert_question_only_repair_scope(request, original, repaired)
        merged[index] = repaired
        modified_indices.append(index)
    return merged, {
        "schema_version": "2.0",
        "status": "complete",
        "input_assessment_count": len(assessments),
        "repair_request_count": len(requests),
        "repaired_candidate_count": len(modified_indices),
        "modified_indices": modified_indices,
    }


def program_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
    if "repaired_candidate_assessments_round_2" in inputs:
        assessments, manifest = merge_repairs(
            inputs,
            assessments_key="candidate_assessments_round_1",
            requests_key="assessment_repair_requests_round_2",
            repairs_key="repaired_candidate_assessments_round_2",
        )
        return {
            "assessment_repair_merge_manifest_round_2": manifest,
            "candidate_assessments_repaired": assessments,
        }
    if "repaired_candidate_assessments_round_1" in inputs:
        assessments, manifest = merge_repairs(
            inputs,
            assessments_key="candidate_assessments",
            requests_key="assessment_repair_requests_round_1",
            repairs_key="repaired_candidate_assessments_round_1",
        )
        return {
            "assessment_repair_merge_manifest_round_1": manifest,
            "candidate_assessments_round_1": assessments,
        }
    if "candidate_assessments_round_1" in inputs:
        requests, manifest = build_repair_requests(inputs, assessments_key="candidate_assessments_round_1")
        return {
            "assessment_repair_manifest_round_2": manifest,
            "assessment_repair_requests_round_2": requests,
        }
    if "candidate_assessments" in inputs:
        requests, manifest = build_repair_requests(inputs, assessments_key="candidate_assessments")
        return {
            "assessment_repair_manifest_round_1": manifest,
            "assessment_repair_requests_round_1": requests,
        }
    raise ValueError("assessment repair Program received no supported input contract")


def main() -> None:
    write_outputs(program_outputs(load_inputs()))


if __name__ == "__main__":
    main()
