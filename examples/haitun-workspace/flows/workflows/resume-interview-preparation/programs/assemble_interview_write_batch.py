"""Join one-candidate drafts to authoritative A2 tasks and build a Feishu write batch."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_RECORD_ID = re.compile(r"^rec[A-Za-z0-9]{8,64}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_GENERATED_FIELDS = ("面试前摘要", "面试重点", "风险提示", "建议问题")
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
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be non-empty text")
    return value.strip()


def _normalize_text(value: Any, path: str) -> tuple[str, bool]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{path} must be non-empty text")
        return text, False
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{path} must be a non-empty text array")
        lines: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise TypeError(f"{path}[{index}] must be non-empty text")
            lines.append(item.strip())
        return "\n".join(lines), True
    raise TypeError(f"{path} must be text or a text array")


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


def _validate_task(task: Any, batch_id: str, index: int) -> tuple[str, dict[str, Any]]:
    path = f"approved_interview_tasks[{index}]"
    if not isinstance(task, dict):
        raise TypeError(f"{path} must be an object")
    candidate_id = task.get("candidate_id")
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        raise ValueError(f"{path}.candidate_id is invalid")
    talent_record_id = task.get("talent_record_id")
    if not isinstance(talent_record_id, str) or _RECORD_ID.fullmatch(talent_record_id) is None:
        raise ValueError(f"{path}.talent_record_id is invalid")
    assessment = task.get("assessment")
    if not isinstance(assessment, dict):
        raise TypeError(f"{path}.assessment must be an object")
    if assessment.get("schema_version") != "3.0" or assessment.get("status") != "assessed":
        raise ValueError(f"{path}.assessment must be assessed schema 3.0")
    if assessment.get("batch_id") != batch_id or assessment.get("candidate_id") != candidate_id:
        raise ValueError(f"{path} candidate or batch identity does not match its assessment")
    _required_text(assessment.get("candidate_name"), f"{path}.assessment.candidate_name")
    revision = assessment.get("assessment_revision")
    if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
        raise ValueError(f"{path}.assessment revision is invalid")
    revisions = assessment.get("document_revisions")
    if (
        not isinstance(revisions, dict)
        or set(revisions) != {"resume_scoring_sha256", "role_information_sha256"}
        or not all(isinstance(value, str) and _REVISION.fullmatch(value) for value in revisions.values())
    ):
        raise ValueError(f"{path}.assessment document revisions are invalid")
    role = task.get("matched_role")
    if not isinstance(role, dict) or role.get("status") != "active":
        raise ValueError(f"{path}.matched role is invalid")
    if role.get("role_key") != assessment.get("matched_role_key") or role.get("name") != assessment.get(
        "matched_role_name"
    ):
        raise ValueError(f"{path}.matched role does not match its assessment")
    _render_questions(
        assessment.get("verification_questions"),
        f"{path}.assessment.verification_questions",
        require_risk=bool(assessment.get("mismatch_points")),
    )
    return candidate_id, task


def _style_warnings(candidate_id: str, fields: dict[str, str]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for field, preferred_lines in (("面试重点", 2), ("建议问题", 2)):
        nonempty_lines = [line for line in fields[field].splitlines() if line.strip()]
        if len(nonempty_lines) < preferred_lines:
            warnings.append(
                {
                    "candidate_id": candidate_id,
                    "field": field,
                    "message": f"preferred at least {preferred_lines} list items",
                }
            )
    return warnings


def _ignored_field_warning(candidate_id: str, field: str) -> dict[str, str]:
    return {
        "candidate_id": candidate_id,
        "field": field,
        "message": "ignored non-write field",
    }


def _extract_generated_fields(
    draft: dict[str, Any], candidate_id: str, index: int
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    path = f"interview_drafts[{index}]"
    extraction_warnings: list[dict[str, str]] = []
    if all(field in draft for field in _GENERATED_FIELDS):
        generated = draft
        field_path = path
        if "generated_fields" in draft:
            extraction_warnings.append(_ignored_field_warning(candidate_id, "generated_fields"))
    else:
        generated = draft.get("generated_fields")
        if not isinstance(generated, dict):
            missing = next((field for field in _GENERATED_FIELDS if field not in draft), None)
            if missing is not None:
                raise ValueError(f"{path} is missing {missing}")
            raise TypeError(f"{path} must contain writeable interview fields")
        field_path = f"{path}.generated_fields"
        extraction_warnings.append(
            {
                "candidate_id": candidate_id,
                "field": "generated_fields",
                "message": "accepted legacy nested fields",
            }
        )

    allowed_top_level = {"schema_version", "candidate_id", "generated_fields", *_GENERATED_FIELDS}
    for field in sorted(set(draft) - allowed_top_level):
        extraction_warnings.append(_ignored_field_warning(candidate_id, field))
    if generated is not draft:
        for field in sorted(set(generated) - set(_GENERATED_FIELDS)):
            extraction_warnings.append(_ignored_field_warning(candidate_id, field))
    return generated, field_path, extraction_warnings


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    del workspace_root
    batch_id = _required_text(_decode(inputs.get("batch_id")), "batch_id")
    if _BATCH_ID.fullmatch(batch_id) is None:
        raise ValueError("batch_id is invalid")
    config = _decode(inputs.get("feishu_config"))
    if not isinstance(config, dict):
        raise TypeError("feishu_config must be an object")
    table_id = _required_text(config.get("interview_table_id"), "feishu_config.interview_table_id")
    tasks = _decode(inputs.get("approved_interview_tasks"))
    drafts = _decode(inputs.get("interview_drafts"))
    if not isinstance(tasks, list) or not isinstance(drafts, list):
        raise TypeError("approved_interview_tasks and interview_drafts must be lists")
    if len(tasks) != len(drafts):
        raise ValueError("task and draft count must match")

    records: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    seen_candidates: set[str] = set()
    seen_talent_records: set[str] = set()
    for index, (raw_task, raw_draft) in enumerate(zip(tasks, drafts, strict=True)):
        candidate_id, task = _validate_task(_decode(raw_task), batch_id, index)
        talent_record_id = task["talent_record_id"]
        if candidate_id in seen_candidates or talent_record_id in seen_talent_records:
            raise ValueError("approved interview tasks contain a duplicate candidate or talent record")
        seen_candidates.add(candidate_id)
        seen_talent_records.add(talent_record_id)

        draft = _decode(raw_draft)
        if isinstance(draft, dict) and set(draft) == {"interview_drafts"}:
            draft = _decode(draft["interview_drafts"])
        if not isinstance(draft, dict) or draft.get("schema_version") != "1.0":
            raise ValueError(f"interview_drafts[{index}] must use schema 1.0")
        if draft.get("candidate_id") != candidate_id:
            raise ValueError(f"interview_drafts[{index}] candidate identity does not match its task")
        generated, field_path, extraction_warnings = _extract_generated_fields(draft, candidate_id, index)
        warnings.extend(extraction_warnings)
        normalized: dict[str, str] = {}
        for field in _GENERATED_FIELDS:
            if field not in generated:
                raise ValueError(f"{field_path} is missing {field}")
            normalized[field], converted = _normalize_text(generated[field], f"{field_path}.{field}")
            if converted:
                warnings.append(
                    {
                        "candidate_id": candidate_id,
                        "field": field,
                        "message": "normalized text array to newline-separated text",
                    }
                )
        warnings.extend(_style_warnings(candidate_id, normalized))

        assessment = task["assessment"]
        expected_questions = _render_questions(
            assessment["verification_questions"],
            f"approved_interview_tasks[{index}].assessment.verification_questions",
            require_risk=bool(assessment.get("mismatch_points")),
        )
        if normalized["建议问题"] != expected_questions:
            raise ValueError(
                f"interview_drafts[{index}].建议问题 must exactly reuse the assessment verification questions"
            )
        normalized["建议问题"] = expected_questions
        records.append(
            {
                "candidate_id": candidate_id,
                "candidate_name": assessment["candidate_name"],
                "talent_record_id": talent_record_id,
                "assessment_revision": assessment["assessment_revision"],
                "document_revisions": assessment["document_revisions"],
                "matched_role_key": assessment["matched_role_key"],
                "row_fingerprint": {
                    "姓名": assessment["candidate_name"],
                    "目标岗位": assessment["matched_role_name"],
                    **normalized,
                },
            }
        )

    return {
        "interview_write_batch": {
            "schema_version": "1.0",
            "status": "complete",
            "batch_id": batch_id,
            "table_id": table_id,
            "expected_count": len(records),
            "records": records,
        },
        "draft_validation_manifest": {
            "schema_version": "1.0",
            "status": "complete",
            "expected_count": len(tasks),
            "validated_count": len(records),
            "warnings": warnings,
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
