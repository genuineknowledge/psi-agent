from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = WORKFLOW_ROOT / "programs"
VALIDATOR_PATH = PROGRAMS / "validate_candidate_assessments.py"
PIPELINE_PATH = PROGRAMS / "assessment_repair_pipeline.py"
READINESS_PATH = PROGRAMS / "assert_workflow_ready.py"
BATCH_ID = "resume-approval-20260809T120000Z"

SCORING_CONTENT = """简历评分标准
更新日期\uff1a2026.08.09 · 满分100分
1. AI基础\uff0820分\uff09
2. 岗位经验\uff0820分\uff09
3. 工程能力\uff0820分\uff09
4. 成长潜力\uff0820分\uff09
5. 实习稳定性\uff0810分\uff09
6. 文化匹配\uff0810分\uff09
评级对照
评级
总分
A
90+
B
80-89
C
70-79
D
60-69
E
F
50-59
<50
"""
ROLE_CONTENT = """招聘需求总览
AI应用开发工程师
研发-工程
构建 AI 应用
Python
自驱
"""


def _load(name: str, path: Path):
    sys.path.insert(0, str(PROGRAMS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validator():
    return _load("validate_candidate_assessments_task6", VALIDATOR_PATH)


def _pipeline():
    validator = _validator()
    sys.modules["validate_candidate_assessments"] = validator
    return _load("assessment_repair_pipeline_task6", PIPELINE_PATH)


def _readiness_gate():
    return _load("assert_workflow_ready_task6", READINESS_PATH)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _documents(scoring: str = SCORING_CONTENT) -> list[dict]:
    return [
        {
            "purpose": "resume_scoring",
            "content": scoring,
            "content_sha256": _sha(scoring),
            "title": "简历评分标准",
        },
        {
            "purpose": "role_information",
            "content": ROLE_CONTENT,
            "content_sha256": _sha(ROLE_CONTENT),
            "title": "招聘需求总览",
        },
    ]


def _catalog() -> dict:
    return {
        "schema_version": "1.0",
        "source_document_sha256": _sha(ROLE_CONTENT),
        "roles": [
            {
                "role_key": "role-0123456789abcdef01234567",
                "name": "AI应用开发工程师",
                "employment_type": "正式",
                "location": "示例城市",
                "headcount": 1,
                "status": "active",
                "responsibilities": ["构建 AI 应用"],
                "hard_requirements": ["Python"],
                "preferences": ["自驱"],
                "source_evidence": [{"section": "岗位", "text": "AI应用开发工程师"}],
            }
        ],
    }


def _assessment() -> dict:
    source_sha = "a" * 64
    return {
        "schema_version": "3.0",
        "status": "assessed",
        "batch_id": BATCH_ID,
        "candidate_id": source_sha[:16],
        "candidate_name": "测试候选人",
        "source": {
            "name": "candidate.pdf",
            "sha256": source_sha,
            "format": ".pdf",
            "extraction_mode": "read_pdf_tool",
            "extraction_quality": "good",
            "extraction_warnings": [],
        },
        "grade": "B",
        "education": "本科",
        "education_background": "本科\uff1a测试大学",
        "resume_summary": ["- 独立完成 Python 项目", "- 有可量化的工程交付成果"],
        "total_score": 85,
        "matched_role_key": "role-0123456789abcdef01234567",
        "matched_role_name": "AI应用开发工程师",
        "match_points": [{"requirement": "Python", "resume_evidence": ["项目经历\uff1a使用 Python"]}],
        "mismatch_points": [
            {
                "requirement": "自驱",
                "resume_evidence": ["简历未体现可验证的独立交付案例\uff0c需在面试中核实"],
            }
        ],
        "interview_recommendation": "建议面试",
        "interview_recommendation_reason": "Python 项目证据明确\uff0c建议面试核实工程深度。",
        "verification_questions": [
            {
                "question": "请说明 Python 项目中你个人负责的关键工作、验证方式和结果。",
                "category": "真实性核验",
                "evidence_anchor": "项目经历\uff1a使用 Python",
                "purpose": "核实 Python 项目证据的真实性和个人贡献。",
                "positive_signal": "能够说明个人职责、关键决策和可验证结果。",
                "risk_signal": "回答停留在团队概述\uff0c不能说明个人贡献。",
            },
            {
                "question": "针对 Python 要求\uff0c请说明你处理过的最复杂工程问题和取舍。",
                "category": "岗位匹配",
                "evidence_anchor": "Python",
                "purpose": "判断 Python 工程能力是否达到岗位要求。",
                "positive_signal": "能够给出具体方案、取舍依据和结果。",
                "risk_signal": "只列技术名词\uff0c缺少具体决策和结果。",
            },
            {
                "question": "简历未体现可验证的独立交付案例\uff0c需在面试中核实\uff1b请补充一个完整案例。",
                "category": "风险澄清",
                "evidence_anchor": "简历未体现可验证的独立交付案例\uff0c需在面试中核实",
                "purpose": "澄清独立交付证据缺口\uff0c不把未知信息视为负面事实。",
                "positive_signal": "能够提供职责边界、交付物和验证结果。",
                "risk_signal": "案例缺少个人职责或可验证交付结果。",
            },
        ],
        "document_revisions": {
            "resume_scoring_sha256": _sha(SCORING_CONTENT),
            "role_information_sha256": _sha(ROLE_CONTENT),
        },
    }


def _inputs(assessments: list[dict], scoring: str = SCORING_CONTENT) -> dict:
    return {
        "candidate_assessments": assessments,
        "reference_documents": _documents(scoring),
        "role_catalog": _catalog(),
        "batch_id": BATCH_ID,
    }


def test_scoring_contract_parses_split_e_f_cells_and_sums_to_100() -> None:
    """The approved Feishu line-break layout must still produce one continuous A-F contract."""
    validator = _validator()

    contract, errors = validator.parse_scoring_contract(_documents())

    assert errors == []
    assert contract["total_max"] == 100
    assert [item["max"] for item in contract["dimensions"]] == [20, 20, 20, 20, 10, 10]
    assert contract["grade_ranges"] == {
        "A": [90, 100],
        "B": [80, 89],
        "C": [70, 79],
        "D": [60, 69],
        "E": [50, 59],
        "F": [0, 49],
    }


def test_valid_dynamic_assessment_is_canonicalized_and_revisioned() -> None:
    """A valid assessment must retain exact role/document identity and gain a deterministic revision."""
    validator = _validator()

    result = validator.run(_inputs([_assessment()]))

    assert result["errors"] == []
    assert result["assessment_validation_manifest"]["status"] == "complete"
    validated = result["validated_candidate_assessments"]
    assert validated["status"] == "complete"
    assert validated["document_revisions"] == _assessment()["document_revisions"]
    assert len(validated["assessments"]) == 1
    assert len(validated["assessments"][0]["assessment_revision"]) == 64


def test_question_bank_accepts_three_and_six_question_boundaries_and_renders_stably() -> None:
    validator = _validator()
    assessment = _assessment()

    three = validator.run(_inputs([assessment]))

    assert three["errors"] == []
    assert validator.render_verification_questions(assessment["verification_questions"]) == (
        "1. [真实性核验] 请说明 Python 项目中你个人负责的关键工作、验证方式和结果。\n"
        "2. [岗位匹配] 针对 Python 要求\uff0c请说明你处理过的最复杂工程问题和取舍。\n"
        "3. [风险澄清] 简历未体现可验证的独立交付案例\uff0c需在面试中核实\uff1b请补充一个完整案例。"
    )

    for suffix, original in enumerate(copy.deepcopy(assessment["verification_questions"]), start=1):
        original["question"] = f"{original['question']} 补充追问 {suffix}。"
        assessment["verification_questions"].append(original)
    six = validator.run(_inputs([assessment]))

    assert six["errors"] == []
    assert len(six["validated_candidate_assessments"]["assessments"][0]["verification_questions"]) == 6


def test_risk_question_coverage_is_conditional_on_material_mismatch_points() -> None:
    validator = _validator()
    assessment = _assessment()
    assessment["mismatch_points"] = []
    assessment["verification_questions"][2] = {
        **copy.deepcopy(assessment["verification_questions"][1]),
        "question": "针对 Python 要求\uff0c请再说明一次性能优化取舍。",
    }

    errors = validator._validate_verification_questions(
        assessment["verification_questions"],
        "candidate_assessments[0].verification_questions",
        assessment,
        _catalog()["roles"][0],
    )

    assert errors == []


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (lambda value: value["verification_questions"].pop(), "must contain 3 to 6 questions"),
        (
            lambda value: value["verification_questions"].extend(
                [
                    {
                        **copy.deepcopy(value["verification_questions"][0]),
                        "question": f"Python 真实性补充问题 {index}",
                    }
                    for index in range(4)
                ]
            ),
            "must contain 3 to 6 questions",
        ),
        (
            lambda value: value["verification_questions"][0].pop("purpose"),
            ".missing_fields:purpose",
        ),
        (
            lambda value: value["verification_questions"][0].update(category="通用问题"),
            ".category must be one of:",
        ),
        (
            lambda value: value["verification_questions"][2].update(category="岗位匹配"),
            "must include category 风险澄清",
        ),
        (
            lambda value: value["verification_questions"][0].update(
                question="请介绍一下自己。",
            ),
            ".question is not linked to its evidence_anchor",
        ),
        (
            lambda value: value["verification_questions"][1].update(
                question=value["verification_questions"][0]["question"],
            ),
            ".question is duplicated",
        ),
        (
            lambda value: value["verification_questions"][0].update(
                evidence_anchor="通用沟通能力",
                question="请介绍你的通用沟通能力。",
            ),
            ".evidence_anchor is not linked",
        ),
        (
            lambda value: value["verification_questions"][0].update(
                question="请说明你的年龄以及 Python 项目经历。",
            ),
            ".question references a protected attribute",
        ),
        (
            lambda value: value["verification_questions"][1].update(
                question="你没有 Python 能力\uff0c请解释原因。",
            ),
            ".question treats unknown information as a negative fact",
        ),
    ],
)
def test_question_bank_rejects_malformed_unlinked_or_unsafe_content(mutation, expected_error: str) -> None:
    validator = _validator()
    assessment = _assessment()
    mutation(assessment)

    result = validator.run(_inputs([assessment]))

    assert any(expected_error in error for error in result["errors"])
    assert result["validated_candidate_assessments"]["assessments"] == []


