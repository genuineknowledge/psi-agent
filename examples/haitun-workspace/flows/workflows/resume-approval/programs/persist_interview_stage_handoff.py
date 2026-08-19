"""Validate a fully reviewed batch and persist the immutable A-to-A2 handoff."""

from __future__ import annotations

import hashlib
import json
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
_QUESTION_FIELDS = {
    "question",
    "category",
    "evidence_anchor",
    "purpose",
    "positive_signal",
    "risk_signal",
}
_QUESTION_CATEGORIES = {"真实性核验", "岗位匹配", "风险澄清"}


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


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be non-empty text")
    return value


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _canonical_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _valid_revision(value: Any) -> bool:
    return isinstance(value, str) and _REVISION.fullmatch(value) is not None


def _validate_questions(value: Any, path: str, *, require_risk: bool) -> None:
    if not isinstance(value, list) or not 3 <= len(value) <= 6:
        raise ValueError(f"{path} must contain 3 to 6 questions")
    categories: set[str] = set()
    seen_questions: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict) or set(item) != _QUESTION_FIELDS:
            raise ValueError(f"{item_path} fields do not match the question-bank contract")
        for field in _QUESTION_FIELDS:
            _required_text(item.get(field), f"{item_path}.{field}")
        if item["category"] not in _QUESTION_CATEGORIES:
            raise ValueError(f"{item_path}.category is invalid")
        normalized_question = item["question"].strip()
        if normalized_question in seen_questions:
            raise ValueError(f"{item_path}.question is duplicated")
        seen_questions.add(normalized_question)
        categories.add(item["category"])
    if not {"真实性核验", "岗位匹配"} <= categories:
        raise ValueError(f"{path} must cover authenticity and role-match categories")
    if require_risk and "风险澄清" not in categories:
        raise ValueError(f"{path} must cover the risk category when mismatch_points is non-empty")


