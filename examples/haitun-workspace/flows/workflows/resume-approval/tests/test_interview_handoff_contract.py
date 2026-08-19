from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = WORKFLOW_ROOT / "programs" / "persist_interview_handoffs.py"
BATCH_ID = "resume-approval-20260809T120000Z"
INTERVIEW_RECORD_ID = "recInterview001"
TALENT_RECORD_ID = "recTalent00001"


def _questions() -> list[dict]:
    return [
        {
            "question": "请说明 Python 项目中你个人负责的关键工作。",
            "category": "真实性核验",
            "evidence_anchor": "项目经历\uff1a使用 Python",
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
    spec = importlib.util.spec_from_file_location("persist_interview_handoffs_task8", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assessment() -> dict:
    return {
        "schema_version": "3.0",
        "status": "assessed",
        "batch_id": BATCH_ID,
        "candidate_id": "a" * 16,
        "candidate_name": "测试候选人",
        "source": {
            "name": "candidate.pdf",
            "sha256": "a" * 64,
            "format": ".pdf",
            "extraction_mode": "read_pdf_tool",
            "extraction_quality": "good",
            "extraction_warnings": [],
        },
        "grade": "B",
        "education": "本科",
        "education_background": "测试大学\uff0c计算机专业",
        "total_score": 85,
        "matched_role_key": "role-0123456789abcdef01234567",
        "matched_role_name": "AI应用开发工程师",
        "match_points": [{"requirement": "Python", "resume_evidence": ["项目经历\uff1a使用 Python"]}],
        "mismatch_points": [],
        "interview_recommendation": "建议面试",
        "interview_recommendation_reason": "岗位匹配证据明确。",
        "verification_questions": _questions(),
        "document_revisions": {
            "resume_scoring_sha256": "b" * 64,
            "role_information_sha256": "c" * 64,
        },
        "assessment_revision": "d" * 64,
    }


def _role() -> dict:
    return {
        "role_key": "role-0123456789abcdef01234567",
        "name": "AI应用开发工程师",
        "employment_type": "正式",
        "location": "示例城市",
        "headcount": 2,
        "status": "active",
        "responsibilities": ["构建 AI 应用"],
        "hard_requirements": ["Python", "生产经验"],
        "preferences": ["自驱"],
        "source_evidence": [{"section": "岗位", "text": "AI应用开发工程师"}],
    }


def _inputs() -> dict:
    assessment = _assessment()
    return {
        "interview_manifest": {
            "schema_version": "4.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "table_id": "tblS1l6CxNfUOrbv",
            "expected_count": 1,
            "records": [
                {
                    "record_id": INTERVIEW_RECORD_ID,
                    "candidate_id": assessment["candidate_id"],
                    "candidate_name": assessment["candidate_name"],
                    "talent_record_id": TALENT_RECORD_ID,
                    "assessment_revision": assessment["assessment_revision"],
                    "document_revisions": copy.deepcopy(assessment["document_revisions"]),
                    "matched_role_key": assessment["matched_role_key"],
                    "row_fingerprint": {
                        "姓名": assessment["candidate_name"],
                        "目标岗位": assessment["matched_role_name"],
                        "面试前摘要": "摘要",
                        "面试重点": "1. 重点",
                        "风险提示": "- 风险",
                        "建议问题": _rendered_questions(),
                    },
                    "created": True,
                }
            ],
            "errors": [],
        },
        "interview_stage_bundle": {
            "schema_version": "1.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "decision_contract": {
                "schema_version": "3.0",
                "sha256": "e" * 64,
                "expected_review_count": 1,
                "approved_count": 1,
                "rejected_count": 0,
                "pending_count": 0,
            },
            "destination": {
                "app_token": "app-token",
                "base_url": "https://example.feishu.cn/base/app-token",
                "talent_pool_table_id": "tblTalent",
                "interview_table_id": "tblS1l6CxNfUOrbv",
            },
            "role_catalog": {
                "schema_version": "1.0",
                "source_document_sha256": "c" * 64,
                "roles": [_role()],
            },
            "approved": [
                {
                    "assessment": assessment,
                    "talent_record_id": TALENT_RECORD_ID,
                    "initial_status": "通过",
                }
            ],
        },
    }


def test_handoff_persists_dynamic_role_and_document_revisions(tmp_path: Path) -> None:
    """Workflow B must be able to prove the exact source documents, role, and talent row."""
    module = _load_module()

    result = module.run(_inputs(), str(tmp_path))

    assert result["interview_record_ids"] == [INTERVIEW_RECORD_ID]
    receipt = result["interview_handoff_receipt"]
    assert receipt["status"] == "complete"
    path = tmp_path / receipt["records"][0]["path"]
    handoff = json.loads(path.read_text(encoding="utf-8"))
    assessment = _assessment()
    assert handoff["schema_version"] == "2.0"
    assert handoff["talent_record_id"] == TALENT_RECORD_ID
    assert handoff["document_revisions"] == assessment["document_revisions"]
    assert handoff["matched_role"] == _role()
    assert handoff["matched_role"]["hard_requirements"] == ["Python", "生产经验"]
    assert handoff["assessment"] == assessment
    assert "target_role" not in handoff


def test_identical_handoff_rerun_reuses_the_exact_private_file(tmp_path: Path) -> None:
    """The same interview record and assessment must be idempotent without rewriting content."""
    module = _load_module()

    first = module.run(_inputs(), str(tmp_path))
    second = module.run(_inputs(), str(tmp_path))

    assert first["interview_handoff_receipt"]["records"][0]["created"] is True
    assert second["interview_handoff_receipt"]["records"][0]["created"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda inputs: inputs["interview_stage_bundle"]["approved"][0].update({"initial_status": "不通过"}),
            "initial_status",
        ),
        (
            lambda inputs: inputs["interview_manifest"]["records"][0].update({"talent_record_id": "recOtherTalent1"}),
            "talent record id",
        ),
        (
            lambda inputs: inputs["interview_manifest"]["records"][0].update({"matched_role_key": "role-other"}),
            "matched role key",
        ),
        (
            lambda inputs: inputs["interview_manifest"]["records"][0]["document_revisions"].update(
                {"resume_scoring_sha256": "e" * 64}
            ),
            "document revisions",
        ),
        (
            lambda inputs: inputs["interview_stage_bundle"]["role_catalog"]["roles"][0].update({"name": "其他岗位"}),
            "matched role",
        ),
        (
            lambda inputs: inputs["interview_stage_bundle"]["role_catalog"].update(
                {"source_document_sha256": "f" * 64}
            ),
            "role document revision",
        ),
    ],
)
def test_handoff_rejects_unapproved_or_mismatched_manifest_identity(tmp_path: Path, mutation, message: str) -> None:
    """Visible interview rows cannot be linked to another approval, role, or document version."""
    module = _load_module()
    inputs = _inputs()
    mutation(inputs)

    with pytest.raises(ValueError, match=message):
        module.run(inputs, str(tmp_path))