def test_final_validation_still_blocks_untraceable_questions_after_repairs() -> None:
    validator = _validator()
    assessment = _assessment()
    assessment["verification_questions"][0]["question"] = "请介绍一下自己。"

    result = validator.run({**_inputs([assessment]), "candidate_assessments_repaired": [assessment]})

    assert result["assessment_validation_manifest"]["status"] == "blocked"
    assert any(".verification_questions" in error for error in result["errors"])
    assert result["validated_candidate_assessments"]["assessments"] == []


@pytest.mark.parametrize(
    ("education", "education_background"),
    [
        ("本科", "本科\uff1a测试大学"),
        ("硕士", "本科\uff1a示例大学 A\uff1b硕士\uff1a示例大学 B"),
        ("博士", "本科\uff1a示例大学 A\uff1b硕士\uff1a示例大学 B\uff1b博士\uff1a示例大学 C"),
        ("unknown", "unknown"),
    ],
)
def test_normalized_education_contract_accepts_pure_values(education: str, education_background: str) -> None:
    """Every known school must carry its education-stage label and remain writable."""
    validator = _validator()
    assessment = _assessment()
    assessment["education"] = education
    assessment["education_background"] = education_background

    result = validator.run(_inputs([assessment]))

    assert result["errors"] == []


@pytest.mark.parametrize(
    ("summary", "expected_error"),
    [
        ([], ".resume_summary must contain 1 to 5 bullet items"),
        (["独立完成 Python 项目"], ".resume_summary items must start with '- '"),
        (["- 项目一", ""], ".resume_summary items must be non-empty strings"),
        (
            ["- 项目一", "- 项目二", "- 项目三", "- 项目四", "- 项目五", "- 项目六"],
            ".resume_summary must contain 1 to 5 bullet items",
        ),
    ],
)
def test_resume_summary_rejects_non_bullet_or_more_than_five_points(summary: list[str], expected_error: str) -> None:
    """Malformed summaries must fail before any table write."""
    validator = _validator()
    assessment = _assessment()
    assessment["resume_summary"] = summary

    result = validator.run(_inputs([assessment]))

    assert any(expected_error in error for error in result["errors"])
    assert result["validated_candidate_assessments"]["assessments"] == []


