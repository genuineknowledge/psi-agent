"""Load and independently verify Workflow A's immutable pre-review handoff."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from typing import Any

_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_RECORD_ID = re.compile(r"^rec[A-Za-z0-9]{8,64}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_DESCRIPTOR_KEYS = {
    "schema_version",
    "status",
    "batch_id",
    "expected_count",
    "path",
    "sha256",
    "next_workflow",
    "next_input",
}
_DOCUMENT_KEYS = {
    "schema_version",
    "status",
    "batch_id",
    "destination",
    "role_catalog",
    "validated_candidate_assessments",
    "talent_pool_manifest",
}
_DESTINATION_FIELDS = (
    "app_token",
    "base_url",
    "talent_pool_table_id",
    "interview_table_id",
)
_AI_FINGERPRINT_FIELDS = {
    "姓名",
    "评级",
    "学历",
    "毕业院校/背景",
    "简历摘要",
    "总分",
    "匹配岗位",
    "匹配点",
    "不匹配点",
    "面试建议",
    "面试建议理由",
    "问题库",
}
_QUESTION_FIELDS = {
    "question",
    "category",
    "evidence_anchor",
    "purpose",
    "positive_signal",
    "risk_signal",
}
_QUESTION_CATEGORIES = {"真实性核验", "岗位匹配", "风险澄清"}
_HANDOFF_PREFIX = os.path.join(".psi", "resume-approval", "initial-review-handoffs")
_DEFAULTS_PATH = os.path.join("flows", "workflows", "resume-approval", "resume-approval.defaults.json")


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


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be non-empty text")
    return value.strip()


def _valid_revision(value: Any) -> bool:
    return isinstance(value, str) and _REVISION.fullmatch(value) is not None


def _validate_row_fingerprint(value: Any, path: str) -> None:
    if not isinstance(value, dict) or set(value) != _AI_FINGERPRINT_FIELDS:
        raise ValueError(f"{path}.row_fingerprint must contain the exact 12 AI-owned fields")
    score = value["总分"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise TypeError(f"{path}.row_fingerprint.总分 must be a finite number")
    for field in _AI_FINGERPRINT_FIELDS - {"总分"}:
        if not isinstance(value[field], str):
            raise TypeError(f"{path}.row_fingerprint.{field} must be text")


def _render_questions(value: Any, path: str, *, require_risk: bool) -> str:
    if not isinstance(value, list) or not 3 <= len(value) <= 6:
        raise ValueError(f"{path} must contain 3 to 6 questions")
    lines: list[str] = []
    categories: set[str] = set()
    seen_questions: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict) or set(item) != _QUESTION_FIELDS:
            raise ValueError(f"{item_path} fields do not match the question-bank contract")
        for field in _QUESTION_FIELDS:
            _required_text(item.get(field), f"{item_path}.{field}")
        category = item["category"]
        if category not in _QUESTION_CATEGORIES:
            raise ValueError(f"{item_path}.category is invalid")
        normalized_question = item["question"].strip()
        if normalized_question in seen_questions:
            raise ValueError(f"{item_path}.question is duplicated")
        seen_questions.add(normalized_question)
        categories.add(category)
        lines.append(f"{index + 1}. [{category}] {normalized_question}")
    if not {"真实性核验", "岗位匹配"} <= categories:
        raise ValueError(f"{path} must cover authenticity and role-match categories")
    if require_risk and "风险澄清" not in categories:
        raise ValueError(f"{path} must cover the risk category when mismatch_points is non-empty")
    return "\n".join(lines)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _render_points(value: Any, path: str, *, mismatch: bool) -> str:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be a table-writeable list")
    lines: list[str] = []
    for index, point in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(point, dict):
            raise TypeError(f"{item_path} must be a table-writeable object")
        requirement = point.get("requirement")
        if not isinstance(requirement, str):
            raise TypeError(f"{item_path}.requirement must be table-writeable text")
        evidence = point.get("resume_evidence")
        if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
            raise TypeError(f"{item_path}.resume_evidence must be a table-writeable text list")
        evidence_text = "、".join(evidence)
        if mismatch:
            lines.append(f"- 风险\uff1a{requirement}\uff1b依据\uff1a{evidence_text}")
        else:
            lines.append(f"- 要求\uff1a{requirement}\uff1b证据\uff1a{evidence_text}")
    return "\n".join(lines)


def _expected_row_fingerprint(assessment: dict[str, Any], path: str) -> dict[str, Any]:
    text_fields = {
        "姓名": "candidate_name",
        "评级": "grade",
        "学历": "education",
        "毕业院校/背景": "education_background",
        "匹配岗位": "matched_role_name",
        "面试建议": "interview_recommendation",
        "面试建议理由": "interview_recommendation_reason",
    }
    fingerprint: dict[str, Any] = {}
    for visible_field, assessment_field in text_fields.items():
        value = assessment.get(assessment_field)
        if not isinstance(value, str):
            raise TypeError(f"{path}.{assessment_field} must be table-writeable text")
        fingerprint[visible_field] = value

    summary = assessment.get("resume_summary")
    if not isinstance(summary, list) or any(not isinstance(item, str) for item in summary):
        raise TypeError(f"{path}.resume_summary must be a table-writeable JSON string array")
    score = assessment.get("total_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise TypeError(f"{path}.total_score must be a finite number")

    fingerprint.update(
        {
            "简历摘要": "\n".join(summary),
            "总分": score,
            "匹配点": _render_points(assessment.get("match_points"), f"{path}.match_points", mismatch=False),
            "不匹配点": _render_points(
                assessment.get("mismatch_points"),
                f"{path}.mismatch_points",
                mismatch=True,
            ),
            "问题库": _render_questions(
                assessment.get("verification_questions"),
                f"{path}.verification_questions",
                require_risk=bool(assessment.get("mismatch_points")),
            ),
        }
    )
    return fingerprint


def _require_descriptor(value: Any) -> dict[str, Any]:
    descriptor = _decode(value)
    if not isinstance(descriptor, dict) or set(descriptor) != _DESCRIPTOR_KEYS:
        raise ValueError("initial review descriptor fields do not match the contract")
    if descriptor.get("schema_version") != "1.0" or descriptor.get("status") != "ready_for_review":
        raise ValueError("initial review descriptor must be ready_for_review schema 1.0")
    batch_id = descriptor.get("batch_id")
    if not isinstance(batch_id, str) or _BATCH_ID.fullmatch(batch_id) is None:
        raise ValueError("initial review descriptor batch_id is invalid")
    expected_count = descriptor.get("expected_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 1:
        raise ValueError("initial review descriptor expected_count is invalid")
    if not _valid_revision(descriptor.get("sha256")):
        raise ValueError("initial review descriptor hash is invalid")
    if descriptor.get("next_workflow") != "resume-interview-preparation":
        raise ValueError("initial review descriptor next_workflow is invalid")
    if descriptor.get("next_input") != "initial_review_handoff":
        raise ValueError("initial review descriptor next_input is invalid")
    return descriptor


def _read_document(workspace: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    relative = descriptor.get("path")
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise ValueError("initial review handoff path must be workspace-relative")
    expected = os.path.join(_HANDOFF_PREFIX, f"{descriptor['batch_id']}.json").replace(os.sep, "/")
    if relative.replace("\\", "/") != expected:
        raise ValueError("initial review handoff path does not match its batch")
    allowed_root = os.path.realpath(os.path.join(workspace, _HANDOFF_PREFIX))
    path = os.path.realpath(os.path.join(workspace, relative))
    if not _inside(allowed_root, workspace) or not _inside(path, allowed_root):
        raise ValueError("initial review handoff path escapes its allowed directory")
    if not os.path.isfile(path):
        raise ValueError("initial review handoff path does not name a file")
    with open(path, "rb") as source:
        raw = source.read()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError("initial review handoff hash does not match the file")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("initial review handoff is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
        raise ValueError("initial review handoff fields do not match the contract")
    if document.get("schema_version") != "1.0" or document.get("status") != "ready_for_review":
        raise ValueError("initial review handoff must be ready_for_review schema 1.0")
    if document.get("batch_id") != descriptor["batch_id"]:
        raise ValueError("descriptor and initial review handoff batch identities do not match")
    return document


def _validate_source(document: dict[str, Any], expected_count: int) -> None:
    batch_id = document["batch_id"]
    validated = document.get("validated_candidate_assessments")
    if not isinstance(validated, dict) or validated.get("status") != "complete":
        raise ValueError("validated_candidate_assessments must be complete")
    if validated.get("schema_version") != "3.0" or validated.get("batch_id") != batch_id:
        raise ValueError("validated_candidate_assessments identity is invalid")
    assessments = validated.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        raise ValueError("validated_candidate_assessments.assessments must be a non-empty list")
    if len(assessments) != expected_count:
        raise ValueError("descriptor expected_count does not match validated assessments")

    by_candidate: dict[str, dict[str, Any]] = {}
    for index, assessment in enumerate(assessments):
        path = f"validated_candidate_assessments.assessments[{index}]"
        if not isinstance(assessment, dict):
            raise TypeError(f"{path} must be an object")
        if assessment.get("schema_version") != "3.0" or assessment.get("status") != "assessed":
            raise ValueError(f"{path} is invalid")
        if assessment.get("batch_id") != batch_id:
            raise ValueError(f"{path}.batch_id is invalid")
        candidate_id = assessment.get("candidate_id")
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"{path}.candidate_id is invalid")
        if candidate_id in by_candidate:
            raise ValueError("validated assessments contain a duplicate candidate")
        if not _valid_revision(assessment.get("assessment_revision")):
            raise ValueError(f"{path}.assessment_revision is invalid")
        revisions = assessment.get("document_revisions")
        if (
            not isinstance(revisions, dict)
            or set(revisions) != {"resume_scoring_sha256", "role_information_sha256"}
            or not all(_valid_revision(revision) for revision in revisions.values())
        ):
            raise ValueError(f"{path}.document_revisions is invalid")
        _render_questions(
            assessment.get("verification_questions"),
            f"{path}.verification_questions",
            require_risk=bool(assessment.get("mismatch_points")),
        )
        by_candidate[candidate_id] = assessment

    manifest = document.get("talent_pool_manifest")
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise ValueError("talent_pool_manifest must be complete")
    if manifest.get("schema_version") != "4.0" or manifest.get("batch_id") != batch_id:
        raise ValueError("talent_pool_manifest identity is invalid")
    records = manifest.get("records")
    if not isinstance(records, list) or manifest.get("expected_count") != len(records):
        raise ValueError("talent_pool_manifest expected_count is invalid")
    if len(records) != expected_count:
        raise ValueError("descriptor expected_count does not match the talent manifest")
    if manifest.get("errors") != []:
        raise ValueError("talent_pool_manifest.errors must be empty")
    seen_candidates: set[str] = set()
    seen_records: set[str] = set()
    for index, record in enumerate(records):
        path = f"talent_pool_manifest.records[{index}]"
        if not isinstance(record, dict):
            raise TypeError(f"{path} must be an object")
        candidate_id = record.get("candidate_id")
        record_id = record.get("record_id")
        if not isinstance(candidate_id, str) or candidate_id not in by_candidate:
            raise ValueError("talent_pool_manifest must exactly cover validated assessments")
        if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError(f"{path}.record_id is invalid")
        if candidate_id in seen_candidates or record_id in seen_records:
            raise ValueError("talent_pool_manifest contains a duplicate candidate or record")
        if record.get("assessment_revision") != by_candidate[candidate_id]["assessment_revision"]:
            raise ValueError(f"{path}.assessment_revision does not match the validated revision")
        fingerprint = record.get("row_fingerprint")
        _validate_row_fingerprint(fingerprint, path)
        expected_fingerprint = _expected_row_fingerprint(
            by_candidate[candidate_id],
            f"validated_candidate_assessments.assessments[{candidate_id}]",
        )
        mismatched_fields = sorted(
            field
            for field in _AI_FINGERPRINT_FIELDS
            if _canonical_bytes(fingerprint[field]) != _canonical_bytes(expected_fingerprint[field])
        )
        if mismatched_fields:
            raise ValueError(
                f"{path}.row_fingerprint does not match the validated assessment: {','.join(mismatched_fields)}"
            )
        seen_candidates.add(candidate_id)
        seen_records.add(record_id)
    if seen_candidates != set(by_candidate):
        raise ValueError("talent_pool_manifest must exactly cover validated assessments")

    catalog = document.get("role_catalog")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != "1.0":
        raise ValueError("role_catalog must use schema 1.0")
    source_revision = catalog.get("source_document_sha256")
    if not _valid_revision(source_revision):
        raise ValueError("role_catalog source revision is invalid")
    roles = catalog.get("roles")
    if not isinstance(roles, list):
        raise TypeError("role_catalog.roles must be a list")
    roles_by_key = {
        role.get("role_key"): role for role in roles if isinstance(role, dict) and isinstance(role.get("role_key"), str)
    }
    if len(roles_by_key) != len(roles):
        raise ValueError("role_catalog contains an invalid or duplicate role")
    for assessment in assessments:
        role = roles_by_key.get(assessment.get("matched_role_key"))
        if role is None or role.get("status") != "active" or role.get("name") != assessment.get("matched_role_name"):
            raise ValueError("assessment role does not match the active role catalog")
        if assessment["document_revisions"]["role_information_sha256"] != source_revision:
            raise ValueError("assessment role document revision does not match the role catalog")


def _load_current_config(workspace: str) -> dict[str, Any]:
    path = os.path.realpath(os.path.join(workspace, _DEFAULTS_PATH))
    if not _inside(path, workspace) or not os.path.isfile(path):
        raise FileNotFoundError("resume-approval.defaults.json is missing")
    with open(path, encoding="utf-8") as source:
        defaults = json.load(source)
    if not isinstance(defaults, dict) or not isinstance(defaults.get("feishu_config"), dict):
        raise TypeError("resume-approval.defaults.json must contain feishu_config")
    config = defaults["feishu_config"]
    for field in _DESTINATION_FIELDS:
        _required_text(config.get(field), f"feishu_config.{field}")
    return config


def _require_destination_match(snapshot: Any, current: dict[str, Any]) -> None:
    if not isinstance(snapshot, dict) or set(snapshot) != set(_DESTINATION_FIELDS):
        raise ValueError("initial review destination fields do not match the contract")
    for field in _DESTINATION_FIELDS:
        if snapshot.get(field) != current.get(field):
            raise ValueError(f"current destination does not match initial review destination: {field}")


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    descriptor = _require_descriptor(inputs.get("initial_review_handoff"))
    document = _read_document(workspace, descriptor)
    _validate_source(document, descriptor["expected_count"])
    feishu_config = _load_current_config(workspace)
    _require_destination_match(document.get("destination"), feishu_config)
    return {
        "initial_review_stage_bundle": document,
        "validated_candidate_assessments": document["validated_candidate_assessments"],
        "talent_pool_manifest": document["talent_pool_manifest"],
        "role_catalog": document["role_catalog"],
        "initial_review_batch_id": document["batch_id"],
        "initial_review_feishu_config": feishu_config,
        "initial_review_load_manifest": {
            "schema_version": "1.0",
            "status": "complete",
            "expected_count": descriptor["expected_count"],
            "errors": [],
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
