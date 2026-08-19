from __future__ import annotations

import json
import re
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = WORKFLOW_ROOT / "instructions" / "analyze-resume.md"
WORKFLOW_PATH = WORKFLOW_ROOT / "resume-approval.workflow"


def _json_examples() -> list[dict]:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    return [json.loads(body) for body in re.findall(r"```json\s*(.*?)\s*```", prompt, re.DOTALL)]


def _analyze_step(workflow: str) -> str:
    start = workflow.index("step_name(analyze_resume_step)")
    end = workflow.index("step_name(build_assessment_repairs_round_1_step)", start)
    return workflow[start:end]


def test_assessed_output_uses_the_dynamic_business_contract() -> None:
    """Restoring legacy score/profile fields must break the new table-facing contract."""
    examples = _json_examples()
    assessed = next(
        value["candidate_assessments"]
        for value in examples
        if value.get("candidate_assessments", {}).get("status") == "assessed"
    )

    assert set(assessed) == {
        "schema_version",
        "status",
        "batch_id",
        "candidate_id",
        "candidate_name",
        "source",
        "grade",
        "education",
        "education_background",
        "resume_summary",
        "total_score",
        "matched_role_key",
        "matched_role_name",
        "match_points",
        "mismatch_points",
        "interview_recommendation",
        "interview_recommendation_reason",
        "verification_questions",
        "document_revisions",
    }
    assert assessed["schema_version"] == "3.0"
    assert set(assessed["document_revisions"]) == {
        "resume_scoring_sha256",
        "role_information_sha256",
    }
    assert set(assessed["match_points"][0]) == {"requirement", "resume_evidence"}
    assert set(assessed["mismatch_points"][0]) == {"requirement", "resume_evidence"}
    assert 3 <= len(assessed["verification_questions"]) <= 6
    assert set(assessed["verification_questions"][0]) == {
        "question",
        "category",
        "evidence_anchor",
        "purpose",
        "positive_signal",
        "risk_signal",
    }
    assert assessed["resume_summary"] == [
        "- 独立交付生产级 Agent 系统",
        "- RAG 检索指标有量化提升",
    ]
    assert not {
        "target_role",
        "standard_versions",
        "candidate_profile",
        "role_match",
        "scoring_profile",
        "weights_used",
        "resume_scores",
        "capability_model",
        "ability_level",
        "strengths",
        "risks",
        "interview_plan",
        "model_recommendation",
    } & set(assessed)


def test_extraction_failure_preserves_source_and_document_identity() -> None:
    """An unreadable attachment must not lose the batch's immutable document revisions."""
    examples = _json_examples()
    failed = next(
        value["candidate_assessments"]
        for value in examples
        if value.get("candidate_assessments", {}).get("status") == "extraction_failed"
    )

    assert failed["schema_version"] == "3.0"
    assert set(failed) == {
        "schema_version",
        "status",
        "batch_id",
        "candidate_id",
        "candidate_name",
        "source",
        "document_revisions",
        "failure",
    }


def test_prompt_forbids_invented_roles_and_defines_the_interview_gate() -> None:
    """Recommendation must be a deterministic resource gate without inventing negative evidence."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "matched_role_key" in prompt
    assert "只能来自 `role_catalog`" in prompt
    assert "unknown" in prompt
    assert "不得把缺失信息断言成候选人不具备该能力" in prompt
    assert "需在面试中核实" in prompt
    assert "评级为 A 或 B" in prompt
    assert "C、D、E、F" in prompt
    assert "每一项 `hard_requirements`" in prompt
    assert "同批候选人" in prompt
    assert "具体岗位证据缺口" in prompt
    assert "不得把评级或分数本身作为主要理由" in prompt


def test_prompt_keeps_education_and_school_fields_pure() -> None:
    """Table-facing education fields must not absorb major, cohort, or profile summaries."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    examples = _json_examples()
    assessed = next(
        value["candidate_assessments"]
        for value in examples
        if value.get("candidate_assessments", {}).get("status") == "assessed"
    )

    assert assessed["education"] == "硕士"
    assert assessed["education_background"] == "本科\uff1a示例大学 A\uff1b硕士\uff1a示例大学 B"
    assert "博士|硕士|本科|专科|高中及以下|unknown" in prompt
    assert "不得包含专业、研究方向、在读状态、毕业届别、工作年限或院校名称" in prompt
    assert "只填写带学历阶段的院校名称" in prompt
    assert "即使只有一段教育经历" in prompt
    assert "本科\uff1a示例大学 A" in prompt
    assert "985/211" in prompt


def test_prompt_requires_both_structured_role_point_lists() -> None:
    """Both table-facing point columns must be populated without weakening structured validation."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    examples = _json_examples()
    assessed = next(
        value["candidate_assessments"]
        for value in examples
        if value.get("candidate_assessments", {}).get("status") == "assessed"
    )

    assert "`match_points` 和 `mismatch_points` 都必须至少包含 1 项" in prompt
    assert "岗位风险或证据缺口" in prompt
    assert assessed["match_points"]
    assert assessed["mismatch_points"]
    assert set(assessed["match_points"][0]) == {"requirement", "resume_evidence"}
    assert set(assessed["mismatch_points"][0]) == {"requirement", "resume_evidence"}


def test_prompt_requires_evidence_backed_safe_verification_questions() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "3\u20136" in prompt
    assert all(category in prompt for category in ("真实性核验", "岗位匹配", "风险澄清"))
    assert "evidence_anchor" in prompt
    assert "positive_signal" in prompt and "risk_signal" in prompt
    assert "受保护属性" in prompt
    assert "不得把未知信息断言为负面事实" in prompt


def test_analyzer_consumes_fixed_online_documents_and_runtime_roles() -> None:
    """Reintroducing local standards or a configured target role must fail graph validation."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    block = _analyze_step(workflow)

    assert (
        "consumes(analyze_resume_step) == [extracted_resume, reference_documents, role_catalog, batch_id];"
    ) in block
    assert "extracted_standards" not in block
    assert "target_role" not in block
    assert "depends_on(analyze_resume_step, assert_reference_documents_ready_step) == True;" in block
    assert "depends_on(analyze_resume_step, assert_role_catalog_ready_step) == True;" in block


def test_role_catalog_readiness_gate_retries_one_transient_failure() -> None:
    """A transient Program-Agent failure must get one retry before aborting the batch."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "max_attempts(assert_role_catalog_ready_step) == 2;" in workflow


def test_analyzer_system_prompt_names_the_dynamic_authorities() -> None:
    """The short system prompt must reinforce the same authorities as the long instruction."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    line = next(item for item in workflow.splitlines() if item.startswith("    agent_system_prompt(resume_analyzer)"))

    assert "online scoring document" in line
    assert "validated runtime role catalog" in line
    assert "normalized education level and institution names only" in line
    assert "safe evidence-backed verification question bank" in line
    assert "deterministic interview gate" in line
    assert "configured role" not in line