@pytest.mark.parametrize("field", ["match_points", "mismatch_points"])
def test_role_point_lists_must_both_contain_a_table_point(field: str) -> None:
    """Either empty point list must be sent through the bounded repair rounds."""
    validator = _validator()
    assessment = _assessment()
    assessment[field] = []

    result = validator.run(_inputs([assessment]))

    assert any(f".{field} must contain at least one point" in error for error in result["errors"])
    assert result["validated_candidate_assessments"]["assessments"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(
            {
                "grade": "C",
                "total_score": 75,
                "interview_recommendation": "不建议面试",
                "interview_recommendation_reason": "评级 C 未达到自动面试门槛\uff0c保留为人工初审备选。",
            }
        ),
        lambda value: (
            value.update(
                {
                    "match_points": [
                        {"requirement": "构建 AI 应用", "resume_evidence": ["项目经历\uff1a构建 AI 应用"]}
                    ],
                    "interview_recommendation": "不建议面试",
                    "interview_recommendation_reason": "评级 B\uff0c但 Python 硬性要求缺少肯定证据。",
                }
            ),
            value["verification_questions"][0].update(
                {
                    "question": "请说明构建 AI 应用项目中你个人负责的关键工作、验证方式和结果。",
                    "evidence_anchor": "项目经历\uff1a构建 AI 应用",
                }
            ),
        ),
        lambda value: (
            value.update(
                {
                    "mismatch_points": [
                        {"requirement": "Python", "resume_evidence": ["项目明确仅使用 Java\uff0c无法使用 Python"]}
                    ],
                    "interview_recommendation": "不建议面试",
                    "interview_recommendation_reason": "评级 B\uff0c但 Python 硬性要求存在明确反证。",
                }
            ),
            value["verification_questions"][2].update(
                {
                    "question": "项目明确仅使用 Java\uff0c无法使用 Python\uff1b请说明当前 Python 实践情况。",
                    "evidence_anchor": "项目明确仅使用 Java\uff0c无法使用 Python",
                }
            ),
        ),
    ],
)
def test_deterministic_recommendation_gate_accepts_the_required_negative_decision(mutation) -> None:
    """C grades and failed hard-requirement gates must remain valid when recommendation is negative."""
    validator = _validator()
    assessment = _assessment()
    mutation(assessment)

    result = validator.run(_inputs([assessment]))

    assert result["errors"] == []


