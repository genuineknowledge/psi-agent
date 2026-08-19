"""Deterministically validate a source-grounded runtime role catalog."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

_ALLOWED_EMPLOYMENT_TYPES = frozenset({"实习", "正式", "正式/实习", "未说明"})
_ALLOWED_STATUSES = frozenset({"active", "inactive", "unclear"})
_INACTIVE_MARKERS = ("暂停招聘", "停止招聘", "已关闭", "不再招聘", "已招满", "✅")
_FORBIDDEN_EVIDENCE_SECTIONS = ("人才库匹配", "首选候选人", "备选候选人", "候选人评分")
_ROLE_KEYS = frozenset(
    {
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
)


def _normalized_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def build_role_key(document_token: str, role_name: str) -> str:
    """Build a stable private role identity from the source document and normalized name."""
    material = f"{document_token}\n{_normalized_name(role_name)}".encode()
    return "role-" + hashlib.sha256(material).hexdigest()[:24]


def _role_document(reference_documents: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(reference_documents, list):
        return None, ["reference_documents_must_be_a_list"]
    matches = [
        item for item in reference_documents if isinstance(item, dict) and item.get("purpose") == "role_information"
    ]
    if len(matches) != 1:
        return None, ["reference_documents_must_contain_one_role_information_document"]
    document = matches[0]
    content = document.get("content")
    document_token = document.get("document_token")
    source_sha256 = document.get("content_sha256")
    if not isinstance(content, str) or not content.strip():
        return None, ["role_information_document_content_must_be_non_empty"]
    if not isinstance(document_token, str) or not document_token:
        return None, ["role_information_document_token_must_be_non_empty"]
    actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if source_sha256 != actual_sha256:
        return None, ["role_information_document_hash_mismatch"]
    return document, []


def _string_list(value: Any, path: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{path}_must_be_a_list")
        return []
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{path}[{index}]_must_be_non_empty_text")
            continue
        normalized.append(item.strip())
    return normalized


def _validate_one_role(
    role: Any,
    index: int,
    document: dict[str, Any],
    seen_names: set[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    path = f"roles[{index}]"
    errors: list[str] = []
    if not isinstance(role, dict):
        return None, [f"{path}.must_be_an_object"]
    if set(role) != _ROLE_KEYS:
        errors.append(f"{path}.fields_do_not_match_contract")

    content = document["content"]
    name_value = role.get("name")
    name = name_value.strip() if isinstance(name_value, str) else ""
    if not name:
        errors.append(f"{path}.name_must_be_non_empty")
    elif name not in content:
        errors.append(f"{path}.name_not_in_source")
    normalized_name = _normalized_name(name)
    if normalized_name:
        if normalized_name in seen_names:
            errors.append(f"{path}.duplicate_role_name")
        seen_names.add(normalized_name)

    employment_type = role.get("employment_type")
    if employment_type not in _ALLOWED_EMPLOYMENT_TYPES:
        errors.append(f"{path}.invalid_employment_type")
    status = role.get("status")
    if status not in _ALLOWED_STATUSES:
        errors.append(f"{path}.invalid_status")
    location_value = role.get("location")
    location = location_value.strip() if isinstance(location_value, str) else ""
    if not location:
        errors.append(f"{path}.location_must_be_non_empty")
    elif location != "未说明" and location not in content:
        errors.append(f"{path}.location_not_in_source")
    headcount = role.get("headcount")
    if isinstance(headcount, bool) or not isinstance(headcount, int) or headcount < 1:
        errors.append(f"{path}.headcount_must_be_a_positive_integer")

    responsibilities = _string_list(role.get("responsibilities"), f"{path}.responsibilities", errors)
    hard_requirements = _string_list(role.get("hard_requirements"), f"{path}.hard_requirements", errors)
    preferences = _string_list(role.get("preferences"), f"{path}.preferences", errors)
    for field_name, items in (
        ("responsibilities", responsibilities),
        ("hard_requirements", hard_requirements),
        ("preferences", preferences),
    ):
        for item_index, item in enumerate(items):
            if item not in content:
                errors.append(f"{path}.{field_name}[{item_index}]_not_in_source")

    evidence_value = role.get("source_evidence")
    evidence: list[dict[str, str]] = []
    if not isinstance(evidence_value, list) or not evidence_value:
        errors.append(f"{path}.source_evidence_must_be_non_empty")
    else:
        for evidence_index, item in enumerate(evidence_value):
            evidence_path = f"{path}.source_evidence[{evidence_index}]"
            if not isinstance(item, dict) or set(item) != {"section", "text"}:
                errors.append(f"{evidence_path}_must_match_contract")
                continue
            section_value = item.get("section")
            text_value = item.get("text")
            section = section_value.strip() if isinstance(section_value, str) else ""
            text = text_value.strip() if isinstance(text_value, str) else ""
            if not section or not text:
                errors.append(f"{evidence_path}_must_contain_text")
                continue
            if text not in content:
                errors.append(f"{evidence_path}.text_not_in_source")
            if any(marker in section for marker in _FORBIDDEN_EVIDENCE_SECTIONS):
                errors.append(f"{path}.historical_candidate_evidence_forbidden")
            evidence.append({"section": section, "text": text})
    if name and evidence and not any(name in item["text"] for item in evidence):
        errors.append(f"{path}.name_not_in_source_evidence")
    evidence_text = "\n".join(item["text"] for item in evidence)
    if status == "active" and any(marker in evidence_text for marker in _INACTIVE_MARKERS):
        errors.append(f"{path}.status_conflicts_with_source")

    if errors:
        return None, errors
    normalized_role = {
        "role_key": build_role_key(document["document_token"], name),
        "name": name,
        "employment_type": employment_type,
        "location": location,
        "headcount": headcount,
        "status": status,
        "responsibilities": responsibilities,
        "hard_requirements": hard_requirements,
        "preferences": preferences,
        "source_evidence": evidence,
    }
    return normalized_role, []


def _result(source_sha256: str, roles: list[dict[str, Any]], errors: list[str]) -> dict[str, Any]:
    active_count = sum(role.get("status") == "active" for role in roles)
    if not errors and active_count == 0:
        errors.append("role_catalog.must_contain_an_active_role")
    blocked = bool(errors)
    return {
        "role_catalog": {
            "schema_version": "1.0",
            "source_document_sha256": source_sha256,
            "roles": [] if blocked else roles,
        },
        "role_catalog_manifest": {
            "schema_version": "1.0",
            "status": "blocked" if blocked else "complete",
            "active_role_count": 0 if blocked else active_count,
            "errors": errors,
        },
    }


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    """Validate an Agent draft against the exact role-information document revision."""
    del workspace_root
    document, errors = _role_document(inputs.get("reference_documents"))
    if document is None:
        return _result("", [], errors)
    source_sha256 = document["content_sha256"]
    draft = inputs.get("role_catalog_draft")
    if not isinstance(draft, dict) or draft.get("schema_version") != "1.0":
        return _result(source_sha256, [], ["role_catalog_draft_must_match_schema_1.0"])
    roles_value = draft.get("roles")
    if not isinstance(roles_value, list) or not roles_value:
        return _result(source_sha256, [], ["role_catalog.roles_must_be_non_empty"])

    normalized_roles: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    seen_names: set[str] = set()
    for index, role in enumerate(roles_value):
        normalized, role_errors = _validate_one_role(role, index, document, seen_names)
        validation_errors.extend(role_errors)
        if normalized is not None:
            normalized_roles.append(normalized)
    return _result(source_sha256, normalized_roles, validation_errors)


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


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
