"""Load and independently verify an immutable resume-approval interview handoff."""

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
_DESCRIPTOR_KEYS = {
    "schema_version",
    "status",
    "batch_id",
    "approved_count",
    "path",
    "sha256",
    "next_workflow",
    "next_input",
}
_DOCUMENT_KEYS = {
    "schema_version",
    "status",
    "batch_id",
    "decision_contract",
    "destination",
    "role_catalog",
    "approved",
}
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
_HANDOFF_PREFIX = os.path.join(".psi", "resume-approval", "interview-stage-handoffs")
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
    return value


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


def _require_descriptor(value: Any) -> dict[str, Any]:
    descriptor = _decode(value)
    if not isinstance(descriptor, dict) or set(descriptor) != _DESCRIPTOR_KEYS:
        raise ValueError("interview stage descriptor fields do not match the contract")
    if descriptor.get("schema_version") != "1.0" or descriptor.get("status") != "complete":
        raise ValueError("interview stage descriptor must be complete schema 1.0")
    batch_id = descriptor.get("batch_id")
    if not isinstance(batch_id, str) or _BATCH_ID.fullmatch(batch_id) is None:
        raise ValueError("interview stage descriptor batch_id is invalid")
    approved_count = descriptor.get("approved_count")
    if isinstance(approved_count, bool) or not isinstance(approved_count, int) or approved_count < 0:
        raise ValueError("interview stage descriptor approved_count is invalid")
    if not _valid_revision(descriptor.get("sha256")):
        raise ValueError("interview stage descriptor hash is invalid")
    if descriptor.get("next_workflow") != "resume-interview-preparation":
        raise ValueError("interview stage descriptor next_workflow is invalid")
    if descriptor.get("next_input") != "interview_stage_handoff":
        raise ValueError("interview stage descriptor next_input is invalid")
    return descriptor