def test_validator_program_emits_only_declared_workflow_artifacts() -> None:
    """The Program boundary must not expose validator-only diagnostics as extra artifacts."""
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH)],
        input=json.dumps({"inputs": _inputs([_assessment()])}, ensure_ascii=False),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert set(json.loads(completed.stdout)) == {
        "assessment_validation_manifest",
        "validated_candidate_assessments",
    }


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (lambda value: value.update({"resume_scores": {}}), ".unexpected_fields:resume_scores"),
        (lambda value: value.update({"total_score": 101}), ".total_score must be an integer from 0 to 100"),
        (lambda value: value.update({"grade": "C"}), ".grade must equal B for total_score 85"),
        (
            lambda value: value.update({"matched_role_name": "虚构岗位"}),
            ".matched_role_name must equal the catalog role name",
        ),
        (lambda value: value.update({"role_direction": "研发-工程"}), ".unexpected_fields:role_direction"),
        (
            lambda value: value.update({"grade": "C", "total_score": 75}),
            ".interview_recommendation must equal 不建议面试 under the deterministic grade and hard-requirement gate",
        ),
        (
            lambda value: value.update(
                {"match_points": [{"requirement": "构建 AI 应用", "resume_evidence": ["项目经历\uff1a构建 AI 应用"]}]}
            ),
            ".interview_recommendation must equal 不建议面试 under the deterministic grade and hard-requirement gate",
        ),
        (
            lambda value: value.update(
                {
                    "mismatch_points": [
                        {"requirement": "Python", "resume_evidence": ["项目明确仅使用 Java\uff0c无法使用 Python"]}
                    ]
                }
            ),
            ".interview_recommendation must equal 不建议面试 under the deterministic grade and hard-requirement gate",
        ),
        (
            lambda value: value.update({"education": "硕士在读\uff08软件工程\uff09"}),
            ".education must be one of:",
        ),
        (
            lambda value: value.update(
                {"education_background": "硕士\uff1a示例大学 B 人工智能硕士在读\uff0c研究方向为视觉模型"}
            ),
            ".education_background must contain institution names only",
        ),
        (
            lambda value: value.update({"education_background": "示例大学 A/示例大学 B"}),
            ".education_background must use semicolon-separated stage labels for multiple institutions",
        ),
        (
            lambda value: value.update({"education_background": "示例大学 A\uff1b示例大学 B"}),
            ".education_background must label each institution with its education stage",
        ),
        (
            lambda value: value.update({"education_background": "示例大学 A"}),
            ".education_background must label each institution with its education stage",
        ),
        (lambda value: value["match_points"][0].update({"resume_evidence": []}), ".resume_evidence must be non-empty"),
        (
            lambda value: value.update(
                {"mismatch_points": [{"requirement": "Python", "resume_evidence": ["简历未提及 Python"]}]}
            ),
            ".mismatch_points[0].resume_evidence treats unknown as mismatch",
        ),
        (
            lambda value: value.update({"interview_recommendation_reason": ""}),
            ".interview_recommendation_reason must be non-empty text",
        ),
        (
            lambda value: value["document_revisions"].update({"resume_scoring_sha256": "b" * 64}),
            ".document_revisions.resume_scoring_sha256 does not match the fixed document",
        ),
    ],
)
def test_invalid_candidate_local_contracts_are_rejected(mutation, expected_error: str) -> None:
    """Every table-facing field must be deterministic before any Feishu write."""
    validator = _validator()
    assessment = _assessment()
    mutation(assessment)

    result = validator.run(_inputs([assessment]))

    assert any(expected_error in error for error in result["errors"])
    assert result["validated_candidate_assessments"]["assessments"] == []
    assert result["assessment_validation_manifest"]["status"] == "blocked"


