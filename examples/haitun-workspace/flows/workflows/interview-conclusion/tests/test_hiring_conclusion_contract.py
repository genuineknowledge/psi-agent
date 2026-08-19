from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = WORKFLOW_ROOT.parent / "resume-approval" / "programs" / "validate_hiring_conclusions.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_hiring_conclusions_b", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scoring() -> dict:
    return {
        "reliability": {"score": 4, "evidence": ["按承诺上线"]},
        "professional": {"score": 4, "evidence": ["解释了架构权衡"]},
        "learning_action": {"score": 4, "evidence": ["说明了复盘过程"]},
        "ai_native": {"score": 4, "evidence": ["说明了验证流程"]},
        "weighted_total": 4.0,
        "grade": "A",
        "smart_level": "T3",
        "smart_level_evidence": ["能拆解并验证陌生问题"],
        "open_questions": [],
    }


def _evidence() -> dict:
    return {
        "schema_version": "2.0",
        "status": "complete",
        "candidate_id": "a" * 16,
        "candidate_name": "测试候选人",
        "interview_record_id": "recInterview001",
        "talent_record_id": "recTalent00001",
        "interview_revision": "f" * 16,
        "assessment": {
            "schema_version": "3.0",
            "status": "assessed",
            "candidate_id": "a" * 16,
            "grade": "B",
            "assessment_revision": "d" * 64,
        },
        "matched_role": {
            "role_key": "role-0123456789abcdef01234567",
            "name": "AI应用开发工程师",
            "status": "active",
            "hard_requirements": ["Python", "生产经验"],
        },
        "interview_scoring": _scoring(),
    }


def _conclusion(evidence: dict) -> dict:
    return {
        "schema_version": "2.0",
        "status": "concluded",
        "candidate_id": evidence["candidate_id"],
        "candidate_name": evidence["candidate_name"],
        "interview_record_id": evidence["interview_record_id"],
        "interview_revision": evidence["interview_revision"],
        "talent_record_id": evidence["talent_record_id"],
        "assessment": copy.deepcopy(evidence["assessment"]),
        "matched_role": copy.deepcopy(evidence["matched_role"]),
        "interview_scoring": copy.deepcopy(evidence["interview_scoring"]),
        "decision_matrix": {
            "resume_grade": "B",
            "interview_grade": "A",
            "performance_band": "符合预期",
            "matrix_result": "推荐录用",
        },
        "recommendation": "hire",
        "requirement_evidence_matrix": [
            {
                "requirement": "Python",
                "result": "pass",
                "evidence": ["简历与面试均有生产案例"],
                "source": "both",
            },
            {
                "requirement": "生产经验",
                "result": "pass",
                "evidence": ["说明了上线结果"],
                "source": "interview",
            },
        ],
        "hire_reasons": [{"reason": "满足硬要求", "evidence": ["证据完整"], "source": "both"}],
        "risk_closure": [],
        "remaining_unknowns": [],
        "interview_summary": "证据支持录用。",
        "confidence": 0.9,
    }


def test_every_private_matched_role_requirement_is_covered_once() -> None:
    module = _load_module()
    evidence = _evidence()

    result = module.run(
        {
            "hiring_conclusions": [_conclusion(evidence)],
            "validated_interview_items": [evidence],
        }
    )

    assert result["status"] == "complete"
    assert result["conclusions"][0]["assessment"]["schema_version"] == "3.0"
    assert result["conclusions"][0]["matched_role"] == evidence["matched_role"]


@pytest.mark.parametrize(
    "requirements",
    [
        ["Python"],
        ["Python", "Python", "生产经验"],
        ["Python", "生产经验", "虚构要求"],
    ],
)
def test_missing_duplicate_or_extra_requirement_is_blocked(requirements: list[str]) -> None:
    module = _load_module()
    evidence = _evidence()
    conclusion = _conclusion(evidence)
    template = conclusion["requirement_evidence_matrix"][0]
    conclusion["requirement_evidence_matrix"] = [
        {**template, "requirement": requirement} for requirement in requirements
    ]

    result = module.run(
        {
            "hiring_conclusions": [conclusion],
            "validated_interview_items": [evidence],
        }
    )

    assert result["status"] == "blocked"
    assert any("exactly once" in error for error in result["errors"])


def test_changed_matched_role_contract_is_blocked() -> None:
    module = _load_module()
    evidence = _evidence()
    conclusion = _conclusion(evidence)
    conclusion["matched_role"]["hard_requirements"] = ["Python"]

    result = module.run(
        {
            "hiring_conclusions": [conclusion],
            "validated_interview_items": [evidence],
        }
    )

    assert result["status"] == "blocked"
    assert any("preserve" in error for error in result["errors"])