def _read_verified_document(workspace: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    relative = descriptor.get("path")
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise ValueError("interview stage handoff path must be workspace-relative")
    expected_relative = os.path.join(_HANDOFF_PREFIX, f"{descriptor['batch_id']}.json").replace(os.sep, "/")
    if relative.replace("\\", "/") != expected_relative:
        raise ValueError("interview stage handoff path does not match its batch")
    allowed_root = os.path.realpath(os.path.join(workspace, _HANDOFF_PREFIX))
    path = os.path.realpath(os.path.join(workspace, relative))
    if not _inside(allowed_root, workspace) or not _inside(path, allowed_root):
        raise ValueError("interview stage handoff path escapes its allowed directory")
    if not os.path.isfile(path):
        raise ValueError("interview stage handoff path does not name a file")
    with open(path, "rb") as source:
        raw = source.read()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError("interview stage handoff hash does not match the file")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("interview stage handoff is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS:
        raise ValueError("interview stage handoff fields do not match the contract")
    if document.get("schema_version") != "1.0" or document.get("status") != "complete":
        raise ValueError("interview stage handoff must be complete schema 1.0")
    if document.get("batch_id") != descriptor["batch_id"]:
        raise ValueError("descriptor and handoff batch identities do not match")
    return document


def _load_current_feishu_config(workspace: str) -> dict[str, Any]:
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
        raise ValueError("handoff destination fields do not match the contract")
    for field in _DESTINATION_FIELDS:
        expected = _required_text(snapshot.get(field), f"destination.{field}")
        if current.get(field) != expected:
            raise ValueError(f"current destination does not match handoff destination: {field}")


def _validate_decision_contract(value: Any, approved_length: int) -> None:
    if not isinstance(value, dict):
        raise TypeError("decision_contract must be an object")
    expected_keys = {
        "schema_version",
        "sha256",
        "expected_review_count",
        "approved_count",
        "rejected_count",
        "pending_count",
    }
    if set(value) != expected_keys or value.get("schema_version") != "3.0":
        raise ValueError("decision_contract fields do not match schema 3.0")
    if not _valid_revision(value.get("sha256")):
        raise ValueError("decision_contract hash is invalid")
    counts: dict[str, int] = {}
    for field in ("expected_review_count", "approved_count", "rejected_count", "pending_count"):
        count = value.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"decision_contract.{field} is invalid")
        counts[field] = count
    if counts["pending_count"] != 0:
        raise ValueError("decision_contract.pending_count must be zero; unreviewed content is forbidden")
    if counts["approved_count"] != approved_length:
        raise ValueError("decision_contract.approved_count does not match the approved list")
    if counts["approved_count"] + counts["rejected_count"] != counts["expected_review_count"]:
        raise ValueError("decision contract final decisions do not equal the expected review count")


def _build_tasks(document: dict[str, Any]) -> list[dict[str, Any]]:
    approved = document.get("approved")
    if not isinstance(approved, list):
        raise TypeError("handoff approved must be a list")
    _validate_decision_contract(document.get("decision_contract"), len(approved))
    catalog = document.get("role_catalog")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != "1.0":
        raise ValueError("handoff role catalog must use schema 1.0")
    source_revision = catalog.get("source_document_sha256")
    if not _valid_revision(source_revision):
        raise ValueError("handoff role document revision is invalid")
    roles = catalog.get("roles")
    if not isinstance(roles, list):
        raise TypeError("handoff role catalog roles must be a list")
    roles_by_key: dict[str, dict[str, Any]] = {}
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            raise TypeError(f"role_catalog.roles[{index}] must be an object")
        role_key = _required_text(role.get("role_key"), f"role_catalog.roles[{index}].role_key")
        if role_key in roles_by_key:
            raise ValueError("handoff role catalog contains a duplicate role")
        if role.get("status") != "active":
            raise ValueError("handoff approved role must be active")
        roles_by_key[role_key] = role

    tasks: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    record_ids: set[str] = set()
    referenced_roles: set[str] = set()
    for index, item in enumerate(approved):
        if not isinstance(item, dict):
            raise TypeError(f"approved[{index}] must be an object")
        if item.get("initial_status") != "通过":
            raise ValueError(f"approved[{index}].initial_status must equal 通过")
        record_id = item.get("talent_record_id")
        if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError(f"approved[{index}] talent record id is invalid")
        assessment = item.get("assessment")
        if not isinstance(assessment, dict):
            raise TypeError(f"approved[{index}].assessment must be an object")
        if assessment.get("schema_version") != "3.0" or assessment.get("status") != "assessed":
            raise ValueError(f"approved[{index}] assessment must be assessed schema 3.0")
        if assessment.get("batch_id") != document["batch_id"]:
            raise ValueError(f"approved[{index}] assessment batch does not match the handoff")
        candidate_id = assessment.get("candidate_id")
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"approved[{index}] candidate id is invalid")
        if candidate_id in candidate_ids or record_id in record_ids:
            raise ValueError("handoff approved list contains a duplicate candidate or talent record")
        candidate_ids.add(candidate_id)
        record_ids.add(record_id)
        if not _valid_revision(assessment.get("assessment_revision")):
            raise ValueError(f"approved[{index}] assessment revision is invalid")
        revisions = assessment.get("document_revisions")
        if not isinstance(revisions, dict) or not all(
            _valid_revision(revisions.get(field)) for field in ("resume_scoring_sha256", "role_information_sha256")
        ):
            raise ValueError(f"approved[{index}] document revisions are invalid")
        _validate_questions(
            assessment.get("verification_questions"),
            f"approved[{index}].assessment.verification_questions",
            require_risk=bool(assessment.get("mismatch_points")),
        )
        if revisions.get("role_information_sha256") != source_revision:
            raise ValueError(f"approved[{index}] role document revision does not match")
        role_key = assessment.get("matched_role_key")
        role = roles_by_key.get(role_key)
        if role is None or role.get("name") != assessment.get("matched_role_name"):
            raise ValueError(f"approved[{index}] role mapping does not match the catalog")
        referenced_roles.add(role_key)
        tasks.append(
            {
                "candidate_id": candidate_id,
                "talent_record_id": record_id,
                "assessment": assessment,
                "matched_role": role,
            }
        )
    if referenced_roles != set(roles_by_key):
        raise ValueError("handoff role catalog must contain exactly the approved referenced roles")
    return tasks


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    descriptor = _require_descriptor(inputs.get("interview_stage_handoff"))
    document = _read_verified_document(workspace, descriptor)
    approved = document.get("approved")
    if not isinstance(approved, list) or descriptor["approved_count"] != len(approved):
        raise ValueError("descriptor approved_count does not match the handoff approved list")
    feishu_config = _load_current_feishu_config(workspace)
    _require_destination_match(document.get("destination"), feishu_config)
    tasks = _build_tasks(document)
    return {
        "interview_stage_bundle": document,
        "approved_interview_tasks": tasks,
        "batch_id": document["batch_id"],
        "feishu_config": feishu_config,
        "stage_load_manifest": {
            "schema_version": "1.0",
            "status": "complete",
            "approved_count": len(tasks),
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