def test_contradictory_scoring_document_blocks_the_whole_batch() -> None:
    """A 105-point source must not be repaired candidate-by-candidate or silently normalized."""
    validator = _validator()
    contradictory = SCORING_CONTENT.replace("5. 实习稳定性\uff0810分\uff09", "5. 实习稳定性\uff0815分\uff09")
    assessment = _assessment()
    assessment["document_revisions"]["resume_scoring_sha256"] = _sha(contradictory)

    result = validator.run(_inputs([assessment], contradictory))

    assert "scoring_contract.dimension_maxima_must_sum_to_declared_total" in result["errors"]
    assert result["assessment_validation_manifest"]["status"] == "blocked"


def test_explicit_pdf_failure_is_isolated_with_fixed_revisions() -> None:
    """A confirmed unreadable PDF may be skipped, but it must retain source and document identity."""
    validator = _validator()
    assessed = _assessment()
    failed_sha = "b" * 64
    failed = {
        "schema_version": "3.0",
        "status": "extraction_failed",
        "batch_id": BATCH_ID,
        "candidate_id": failed_sha[:16],
        "candidate_name": "unknown",
        "source": {
            "name": "scan.pdf",
            "sha256": failed_sha,
            "format": ".pdf",
            "extraction_mode": "read_pdf_tool",
            "extraction_quality": "unusable",
            "extraction_warnings": ["OCR failed on scanned page"],
        },
        "document_revisions": copy.deepcopy(assessed["document_revisions"]),
        "failure": {
            "stage": "pdf_ocr",
            "code": "pdf_extraction_failed",
            "message": "The scanned page could not be read.",
        },
    }

    result = validator.run(_inputs([assessed, failed]))

    assert result["errors"] == []
    assert len(result["validated_candidate_assessments"]["assessments"]) == 1
    assert result["validated_candidate_assessments"]["failed_candidates"] == [failed]


