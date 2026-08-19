from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = WORKFLOW_ROOT / "programs" / "persist_initial_review_handoff.py"
BATCH_ID = "resume-20260810-120000-123456"
CANDIDATE_ID = "a" * 16
ASSESSMENT_REVISION = "d" * 64
ROLE_REVISION = "c" * 64
ROLE_KEY = "role-0123456789abcdef01234567"
TALENT_RECORD_ID = "recTalent00001"


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


def _row_fingerprint() -> dict:
    return {
        "姓名": "测试候选人",
        "评级": "B",
        "学历": "硕士",
        "毕业院校/背景": "硕士\uff1a测试大学",
        "简历摘要": "- 有相关项目经验\n- 完成可验证交付",
        "总分": 82,
        "匹配岗位": "AI应用开发工程师",
        "匹配点": (
            "- 要求\uff1aPython\uff1b证据\uff1a项目使用 Python、独立交付\n"
            "- 要求\uff1aAI 应用\uff1b证据\uff1a完成应用原型"
        ),
        "不匹配点": (
            "- 风险\uff1a生产经验\uff1b依据\uff1a简历未体现、需要核实\n"
            "- 风险\uff1a规模化\uff1b依据\uff1a缺少规模数据"
        ),
        "面试建议": "建议面试",
        "面试建议理由": "核心技能基本匹配。",
        "问题库": _rendered_questions(),
    }


