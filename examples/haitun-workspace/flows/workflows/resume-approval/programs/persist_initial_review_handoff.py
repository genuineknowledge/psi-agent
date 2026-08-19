"""Persist the immutable pre-review handoff that separates Workflow A from A2."""

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
_MANIFEST_RECORD_FIELDS = {
    "record_id",
    "candidate_id",
    "assessment_revision",
    "row_fingerprint",
    "attachment_persisted",
    "created",
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


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
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


def _validate_assessments(value: Any, batch_id: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise ValueError("validated_candidate_assessments must be complete")
    if value.get("schema_version") != "3.0" or value.get("batch_id") != batch_id:
        raise ValueError("validated_candidate_assessments must use schema 3.0 and the workflow batch_id")
    assessments = value.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        raise ValueError("validated_candidate_assessments.assessments must be a non-empty list")

    indexed: dict[str, dict[str, Any]] = {}
    for index, assessment in enumerate(assessments):
        path = f"validated_candidate_assessments.assessments[{index}]"
        if not isinstance(assessment, dict):
            raise TypeError(f"{path} must be an object")
        if assessment.get("schema_version") != "3.0" or assessment.get("status") != "assessed":
            raise ValueError(f"{path} must be assessed schema 3.0")
        if assessment.get("batch_id") != batch_id:
            raise ValueError(f"{path}.batch_id does not match the workflow batch")
        candidate_id = assessment.get("candidate_id")
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"{path}.candidate_id is invalid")
        if candidate_id in indexed:
            raise ValueError("validated_candidate_assessments contains a duplicate candidate")
        _required_text(assessment.get("candidate_name"), f"{path}.candidate_name")
        _required_text(assessment.get("matched_role_key"), f"{path}.matched_role_key")
        _required_text(assessment.get("matched_role_name"), f"{path}.matched_role_name")
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
        indexed[candidate_id] = assessment
    return indexed


def _validate_manifest(
    value: Any, batch_id: str, assessments: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], str, str]:
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise ValueError("talent_pool_manifest must be complete")
    if value.get("schema_version") != "4.0" or value.get("batch_id") != batch_id:
        raise ValueError("talent_pool_manifest must use schema 4.0 and the workflow batch_id")
    records = value.get("records")
    expected_count = value.get("expected_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 1:
        raise ValueError("talent_pool_manifest.expected_count must be a positive integer")
    if not isinstance(records, list) or len(records) != expected_count:
        raise ValueError("talent_pool_manifest.expected_count must equal its record count")
    if expected_count != len(assessments):
        raise ValueError("talent_pool_manifest must exactly cover validated assessments")
    if value.get("errors") != []:
        raise ValueError("talent_pool_manifest.errors must be empty")

    indexed: dict[str, dict[str, Any]] = {}
    record_ids: set[str] = set()
    for index, record in enumerate(records):
        path = f"talent_pool_manifest.records[{index}]"
        if not isinstance(record, dict):
            raise TypeError(f"{path} must be an object")
        if set(record) != _MANIFEST_RECORD_FIELDS:
            raise ValueError(f"{path} must contain only the private attachment-safe manifest fields")
        if record.get("attachment_persisted") is not True:
            raise ValueError(f"{path}.attachment_persisted must be true")
        if not isinstance(record.get("created"), bool):
            raise TypeError(f"{path}.created must be boolean")
        candidate_id = record.get("candidate_id")
        record_id = record.get("record_id")
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"{path}.candidate_id is invalid")
        if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError(f"{path}.record_id is invalid")
        if candidate_id in indexed or record_id in record_ids:
            raise ValueError("talent_pool_manifest contains a duplicate candidate or record")
        assessment = assessments.get(candidate_id)
        if assessment is None:
            raise ValueError("talent_pool_manifest must exactly cover validated assessments")
        if record.get("assessment_revision") != assessment["assessment_revision"]:
            raise ValueError(f"{path}.assessment_revision does not match the validated revision")
        fingerprint = record.get("row_fingerprint")
        _validate_row_fingerprint(fingerprint, path)
        expected_fingerprint = _expected_row_fingerprint(
            assessment,
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
        indexed[candidate_id] = record
        record_ids.add(record_id)
    if set(indexed) != set(assessments):
        raise ValueError("talent_pool_manifest must exactly cover validated assessments")
    return (
        indexed,
        _required_text(value.get("base_url"), "talent_pool_manifest.base_url"),
        _required_text(value.get("view_name"), "talent_pool_manifest.view_name"),
    )


def _validate_roles(value: Any, assessments: dict[str, dict[str, Any]]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise ValueError("role_catalog must use schema 1.0")
    source_revision = value.get("source_document_sha256")
    if not _valid_revision(source_revision):
        raise ValueError("role_catalog.source_document_sha256 is invalid")
    roles = value.get("roles")
    if not isinstance(roles, list):
        raise TypeError("role_catalog.roles must be a list")
    by_key: dict[str, dict[str, Any]] = {}
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            raise TypeError(f"role_catalog.roles[{index}] must be an object")
        role_key = _required_text(role.get("role_key"), f"role_catalog.roles[{index}].role_key")
        if role_key in by_key:
            raise ValueError("role_catalog contains a duplicate role_key")
        by_key[role_key] = role
    for candidate_id, assessment in assessments.items():
        role = by_key.get(assessment["matched_role_key"])
        if role is None or role.get("status") != "active":
            raise ValueError(f"assessment {candidate_id} must reference an active role")
        if role.get("name") != assessment["matched_role_name"]:
            raise ValueError(f"assessment {candidate_id} role name does not match the catalog")
        if assessment["document_revisions"]["role_information_sha256"] != source_revision:
            raise ValueError(f"assessment {candidate_id} role document revision does not match")


def _destination(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("feishu_config must be an object")
    return {field: _required_text(value.get(field), f"feishu_config.{field}") for field in _DESTINATION_FIELDS}


def _review_request(batch_id: str, count: int, base_url: str, view_name: str) -> str:
    return (
        "初审任务已就绪\uff0c正在等你操作\uff1a\n\n"
        f"**批次**\uff1a`{batch_id}`\n"
        f"**请打开**\uff1a{base_url} → 数据表「候选人才库」→ 视图「{view_name}」\n"
        f"**本次建档**\uff1a{count} 名候选人\n"
        "**你的操作**\uff1a逐行查看简历摘要、总分、评级、匹配岗位、匹配点、不匹配点、"
        "面试建议、面试建议理由和问题库\uff0c然后只修改「初审状态」为「通过」或「不通过」。"
        "全部完成后\uff0c请回到聊天并只回复\uff1a初审完成\n"
        "收到后将直接启动下一阶段 workflow:resume-interview-preparation。"
        "只要仍存在「待审批」\uff0c下一阶段会拒绝执行。"
    )


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    batch_id = _required_text(_decode(inputs.get("batch_id")), "batch_id")
    if _BATCH_ID.fullmatch(batch_id) is None:
        raise ValueError("batch_id is invalid")
    validated = _decode(inputs.get("validated_candidate_assessments"))
    manifest = _decode(inputs.get("talent_pool_manifest"))
    role_catalog = _decode(inputs.get("role_catalog"))
    assessments = _validate_assessments(validated, batch_id)
    _, base_url, view_name = _validate_manifest(manifest, batch_id, assessments)
    _validate_roles(role_catalog, assessments)
    destination = _destination(_decode(inputs.get("feishu_config")))
    if manifest.get("table_id") != destination["talent_pool_table_id"]:
        raise ValueError("talent_pool_manifest table does not match the configured destination")
    if base_url != destination["base_url"]:
        raise ValueError("talent_pool_manifest base URL does not match the configured destination")

    document = {
        "schema_version": "1.0",
        "status": "ready_for_review",
        "batch_id": batch_id,
        "destination": destination,
        "role_catalog": role_catalog,
        "validated_candidate_assessments": validated,
        "talent_pool_manifest": manifest,
    }
    serialized = _canonical_bytes(document)
    digest = hashlib.sha256(serialized).hexdigest()
    handoff_root = os.path.realpath(os.path.join(workspace, ".psi", "resume-approval", "initial-review-handoffs"))
    if not _inside(handoff_root, workspace):
        raise ValueError("initial review handoff directory escapes the workspace")
    path = os.path.realpath(os.path.join(handoff_root, f"{batch_id}.json"))
    if not _inside(path, handoff_root):
        raise ValueError("initial review handoff path escapes its directory")
    os.makedirs(handoff_root, exist_ok=True)
    if os.path.exists(path):
        with open(path, "rb") as source:
            if source.read() != serialized:
                raise ValueError(f"conflicting initial review handoff already exists for batch {batch_id}")
    else:
        temporary = f"{path}.tmp-{os.getpid()}"
        try:
            with open(temporary, "xb") as target:
                target.write(serialized)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    count = len(assessments)
    return {
        "initial_review_handoff": {
            "schema_version": "1.0",
            "status": "ready_for_review",
            "batch_id": batch_id,
            "expected_count": count,
            "path": os.path.relpath(path, workspace).replace(os.sep, "/"),
            "sha256": digest,
            "next_workflow": "resume-interview-preparation",
            "next_input": "initial_review_handoff",
        },
        "initial_review_handoff_manifest": {
            "schema_version": "1.0",
            "status": "complete",
            "expected_count": count,
            "errors": [],
        },
        "initial_review_request": _review_request(batch_id, count, base_url, view_name),
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