def test_repair_request_contains_only_fixed_authorities_and_candidate_errors() -> None:
    """A repair agent must receive exact rules and roles without authority to change their revisions."""
    pipeline = _pipeline()
    assessment = _assessment()
    assessment["grade"] = "C"

    requests, manifest = pipeline.build_repair_requests(_inputs([assessment]), assessments_key="candidate_assessments")

    assert manifest["status"] == "repair_required"
    assert manifest["repair_request_count"] == 1
    request = requests[0]
    assert request["immutable_identity"] == {
        "batch_id": BATCH_ID,
        "candidate_id": "a" * 16,
        "source_sha256": "a" * 64,
        "document_revisions": _assessment()["document_revisions"],
        "matched_role_key": "role-0123456789abcdef01234567",
    }
    assert request["expected_contract"]["schema_version"] == "3.0"
    assert request["expected_contract"]["scoring_rules"]["grade_ranges"]["B"] == [80, 89]
    assert request["expected_contract"]["active_roles"][0] == {
        "role_key": assessment["matched_role_key"],
        "name": assessment["matched_role_name"],
        "responsibilities": ["构建 AI 应用"],
        "hard_requirements": ["Python"],
        "preferences": ["自驱"],
    }
    assert request["expected_contract"]["recommendation_rules"] == {
        "eligible_grades": ["A", "B"],
        "positive_hard_requirement_evidence": "required for every selected-role hard requirement",
        "hard_requirement_mismatch": "forces 不建议面试",
        "other_grades": "C、D、E、F default to 不建议面试; Human may override through 初审状态",
    }
    assert request["expected_contract"]["education_rules"] == {
        "education_levels": ["博士", "硕士", "本科", "专科", "高中及以下", "unknown"],
        "single_institution_format": "本科\uff1a院校名称",
        "multiple_institutions_format": "本科\uff1a院校名称\uff1b硕士\uff1a院校名称",
        "institution_stages": ["专科", "本科", "硕士", "博士"],
        "institution_content": "仅限院校名称\uff1b不含专业、届别、学历、排名标签或个人背景摘要",
    }
    assert request["expected_contract"]["resume_summary_rules"] == {
        "type": "array",
        "min_items": 1,
        "max_items": 5,
        "item_format": "non-empty string starting with '- '",
        "content": "resume-supported strengths only",
        "example": ["- 独立交付生产级系统", "- 核心指标有量化提升"],
    }
    assert request["expected_contract"]["point_rules"] == {
        "type": "array of {requirement, resume_evidence} objects",
        "min_items_each": 1,
        "match_content": "resume-supported role matches",
        "mismatch_content": "resume-supported shortfalls, contradictions, or material evidence gaps",
        "table_format": "one '- ' bullet line per point",
    }
    assert request["expected_contract"]["verification_question_rules"] == {
        "type": (
            "array of {question, category, evidence_anchor, purpose, positive_signal, risk_signal} objects"
        ),
        "min_items": 3,
        "max_items": 6,
        "categories": ["真实性核验", "岗位匹配", "风险澄清"],
        "required_coverage": ["真实性核验", "岗位匹配", "风险澄清 when mismatch_points is non-empty"],
        "evidence_anchor": (
            "exact selected-role requirement or exact resume evidence from match_points/mismatch_points"
        ),
        "public_rendering": "<index>. [<category>] <question> in source order",
        "safety": "exclude protected attributes and unsupported negative claims",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda repaired: repaired["source"].update({"sha256": "b" * 64}), "source.sha256"),
        (lambda repaired: repaired.update({"batch_id": "other"}), "batch_id"),
        (
            lambda repaired: repaired["document_revisions"].update({"resume_scoring_sha256": "b" * 64}),
            "document_revisions",
        ),
        (lambda repaired: repaired.update({"matched_role_key": "role-other"}), "matched_role_key"),
    ],
)
def test_repair_merge_rejects_immutable_identity_changes(mutation, message: str) -> None:
    """A repair may fix local judgments but can never move the candidate to another source contract."""
    pipeline = _pipeline()
    original = _assessment()
    original["grade"] = "C"
    requests, _ = pipeline.build_repair_requests(_inputs([original]), assessments_key="candidate_assessments")
    repaired = _assessment()
    mutation(repaired)

    with pytest.raises(ValueError, match=message):
        pipeline.merge_repairs(
            {
                "candidate_assessments": [original],
                "assessment_repair_requests_round_1": requests,
                "repaired_candidate_assessments_round_1": [repaired],
                "batch_id": BATCH_ID,
            },
            assessments_key="candidate_assessments",
            requests_key="assessment_repair_requests_round_1",
            repairs_key="repaired_candidate_assessments_round_1",
        )


