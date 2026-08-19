"""Persist private assessment handoffs keyed by Feishu interview record id."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_RECORD_ID = re.compile(r"^rec[A-Za-z0-9]{8,64}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_ROLE_FIELDS = {
    "role_key",
    "name",
    "employment_type",
    "location",
    "headcount",
    "status",
    "responsibilities",
    "hard_requirements",
    "preferences",
    "source_evidence",
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


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _validated_roles(value: Any) -> tuple[str, dict[str, dict[str, Any]]]:
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise ValueError("interview stage role catalog must use schema 1.0")
    source_revision = value.get("source_document_sha256")
    if not isinstance(source_revision, str) or _REVISION.fullmatch(source_revision) is None:
        raise ValueError("interview stage role catalog revision is invalid")
    roles = value.get("roles")
    if not isinstance(roles, list):
        raise TypeError("interview stage role catalog roles must be a list")
    roles_by_key: dict[str, dict[str, Any]] = {}
    for index, role in enumerate(roles):
        path = f"interview_stage_bundle.role_catalog.roles[{index}]"
        if not isinstance(role, dict) or set(role) != _ROLE_FIELDS:
            raise ValueError(f"{path} fields do not match the complete role contract")
        role_key = _required_text(role.get("role_key"), f"{path}.role_key")
        _required_text(role.get("name"), f"{path}.name")
        if role.get("status") != "active":
            raise ValueError(f"{path}.status must equal active")
        hard_requirements = role.get("hard_requirements")
        if not isinstance(hard_requirements, list) or not all(
            isinstance(item, str) and bool(item.strip()) for item in hard_requirements
        ):
            raise ValueError(f"{path}.hard_requirements must be a text list")
        if role_key in roles_by_key:
            raise ValueError("interview stage role catalog contains a duplicate role_key")
        roles_by_key[role_key] = role
    return source_revision, roles_by_key


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    manifest = _decode(inputs.get("interview_manifest"))
    stage = _decode(inputs.get("interview_stage_bundle"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "4.0"
        or manifest.get("status") != "complete"
    ):
        raise ValueError("interview_manifest must be complete")
    if not isinstance(stage, dict) or stage.get("schema_version") != "1.0" or stage.get("status") != "complete":
        raise ValueError("interview_stage_bundle must be complete")
    batch_id = _required_text(manifest.get("batch_id"), "interview_manifest.batch_id")
    if stage.get("batch_id") != batch_id:
        raise ValueError("interview manifest and interview stage must use the same batch_id")
    destination_config = stage.get("destination")
    if not isinstance(destination_config, dict) or manifest.get("table_id") != destination_config.get(
        "interview_table_id"
    ):
        raise ValueError("interview manifest table id does not match the interview stage destination")
    contract = stage.get("decision_contract")
    if not isinstance(contract, dict) or contract.get("schema_version") != "3.0":
        raise ValueError("interview stage decision contract is invalid")
    for field in ("expected_review_count", "approved_count", "rejected_count", "pending_count"):
        value = contract.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"interview stage {field} is invalid")
    if contract["pending_count"] != 0:
        raise ValueError("interview stage contains pending, unreviewed candidates")
    if contract["approved_count"] + contract["rejected_count"] != contract["expected_review_count"]:
        raise ValueError("interview stage reviewed counts are incomplete")
    records = manifest.get("records")
    approved = stage.get("approved")
    if not isinstance(records, list) or not isinstance(approved, list):
        raise ValueError("complete interview handoff requires record and approved lists")
    if manifest.get("errors") not in (None, []):
        raise ValueError("interview_manifest.errors must be empty")
    expected_count = manifest.get("expected_count")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count != len(records)
        or expected_count != len(approved)
        or contract["approved_count"] != len(approved)
    ):
        raise ValueError("interview manifest expected_count must equal the approved candidate count")
    if len(records) != len(approved):
        raise ValueError("interview records must exactly cover approved candidates")

    approved_by_candidate: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(approved):
        assessment = item.get("assessment") if isinstance(item, dict) else None
        candidate_id = assessment.get("candidate_id") if isinstance(assessment, dict) else None
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"approved[{index}] has an invalid candidate assessment")
        if candidate_id in approved_by_candidate:
            raise ValueError(f"approved[{index}] duplicates candidate_id")
        if item.get("initial_status") != "通过":
            raise ValueError(f"approved[{index}].initial_status must equal 通过")
        if assessment.get("schema_version") != "3.0" or assessment.get("batch_id") != batch_id:
            raise ValueError(f"approved[{index}] assessment must use schema 3.0 and the workflow batch_id")
        approved_by_candidate[candidate_id] = item

    role_revision, roles_by_key = _validated_roles(stage.get("role_catalog"))
    referenced_role_keys = {item["assessment"].get("matched_role_key") for item in approved_by_candidate.values()}
    if referenced_role_keys != set(roles_by_key):
        raise ValueError("interview stage role catalog must exactly cover approved candidates")

    handoff_dir = os.path.realpath(os.path.join(resolved_workspace, ".psi", "resume-approval", "interview-handoffs"))
    if not _inside(handoff_dir, resolved_workspace):
        raise ValueError("interview handoff directory escapes the workspace")
    os.makedirs(handoff_dir, exist_ok=True)

    receipt_records: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    seen_candidate_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TypeError(f"interview_manifest.records[{index}] must be an object")
        record_id = _required_text(record.get("record_id"), f"records[{index}].record_id")
        candidate_id = _required_text(record.get("candidate_id"), f"records[{index}].candidate_id")
        if _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError(f"records[{index}].record_id is invalid")
        if _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError(f"records[{index}].candidate_id is invalid")
        if record_id in seen_record_ids or candidate_id in seen_candidate_ids:
            raise ValueError("interview manifest contains a duplicate record or candidate")
        seen_record_ids.add(record_id)
        seen_candidate_ids.add(candidate_id)

        approved_item = approved_by_candidate.get(candidate_id)
        if approved_item is None:
            raise ValueError(f"records[{index}] does not match an approved candidate")
        assessment = approved_item.get("assessment")
        talent_record_id = approved_item.get("talent_record_id")
        if not isinstance(assessment, dict) or assessment.get("status") != "assessed":
            raise ValueError(f"approved assessment for {candidate_id} is invalid")
        assessment_revision = assessment.get("assessment_revision")
        if not isinstance(assessment_revision, str) or _REVISION.fullmatch(assessment_revision) is None:
            raise ValueError(f"approved assessment revision for {candidate_id} is invalid")
        if not isinstance(talent_record_id, str) or _RECORD_ID.fullmatch(talent_record_id) is None:
            raise ValueError(f"approved talent record id for {candidate_id} is invalid")
        if record.get("assessment_revision") != assessment_revision:
            raise ValueError(f"records[{index}] assessment revision does not match")
        if record.get("talent_record_id") != talent_record_id:
            raise ValueError(f"records[{index}] talent record id does not match")
        if record.get("candidate_name") != assessment.get("candidate_name"):
            raise ValueError(f"records[{index}] candidate name does not match")
        revisions = assessment.get("document_revisions")
        if (
            not isinstance(revisions, dict)
            or set(revisions) != {"resume_scoring_sha256", "role_information_sha256"}
            or not all(isinstance(value, str) and _REVISION.fullmatch(value) for value in revisions.values())
        ):
            raise ValueError(f"approved document revisions for {candidate_id} are invalid")
        if record.get("document_revisions") != revisions:
            raise ValueError(f"records[{index}] document revisions do not match")
        matched_role_key = _required_text(assessment.get("matched_role_key"), "assessment.matched_role_key")
        matched_role_name = _required_text(assessment.get("matched_role_name"), "assessment.matched_role_name")
        if record.get("matched_role_key") != matched_role_key:
            raise ValueError(f"records[{index}] matched role key does not match")
        matched_role = roles_by_key.get(matched_role_key)
        if matched_role is None or matched_role.get("name") != matched_role_name:
            raise ValueError(f"records[{index}] matched role does not match the role catalog")
        if revisions.get("role_information_sha256") != role_revision:
            raise ValueError(f"records[{index}] role document revision does not match the role catalog")
        fingerprint = record.get("row_fingerprint")
        fingerprint_fields = {
            "姓名",
            "目标岗位",
            "面试前摘要",
            "面试重点",
            "风险提示",
            "建议问题",
        }
        if (
            not isinstance(fingerprint, dict)
            or set(fingerprint) != fingerprint_fields
            or not all(isinstance(value, str) and value.strip() for value in fingerprint.values())
        ):
            raise ValueError(f"records[{index}] row fingerprint is invalid")
        if fingerprint["姓名"] != assessment.get("candidate_name") or fingerprint["目标岗位"] != matched_role_name:
            raise ValueError(f"records[{index}] row fingerprint identity does not match")

        handoff = {
            "schema_version": "2.0",
            "interview_record_id": record_id,
            "interview_table_id": manifest.get("table_id"),
            "batch_id": batch_id,
            "candidate_id": candidate_id,
            "candidate_name": assessment.get("candidate_name"),
            "talent_record_id": talent_record_id,
            "assessment_revision": assessment_revision,
            "document_revisions": revisions,
            "matched_role": matched_role,
            "assessment": assessment,
        }
        serialized = (
            json.dumps(
                handoff,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        destination = os.path.realpath(os.path.join(handoff_dir, f"{record_id}.json"))
        if not _inside(destination, handoff_dir):
            raise ValueError("interview handoff path escapes its directory")
        created = True
        if os.path.exists(destination):
            with open(destination, encoding="utf-8") as source:
                existing = source.read()
            if existing != serialized:
                raise ValueError(f"conflicting handoff already exists for {record_id}")
            created = False
        else:
            temporary = f"{destination}.tmp-{os.getpid()}"
            try:
                with open(temporary, "x", encoding="utf-8", newline="\n") as target:
                    target.write(serialized)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        receipt_records.append(
            {
                "interview_record_id": record_id,
                "candidate_id": candidate_id,
                "path": os.path.relpath(destination, resolved_workspace).replace(os.sep, "/"),
                "created": created,
            }
        )

    missing = sorted(set(approved_by_candidate) - seen_candidate_ids)
    if missing:
        raise ValueError(f"interview manifest is missing approved candidates: {', '.join(missing)}")
    return {
        "interview_record_ids": [record["interview_record_id"] for record in receipt_records],
        "interview_handoff_receipt": {
            "schema_version": "2.0",
            "status": "complete",
            "expected_count": len(approved),
            "records": receipt_records,
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
