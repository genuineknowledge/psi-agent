from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "assemble_interview_write_batch.py"
BATCH_ID = "resume-20260810-120000-123456"
CANDIDATE_ID = "a" * 16
ROLE_KEY = "role-0123456789abcdef01234567"


def _questions() -> list[dict]:
    return [
        {
            "question": "请说明 Python 项目中你个人负责的关键工作。",
            "category": "真实性核验",
            "evidence_anchor": "项目使用 Python",
            "purpose": "核实项目真实性和个人贡献。",
            "positive_signal": "能够说明个人职责和结果。",
            "risk_signal": "回答缺少个人职责或结果。",
        },
        {
            "question": "针对 Python 要求\uff0c请说明一次复杂问题的解决过程。",
            "category": "岗位匹配",
            "evidence_anchor": "Python",
            "purpose": "判断岗位所需的 Python 工程深度。",
            "positive_signal": "能够说明方案、取舍和结果。",
            "risk_signal": "回答仅列技术名词。",
        },
        {
            "question": "请澄清生产经验的具体范围和验证结果。",
            "category": "风险澄清",
            "evidence_anchor": "生产经验",
            "purpose": "澄清生产经验的证据缺口。",
            "positive_signal": "能够提供可验证案例。",
            "risk_signal": "案例缺少可验证结果。",
        },
    ]


def _rendered_questions() -> str:
    return "\n".join(
        f"{index}. [{item['category']}] {item['question']}" for index, item in enumerate(_questions(), start=1)
    )