def test_question_only_repair_preserves_unrelated_fields_and_candidate_indices() -> None:
    pipeline = _pipeline()
    untouched = _assessment()
    untouched["source"]["sha256"] = "b" * 64
    untouched["candidate_id"] = "b" * 16
    broken = _assessment()
    broken["verification_questions"][0]["question"] = "请介绍一下自己。"
    requests, _ = pipeline.build_repair_requests(
        _inputs([untouched, broken]),
        assessments_key="candidate_assessments",
    )
    repaired = _assessment()

    merged, manifest = pipeline.merge_repairs(
        {
            "candidate_assessments": [untouched, broken],
            "assessment_repair_requests_round_1": requests,
            "repaired_candidate_assessments_round_1": [repaired],
        },
        assessments_key="candidate_assessments",
        requests_key="assessment_repair_requests_round_1",
        repairs_key="repaired_candidate_assessments_round_1",
    )

    assert merged[0] == untouched
    assert merged[1] == repaired
    assert manifest["modified_indices"] == [1]

    forged = copy.deepcopy(repaired)
    forged["education"] = "硕士"
    with pytest.raises(ValueError, match="question-only repair"):
        pipeline.merge_repairs(
            {
                "candidate_assessments": [untouched, broken],
                "assessment_repair_requests_round_1": requests,
                "repaired_candidate_assessments_round_1": [forged],
            },
            assessments_key="candidate_assessments",
            requests_key="assessment_repair_requests_round_1",
            repairs_key="repaired_candidate_assessments_round_1",
        )


def test_final_validation_keeps_writeable_assessment_and_reports_constraint_warnings() -> None:
    """After two repair rounds, content violations warn but do not block a table-writeable object."""
    validator = _validator()
    pipeline = _pipeline()
    original = _assessment()
    original["grade"] = "C"
    requests, _ = pipeline.build_repair_requests(_inputs([original]), assessments_key="candidate_assessments")
    still_invalid = _assessment()
    still_invalid["interview_recommendation_reason"] = ""

    merged, manifest = pipeline.merge_repairs(
        {
            "candidate_assessments": [original],
            "assessment_repair_requests_round_1": requests,
            "repaired_candidate_assessments_round_1": [still_invalid],
            "batch_id": BATCH_ID,
        },
        assessments_key="candidate_assessments",
        requests_key="assessment_repair_requests_round_1",
        repairs_key="repaired_candidate_assessments_round_1",
    )
    final = validator.run({**_inputs(merged), "candidate_assessments_repaired": merged})

    assert manifest["status"] == "complete"
    assert final["errors"] == []
    assert final["assessment_validation_manifest"]["status"] == "complete"
    assert final["assessment_validation_manifest"]["constraint_warning_count"] == 1
    assert any(
        ".interview_recommendation_reason must be non-empty text" in warning
        for warning in final["assessment_validation_manifest"]["constraint_warnings"]
    )
    assert final["validated_candidate_assessments"]["assessments"] == [
        {**still_invalid, "assessment_revision": final["assessment_validation_manifest"]["assessment_revision"]}
    ]
    _readiness_gate().run(
        {
            "validated_candidate_assessments": final["validated_candidate_assessments"],
            "assessment_validation_manifest": final["assessment_validation_manifest"],
        }
    )