def _load_module():
    spec = importlib.util.spec_from_file_location("persist_initial_review_handoff", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assessment() -> dict:
    return {
        "schema_version": "3.0",
        "status": "assessed",
        "batch_id": BATCH_ID,
        "candidate_id": CANDIDATE_ID,
        "candidate_name": "测试候选人",
        "grade": "B",
        "education": "硕士",
        "education_background": "硕士\uff1a测试大学",
        "resume_summary": ["- 有相关项目经验", "- 完成可验证交付"],
        "total_score": 82,
        "matched_role_key": ROLE_KEY,
        "matched_role_name": "AI应用开发工程师",
        "match_points": [
            {"requirement": "Python", "resume_evidence": ["项目使用 Python", "独立交付"]},
            {"requirement": "AI 应用", "resume_evidence": ["完成应用原型"]},
        ],
        "mismatch_points": [
            {"requirement": "生产经验", "resume_evidence": ["简历未体现", "需要核实"]},
            {"requirement": "规模化", "resume_evidence": ["缺少规模数据"]},
        ],
        "interview_recommendation": "建议面试",
        "interview_recommendation_reason": "核心技能基本匹配。",
        "verification_questions": _questions(),
        "document_revisions": {
            "resume_scoring_sha256": "b" * 64,
            "role_information_sha256": ROLE_REVISION,
        },
        "assessment_revision": ASSESSMENT_REVISION,
    }


def _role() -> dict:
    return {
        "role_key": ROLE_KEY,
        "name": "AI应用开发工程师",
        "status": "active",
    }


def _inputs() -> dict:
    assessment = _assessment()
    return {
        "batch_id": BATCH_ID,
        "validated_candidate_assessments": {
            "schema_version": "3.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "document_revisions": copy.deepcopy(assessment["document_revisions"]),
            "assessments": [assessment],
            "failed_candidates": [],
            "constraint_warnings": [],
        },
        "talent_pool_manifest": {
            "schema_version": "4.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "base_url": "https://example.feishu.cn/base/app-token",
            "table_id": "tblTalent",
            "view_name": "候选人看板",
            "expected_count": 1,
            "failed_candidates": [],
            "records": [
                {
                    "record_id": TALENT_RECORD_ID,
                    "candidate_id": CANDIDATE_ID,
                    "assessment_revision": ASSESSMENT_REVISION,
                    "row_fingerprint": _row_fingerprint(),
                    "attachment_persisted": True,
                    "created": True,
                }
            ],
            "errors": [],
        },
        "role_catalog": {
            "schema_version": "1.0",
            "source_document_sha256": ROLE_REVISION,
            "roles": [_role()],
        },
        "feishu_config": {
            "app_token": "app-token",
            "base_url": "https://example.feishu.cn/base/app-token",
            "talent_pool_table_id": "tblTalent",
            "interview_table_id": "tblInterview",
            "user_key": "must-not-cross-stage",
        },
    }


def _apply_warning_only_values(data: dict) -> None:
    assessment = data["validated_candidate_assessments"]["assessments"][0]
    assessment.update(
        education="",
        education_background="",
        resume_summary=[],
        match_points=[],
        mismatch_points=[{"requirement": "", "resume_evidence": []}],
        interview_recommendation_reason="",
    )
    data["validated_candidate_assessments"]["constraint_warnings"] = ["content remains table-writeable"]
    data["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
        学历="",
        **{
            "毕业院校/背景": "",
            "简历摘要": "",
            "匹配点": "",
            "不匹配点": "- 风险\uff1a\uff1b依据\uff1a",
            "面试建议理由": "",
        },
    )


def test_persists_review_source_and_returns_launch_contract(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()

    result = module.run(inputs, str(tmp_path))

    descriptor = result["initial_review_handoff"]
    assert set(descriptor) == {
        "schema_version",
        "status",
        "batch_id",
        "expected_count",
        "path",
        "sha256",
        "next_workflow",
        "next_input",
    }
    assert descriptor["status"] == "ready_for_review"
    assert descriptor["expected_count"] == 1
    assert descriptor["next_workflow"] == "resume-interview-preparation"
    assert descriptor["next_input"] == "initial_review_handoff"
    destination = tmp_path / descriptor["path"]
    payload_bytes = destination.read_bytes()
    assert hashlib.sha256(payload_bytes).hexdigest() == descriptor["sha256"]

    payload = json.loads(payload_bytes)
    assert set(payload) == {
        "schema_version",
        "status",
        "batch_id",
        "destination",
        "role_catalog",
        "validated_candidate_assessments",
        "talent_pool_manifest",
    }
    assert payload["status"] == "ready_for_review"
    assert payload["validated_candidate_assessments"] == inputs["validated_candidate_assessments"]
    assert payload["talent_pool_manifest"] == inputs["talent_pool_manifest"]
    assert set(payload["destination"]) == {
        "app_token",
        "base_url",
        "talent_pool_table_id",
        "interview_table_id",
    }
    assert result["initial_review_handoff_manifest"] == {
        "schema_version": "1.0",
        "status": "complete",
        "expected_count": 1,
        "errors": [],
    }
    request = result["initial_review_request"]
    assert BATCH_ID in request
    assert "候选人看板" in request
    assert "只修改「初审状态」" in request
    assert "通过" in request and "不通过" in request
    assert "只回复\uff1a初审完成" in request
    assert "workflow:resume-interview-preparation" in request
    assert "待审批" in request and "拒绝执行" in request
    assert "问题库" in request
    assert "已完成初审\uff0c继续" not in request


def test_persists_warning_only_table_writeable_values(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()
    _apply_warning_only_values(inputs)

    result = module.run(inputs, str(tmp_path))

    assert result["initial_review_handoff"]["status"] == "ready_for_review"
    payload = json.loads((tmp_path / result["initial_review_handoff"]["path"]).read_bytes())
    assert payload["validated_candidate_assessments"] == inputs["validated_candidate_assessments"]
    assert payload["talent_pool_manifest"] == inputs["talent_pool_manifest"]


def test_identical_publication_reuses_exact_bytes(tmp_path: Path) -> None:
    module = _load_module()
    first = module.run(_inputs(), str(tmp_path))["initial_review_handoff"]
    path = tmp_path / first["path"]
    before = path.read_bytes()

    second = module.run(_inputs(), str(tmp_path))["initial_review_handoff"]

    assert second == first
    assert path.read_bytes() == before


def test_conflicting_publication_is_not_overwritten(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / ".psi" / "resume-approval" / "initial-review-handoffs" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="conflicting"):
        module.run(_inputs(), str(tmp_path))

    assert path.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["validated_candidate_assessments"].update(status="blocked"), "complete"),
        (lambda data: data["talent_pool_manifest"].update(expected_count=2), "expected_count"),
        (
            lambda data: data["talent_pool_manifest"]["records"][0].update(candidate_id="e" * 16),
            "cover",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0].update(assessment_revision="f" * 64),
            "revision",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0]["row_fingerprint"].pop("面试建议理由"),
            "row_fingerprint",
        ),
        (
            lambda data: data["validated_candidate_assessments"]["assessments"][0][
                "verification_questions"
            ].pop(),
            "3 to 6",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
                问题库="1. [真实性核验] 被篡改的问题"
            ),
            "问题库",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(总分=99),
            "总分",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
                简历摘要="- 被篡改的摘要"
            ),
            "简历摘要",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
                匹配点="- 要求\uff1aPython\uff1b证据\uff1a被篡改"
            ),
            "匹配点",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
                不匹配点="- 风险\uff1a生产经验\uff1b依据\uff1a被篡改"
            ),
            "不匹配点",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0].update(attachment_persisted=False),
            "attachment_persisted",
        ),
        (
            lambda data: data["talent_pool_manifest"]["records"][0].update(file_token="must-not-persist"),
            "attachment-safe",
        ),
        (lambda data: data["validated_candidate_assessments"].update(assessments=[]), "non-empty"),
    ],
)
def test_rejects_incomplete_or_mismatched_review_source(tmp_path: Path, mutation, message: str) -> None:
    module = _load_module()
    inputs = _inputs()
    mutation(inputs)

    with pytest.raises((TypeError, ValueError), match=message):
        module.run(inputs, str(tmp_path))

    assert not (tmp_path / ".psi" / "resume-approval" / "initial-review-handoffs" / f"{BATCH_ID}.json").exists()