def _load_module():
    spec = importlib.util.spec_from_file_location("assemble_interview_write_batch", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task() -> dict:
    assessment = {
        "schema_version": "3.0",
        "status": "assessed",
        "batch_id": BATCH_ID,
        "candidate_id": CANDIDATE_ID,
        "candidate_name": "测试候选人",
        "matched_role_key": ROLE_KEY,
        "matched_role_name": "AI应用开发工程师",
        "verification_questions": _questions(),
        "assessment_revision": "d" * 64,
        "document_revisions": {
            "resume_scoring_sha256": "b" * 64,
            "role_information_sha256": "c" * 64,
        },
    }
    return {
        "candidate_id": CANDIDATE_ID,
        "talent_record_id": "recTalent00001",
        "assessment": assessment,
        "matched_role": {
            "role_key": ROLE_KEY,
            "name": "AI应用开发工程师",
            "status": "active",
        },
    }


def _draft() -> dict:
    return {
        "schema_version": "1.0",
        "candidate_id": CANDIDATE_ID,
        "面试前摘要": "候选人摘要",
        "面试重点": "1. 验证 Python 工程能力\n2. 验证系统设计能力",
        "风险提示": "- 生产经验需要核实",
        "建议问题": _rendered_questions(),
        "candidate_name": "Agent 伪造姓名",
        "talent_record_id": "recForged00001",
    }


def _inputs() -> dict:
    return {
        "approved_interview_tasks": [_task()],
        "interview_drafts": [_draft()],
        "batch_id": BATCH_ID,
        "feishu_config": {"interview_table_id": "tblInterview"},
    }


def test_builds_authoritative_write_batch_and_ignores_agent_identity() -> None:
    module = _load_module()
    result = module.run(_inputs())

    batch = result["interview_write_batch"]
    assert batch["status"] == "complete"
    assert batch["expected_count"] == 1
    row = batch["records"][0]
    task = _task()
    assert row["candidate_id"] == task["candidate_id"]
    assert row["candidate_name"] == task["assessment"]["candidate_name"]
    assert row["talent_record_id"] == task["talent_record_id"]
    assert row["assessment_revision"] == task["assessment"]["assessment_revision"]
    assert row["document_revisions"] == task["assessment"]["document_revisions"]
    assert row["matched_role_key"] == ROLE_KEY
    assert row["row_fingerprint"] == {
        "姓名": "测试候选人",
        "目标岗位": "AI应用开发工程师",
        "面试前摘要": "候选人摘要",
        "面试重点": "1. 验证 Python 工程能力\n2. 验证系统设计能力",
        "风险提示": "- 生产经验需要核实",
        "建议问题": _rendered_questions(),
    }
    ignored = {
        warning["field"]
        for warning in result["draft_validation_manifest"]["warnings"]
        if warning["message"] == "ignored non-write field"
    }
    assert ignored == {"candidate_name", "talent_record_id"}


def test_normalizes_string_arrays_to_newline_text() -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["interview_drafts"][0]["风险提示"] = ["- 风险一", "- 风险二"]
    inputs["interview_drafts"][0]["建议问题"] = _rendered_questions().splitlines()

    result = module.run(inputs)
    fingerprint = result["interview_write_batch"]["records"][0]["row_fingerprint"]

    assert fingerprint["风险提示"] == "- 风险一\n- 风险二"
    assert fingerprint["建议问题"] == _rendered_questions()


def test_style_shortfalls_are_warnings_not_failures() -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["interview_drafts"][0]["面试重点"] = "只核实一个重点"

    result = module.run(inputs)

    manifest = result["draft_validation_manifest"]
    assert manifest["status"] == "complete"
    assert manifest["errors"] == []
    assert manifest["warnings"]


def test_rejects_independently_rewritten_suggested_questions() -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["interview_drafts"][0]["建议问题"] = "1. 请介绍一下自己"

    with pytest.raises(ValueError, match="exactly reuse"):
        module.run(inputs)


def test_accepts_legacy_generated_fields_contract() -> None:
    module = _load_module()
    inputs = _inputs()
    draft = inputs["interview_drafts"][0]
    generated = {field: draft.pop(field) for field in module._GENERATED_FIELDS}
    draft["generated_fields"] = generated
    draft["historical_record_count"] = 0
    draft["warnings"] = []

    result = module.run(inputs)

    assert result["interview_write_batch"]["records"][0]["row_fingerprint"]["面试前摘要"] == "候选人摘要"
    assert any(
        warning["field"] == "generated_fields" and warning["message"] == "accepted legacy nested fields"
        for warning in result["draft_validation_manifest"]["warnings"]
    )


def test_accepts_misplaced_metadata_and_ignores_forbidden_reference() -> None:
    module = _load_module()
    inputs = _inputs()
    draft = inputs["interview_drafts"][0]
    generated = {field: draft.pop(field) for field in module._GENERATED_FIELDS}
    generated["historical_record_count"] = 2
    generated["warnings"] = ["历史记录查询失败"]
    draft["generated_fields"] = generated
    draft["interview_task_ref"] = "must-not-be-trusted"

    result = module.run(inputs)

    manifest_warnings = result["draft_validation_manifest"]["warnings"]
    assert {warning["field"] for warning in manifest_warnings if warning["message"] == "ignored non-write field"} >= {
        "historical_record_count",
        "warnings",
        "interview_task_ref",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["interview_drafts"].clear(), "count"),
        (lambda data: data["interview_drafts"][0].update(candidate_id="e" * 16), "candidate"),
        (lambda data: data["interview_drafts"][0].pop("建议问题"), "建议问题"),
        (lambda data: data["interview_drafts"][0].update(面试前摘要="  "), "non-empty"),
        (lambda data: data["interview_drafts"][0].update(风险提示={"x": 1}), "text"),
        (lambda data: data["interview_drafts"][0].update(schema_version="2.0"), "schema"),
        (lambda data: data["approved_interview_tasks"][0]["assessment"].update(assessment_revision="bad"), "revision"),
        (
            lambda data: data["approved_interview_tasks"][0]["assessment"][
                "verification_questions"
            ].pop(),
            "3 to 6",
        ),
        (lambda data: data["approved_interview_tasks"][0].update(talent_record_id="bad"), "talent"),
        (lambda data: data["approved_interview_tasks"][0]["matched_role"].update(name="其他岗位"), "role"),
    ],
)
def test_rejects_non_writeable_or_ambiguous_structure(mutation, message: str) -> None:
    module = _load_module()
    inputs = _inputs()
    mutation(inputs)
    with pytest.raises((TypeError, ValueError), match=message):
        module.run(inputs)


def test_rejects_duplicate_task_candidate_identity() -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["approved_interview_tasks"].append(copy.deepcopy(inputs["approved_interview_tasks"][0]))
    inputs["interview_drafts"].append(copy.deepcopy(inputs["interview_drafts"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        module.run(inputs)


def test_zero_tasks_and_drafts_produce_complete_empty_batch() -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["approved_interview_tasks"] = []
    inputs["interview_drafts"] = []

    result = module.run(inputs)

    assert result["interview_write_batch"] == {
        "schema_version": "1.0",
        "status": "complete",
        "batch_id": BATCH_ID,
        "table_id": "tblInterview",
        "expected_count": 0,
        "records": [],
    }
    assert result["draft_validation_manifest"]["status"] == "complete"