def test_final_validation_does_not_block_unrepaired_point_or_school_content() -> None:
    """The new content rules remain warnings after two rounds when JSON is still table-writeable."""
    validator = _validator()
    assessment = _assessment()
    assessment["education_background"] = "测试大学"
    assessment["match_points"][0]["requirement"] = "非目录要求"

    final = validator.run({**_inputs([assessment]), "candidate_assessments_repaired": [assessment]})

    assert final["errors"] == []
    assert final["assessment_validation_manifest"]["status"] == "complete"
    warnings = final["assessment_validation_manifest"]["constraint_warnings"]
    assert any(".education_background must label each institution" in item for item in warnings)
    assert any(".match_points[0].requirement must exactly match" in item for item in warnings)
    assert len(final["validated_candidate_assessments"]["assessments"]) == 1


def test_final_validation_still_blocks_json_that_cannot_map_to_table_types() -> None:
    """Content is best-effort, but a non-numeric score cannot be written to the numeric table field."""
    validator = _validator()
    assessment = _assessment()
    assessment["total_score"] = {"unexpected": 85}

    final = validator.run({**_inputs([assessment]), "candidate_assessments_repaired": [assessment]})

    assert final["assessment_validation_manifest"]["status"] == "blocked"
    assert any(".total_score must be a table-writeable number" in error for error in final["errors"])
    assert final["validated_candidate_assessments"]["assessments"] == []


def test_final_validation_blocks_values_rejected_by_feishu_single_select_fields() -> None:
    """A string is not writeable when it is absent from the destination single-select options."""
    validator = _validator()
    assessment = _assessment()
    assessment["grade"] = "G"
    assessment["interview_recommendation"] = "待确认"

    final = validator.run({**_inputs([assessment]), "candidate_assessments_repaired": [assessment]})

    assert final["assessment_validation_manifest"]["status"] == "blocked"
    assert any(".grade is not a writeable Feishu option" in error for error in final["errors"])
    assert any(".interview_recommendation is not a writeable Feishu option" in error for error in final["errors"])


def test_workflow_repair_and_validation_steps_use_dynamic_authorities() -> None:
    """Every validation round must use the same fixed documents and runtime role catalog."""
    workflow = (WORKFLOW_ROOT / "resume-approval.workflow").read_text(encoding="utf-8")
    step_names = [
        "build_assessment_repairs_round_1_step",
        "repair_assessments_round_1_step",
        "build_assessment_repairs_round_2_step",
        "repair_assessments_round_2_step",
        "validate_assessments_step",
    ]
    for index, step_name in enumerate(step_names):
        start = workflow.index(f"step_name({step_name})")
        later = [workflow.find(f"step_name({name})", start + 1) for name in step_names[index + 1 :]]
        later = [position for position in later if position >= 0]
        end = min(later) if later else workflow.index("step_name(cleanup_temporary_files_step)", start)
        block = workflow[start:end]
        assert "reference_documents" in block
        assert "role_catalog" in block
        assert "extracted_standards" not in block
        assert "target_role" not in block


def test_repair_prompt_locks_all_cross_batch_identity() -> None:
    """The Agent instruction must mirror the merge guard instead of inviting a full reassessment."""
    prompt = (WORKFLOW_ROOT / "instructions" / "repair-candidate-assessment.md").read_text(encoding="utf-8")

    for token in ("source", "batch_id", "candidate_id", "document_revisions", "matched_role_key"):
        assert token in prompt
    assert "forbidden legacy fields" in prompt
    assert "expected_contract.education_rules" in prompt
    assert "expected_contract.recommendation_rules" in prompt
    assert "expected_contract.verification_question_rules" in prompt
    assert "question-only" in prompt
    assert "never claim the candidate lacks the ability" in prompt
    assert "具体岗位证据缺口" in prompt
    assert "不得把评级或分数本身作为主要理由" in prompt