def _validate_manifest(manifest: Any, batch_id: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise ValueError("talent_pool_manifest must be complete")
    if manifest.get("schema_version") != "4.0" or manifest.get("batch_id") != batch_id:
        raise ValueError("talent_pool_manifest must use schema 4.0 and the workflow batch_id")
    records = manifest.get("records")
    expected_count = manifest.get("expected_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError("talent_pool_manifest.expected_count must be a non-negative integer")
    if not isinstance(records, list) or len(records) != expected_count:
        raise ValueError("talent_pool_manifest.expected_count must equal its record count")
    if manifest.get("errors") != []:
        raise ValueError("talent_pool_manifest.errors must be empty")

    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    candidate_ids: set[str] = set()
    record_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TypeError(f"talent_pool_manifest.records[{index}] must be an object")
        candidate_id = record.get("candidate_id")
        record_id = record.get("record_id")
        revision = record.get("assessment_revision")
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"talent_pool_manifest.records[{index}].candidate_id is invalid")
        if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError(f"talent_pool_manifest.records[{index}].record_id is invalid")
        if not _valid_revision(revision):
            raise ValueError(f"talent_pool_manifest.records[{index}].assessment_revision is invalid")
        if candidate_id in candidate_ids:
            raise ValueError("talent_pool_manifest contains a duplicate candidate")
        if record_id in record_ids:
            raise ValueError("talent_pool_manifest contains a duplicate talent record")
        candidate_ids.add(candidate_id)
        record_ids.add(record_id)
        key = (candidate_id, record_id, revision)
        if key in indexed:
            raise ValueError("talent_pool_manifest contains a duplicate review row")
        indexed[key] = record
    return indexed


def _validate_assessment(assessment: Any, batch_id: str, path: str) -> tuple[str, str, str]:
    if not isinstance(assessment, dict):
        raise TypeError(f"{path} must be an object")
    if assessment.get("schema_version") != "3.0" or assessment.get("status") != "assessed":
        raise ValueError(f"{path} must be an assessed schema 3.0 object")
    if assessment.get("batch_id") != batch_id:
        raise ValueError(f"{path}.batch_id must equal the workflow batch_id")
    candidate_id = assessment.get("candidate_id")
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        raise ValueError(f"{path}.candidate_id is invalid")
    _required_text(assessment.get("candidate_name"), f"{path}.candidate_name")
    revision = assessment.get("assessment_revision")
    if not _valid_revision(revision):
        raise ValueError(f"{path}.assessment_revision is invalid")
    role_key = _required_text(assessment.get("matched_role_key"), f"{path}.matched_role_key")
    _required_text(assessment.get("matched_role_name"), f"{path}.matched_role_name")
    revisions = assessment.get("document_revisions")
    if (
        not isinstance(revisions, dict)
        or set(revisions) != {"resume_scoring_sha256", "role_information_sha256"}
        or not all(_valid_revision(value) for value in revisions.values())
    ):
        raise ValueError(f"{path}.document_revisions is invalid")
    _validate_questions(
        assessment.get("verification_questions"),
        f"{path}.verification_questions",
        require_risk=bool(assessment.get("mismatch_points")),
    )
    return candidate_id, revision, role_key


def _validate_source_assessments(
    value: Any,
    batch_id: str,
    manifest_rows: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise ValueError("validated_candidate_assessments must be complete")
    if value.get("schema_version") != "3.0" or value.get("batch_id") != batch_id:
        raise ValueError("validated_candidate_assessments must use schema 3.0 and the workflow batch_id")
    if value.get("errors") != []:
        raise ValueError("validated_candidate_assessments.errors must be empty")
    assessments = value.get("assessments")
    if not isinstance(assessments, list):
        raise TypeError("validated_candidate_assessments.assessments must be a list")

    indexed: dict[str, dict[str, Any]] = {}
    for index, assessment in enumerate(assessments):
        path = f"validated_candidate_assessments.assessments[{index}]"
        candidate_id, _, _ = _validate_assessment(assessment, batch_id, path)
        if candidate_id in indexed:
            raise ValueError("validated_candidate_assessments contains a duplicate candidate")
        indexed[candidate_id] = assessment

    source_keys = {
        (candidate_id, assessment["assessment_revision"])
        for candidate_id, assessment in indexed.items()
    }
    manifest_keys = {(candidate_id, revision) for candidate_id, _, revision in manifest_rows}
    if source_keys != manifest_keys:
        raise ValueError("validated_candidate_assessments must exactly cover the talent manifest")
    return indexed


def _validate_decisions(
    decisions: Any,
    batch_id: str,
    manifest_rows: dict[tuple[str, str, str], dict[str, Any]],
    source_assessments: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(decisions, dict) or decisions.get("status") != "complete":
        raise ValueError("initial_decision_bundle must be complete")
    if decisions.get("schema_version") != "3.0" or decisions.get("batch_id") != batch_id:
        raise ValueError("initial_decision_bundle must use schema 3.0 and the workflow batch_id")
    pending = decisions.get("pending")
    if not isinstance(pending, list):
        raise TypeError("initial_decision_bundle.pending must be a list")
    if pending:
        raise ValueError("initial_decision_bundle contains pending, unreviewed candidates")
    if decisions.get("errors") != []:
        raise ValueError("initial_decision_bundle.errors must be empty")

    approved = decisions.get("approved")
    rejected = decisions.get("rejected")
    if not isinstance(approved, list) or not isinstance(rejected, list):
        raise TypeError("initial_decision_bundle approved and rejected must be lists")

    seen: set[tuple[str, str, str]] = set()
    for group_name, expected_status, items in (
        ("approved", "通过", approved),
        ("rejected", "不通过", rejected),
    ):
        for index, item in enumerate(items):
            path = f"initial_decision_bundle.{group_name}[{index}]"
            if not isinstance(item, dict):
                raise TypeError(f"{path} must be an object")
            if item.get("initial_status") != expected_status:
                raise ValueError(f"{group_name}[{index}] initial_status must equal {expected_status}")
            assessment = item.get("assessment")
            candidate_id, revision, _ = _validate_assessment(assessment, batch_id, f"{path}.assessment")
            source_assessment = source_assessments.get(candidate_id)
            if source_assessment is None or _canonical_text(assessment) != _canonical_text(source_assessment):
                raise ValueError(f"{path}.assessment must exactly match the immutable validated assessment")
            record_id = item.get("record_id")
            if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
                raise ValueError(f"{path}.record_id is invalid")
            key = (candidate_id, record_id, revision)
            if key in seen:
                raise ValueError("initial_decision_bundle contains a duplicate reviewed candidate")
            seen.add(key)

    if seen != set(manifest_rows):
        raise ValueError("approved and rejected decisions must exactly cover the talent manifest")
    return approved, rejected


def _validated_role_subset(
    role_catalog: Any,
    approved: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(role_catalog, dict) or role_catalog.get("schema_version") != "1.0":
        raise ValueError("role_catalog must use schema 1.0")
    source_revision = role_catalog.get("source_document_sha256")
    if not _valid_revision(source_revision):
        raise ValueError("role_catalog.source_document_sha256 is invalid")
    roles = role_catalog.get("roles")
    if not isinstance(roles, list):
        raise TypeError("role_catalog.roles must be a list")
    roles_by_key: dict[str, dict[str, Any]] = {}
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            raise TypeError(f"role_catalog.roles[{index}] must be an object")
        role_key = _required_text(role.get("role_key"), f"role_catalog.roles[{index}].role_key")
        if role_key in roles_by_key:
            raise ValueError("role_catalog contains a duplicate role_key")
        roles_by_key[role_key] = role

    referenced: set[str] = set()
    for index, item in enumerate(approved):
        assessment = item["assessment"]
        role_key = assessment["matched_role_key"]
        role = roles_by_key.get(role_key)
        if role is None or role.get("status") != "active":
            raise ValueError(f"approved[{index}] must reference an active role")
        if assessment.get("matched_role_name") != role.get("name"):
            raise ValueError(f"approved[{index}] matched role name does not match the role catalog")
        revisions = assessment["document_revisions"]
        if revisions.get("role_information_sha256") != source_revision:
            raise ValueError(f"approved[{index}] role document revision does not match the role catalog")
        referenced.add(role_key)

    return {
        "schema_version": "1.0",
        "source_document_sha256": source_revision,
        "roles": [roles_by_key[key] for key in sorted(referenced)],
    }


def _destination(feishu_config: Any) -> dict[str, str]:
    if not isinstance(feishu_config, dict):
        raise TypeError("feishu_config must be an object")
    return {field: _required_text(feishu_config.get(field), f"feishu_config.{field}") for field in _DESTINATION_FIELDS}


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    raw_batch_id = inputs.get("batch_id") if "batch_id" in inputs else inputs.get("initial_review_batch_id")
    batch_id = _required_text(_decode(raw_batch_id), "batch_id")
    if _BATCH_ID.fullmatch(batch_id) is None:
        raise ValueError("batch_id is invalid")
    manifest = _decode(inputs.get("talent_pool_manifest"))
    decisions = _decode(inputs.get("initial_decision_bundle"))
    source_assessments = _decode(inputs.get("validated_candidate_assessments"))
    role_catalog = _decode(inputs.get("role_catalog"))
    raw_feishu_config = (
        inputs.get("feishu_config") if "feishu_config" in inputs else inputs.get("initial_review_feishu_config")
    )
    feishu_config = _decode(raw_feishu_config)

    manifest_rows = _validate_manifest(manifest, batch_id)
    indexed_assessments = _validate_source_assessments(source_assessments, batch_id, manifest_rows)
    approved, rejected = _validate_decisions(decisions, batch_id, manifest_rows, indexed_assessments)
    role_subset = _validated_role_subset(role_catalog, approved)
    destination_config = _destination(feishu_config)

    payload = {
        "schema_version": "1.0",
        "status": "complete",
        "batch_id": batch_id,
        "decision_contract": {
            "schema_version": "3.0",
            "sha256": hashlib.sha256(_canonical_text(decisions).encode("utf-8")).hexdigest(),
            "expected_review_count": len(manifest_rows),
            "approved_count": len(approved),
            "rejected_count": len(rejected),
            "pending_count": 0,
        },
        "destination": destination_config,
        "role_catalog": role_subset,
        "approved": [
            {
                "talent_record_id": item["record_id"],
                "initial_status": "通过",
                "assessment": item["assessment"],
            }
            for item in approved
        ],
    }
    serialized = (_canonical_text(payload) + "\n").encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()

    handoff_dir = os.path.realpath(
        os.path.join(resolved_workspace, ".psi", "resume-approval", "interview-stage-handoffs")
    )
    if not _inside(handoff_dir, resolved_workspace):
        raise ValueError("interview stage handoff directory escapes the workspace")
    destination_path = os.path.realpath(os.path.join(handoff_dir, f"{batch_id}.json"))
    if not _inside(destination_path, handoff_dir):
        raise ValueError("interview stage handoff path escapes its directory")
    os.makedirs(handoff_dir, exist_ok=True)
    if os.path.exists(destination_path):
        with open(destination_path, "rb") as source:
            existing = source.read()
        if existing != serialized:
            raise ValueError(f"conflicting handoff already exists for batch {batch_id}")
    else:
        temporary = f"{destination_path}.tmp-{os.getpid()}"
        try:
            with open(temporary, "xb") as target:
                target.write(serialized)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    return {
        "interview_stage_handoff": {
            "schema_version": "1.0",
            "status": "complete",
            "batch_id": batch_id,
            "approved_count": len(approved),
            "path": os.path.relpath(destination_path, resolved_workspace).replace(os.sep, "/"),
            "sha256": digest,
            "next_workflow": "resume-interview-preparation",
            "next_input": "interview_stage_handoff",
        },
        "interview_stage_handoff_manifest": {
            "schema_version": "1.0",
            "status": "complete",
            "approved_count": len(approved),
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