def test_empty_approved_stage_returns_complete_empty_receipt(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["interview_stage_bundle"]["approved"] = []
    inputs["interview_stage_bundle"]["role_catalog"]["roles"] = []
    inputs["interview_stage_bundle"]["decision_contract"].update(
        expected_review_count=1,
        approved_count=0,
        rejected_count=1,
    )
    inputs["interview_manifest"].update(expected_count=0, records=[])

    result = module.run(inputs, str(tmp_path))

    assert result["interview_record_ids"] == []
    assert result["interview_handoff_receipt"] == {
        "schema_version": "2.0",
        "status": "complete",
        "expected_count": 0,
        "records": [],
        "errors": [],
    }


def test_handoff_rejects_any_unreviewed_stage_contract(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["interview_stage_bundle"]["decision_contract"].update(pending_count=1)

    with pytest.raises(ValueError, match="pending"):
        module.run(inputs, str(tmp_path))


def test_workflow_a_ends_before_human_review_with_initial_review_handoff() -> None:
    """Workflow A must complete before the external Human review begins."""
    workflow = (WORKFLOW_ROOT / "resume-approval.workflow").read_text(encoding="utf-8")

    assert "const initial_review_handoff:Artifact;" in workflow
    assert "const initial_review_handoff_manifest:Artifact;" in workflow
    assert "const initial_review_request:Artifact;" in workflow
    assert "const persist_initial_review_handoff_step:Step;" in workflow
    assert "program_path(initial_review_handoff_persister)" in workflow
    assert (
        "consumes(persist_initial_review_handoff_step) == "
        "[validated_candidate_assessments, talent_pool_manifest, role_catalog, batch_id, feishu_config];"
    ) in workflow
    assert (
        "produces(persist_initial_review_handoff_step) == "
        "[initial_review_handoff, initial_review_handoff_manifest, initial_review_request];"
    ) in workflow
    output_block = workflow[
        workflow.index("output_workflow(resume_approval)") : workflow.index(
            "];", workflow.index("output_workflow(resume_approval)")
        )
    ]
    assert "initial_review_handoff" in output_block
    assert "initial_review_request" in output_block
    assert "initial_decision_bundle" not in output_block
    assert "interview_stage_handoff" not in output_block
    assert "interview_manifest" not in output_block
    assert "interview_record_ids" not in output_block
    assert "interview_handoff_receipt" not in output_block
    assert "prepare_interviews_step" not in workflow
    assert ":Human,Executor" not in workflow
    assert "initial_human_review_step" not in workflow
    assert "collect_initial_decisions_step" not in workflow
    assert "feishu_bitable_create_records" in workflow  # Talent-table writes still belong to A.
    assert "allowed_tool(interview_agent" not in workflow
