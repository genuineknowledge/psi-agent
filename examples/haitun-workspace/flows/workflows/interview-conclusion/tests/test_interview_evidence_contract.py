from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = WORKFLOW_ROOT.parent / "resume-approval" / "programs" / "validate_interview_evidence.py"
INTERVIEW_RECORD_ID = "recInterview001"
TALENT_RECORD_ID = "recTalent00001"
TABLE_ID = "tblInterview"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_interview_evidence_b", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assessment() -> dict:
    return {
        "schema_version": "3.0",
        "status": "assessed",
        "batch_id": "batch-1",
        "candidate_id": "a" * 16,
        "candidate_name": "测试候选人",
        "matched_role_key": "role-0123456789abcdef01234567",
        "matched_role_name": "AI应用开发工程师",
        "grade": "B",
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


def _item() -> dict:
    assessment = _assessment()
    return {
        "schema_version": "2.0",
        "status": "complete",
        "interview_record_id": INTERVIEW_RECORD_ID,
        "talent_record_id": TALENT_RECORD_ID,
        "batch_id": "batch-1",
        "candidate_id": "a" * 16,
        "candidate_name": "测试候选人",
        "assessment_revision": "d" * 64,
        "matched_role": _role(),
        "assessment": assessment,
        "notes": "候选人给出了可靠的 Python 生产案例。",
        "supplement": "补充说明了上线结果。",
        "evidence": "Human 说明了具体案例和结果。",
        "interview_scoring": {
            "reliability": {"score": 4, "evidence": ["按承诺完成上线"]},
            "professional": {"score": 4, "evidence": ["解释了 Python 架构权衡"]},
            "learning_action": {"score": 4, "evidence": ["说明了学习和复盘过程"]},
            "ai_native": {"score": 4, "evidence": ["说明了 AI 工具验证流程"]},
            "weighted_total": 4.0,
            "grade": "A",
            "smart_level": "T3",
            "smart_level_evidence": ["能把陌生问题拆解并验证"],
            "open_questions": [],
        },
        "risk_verification": "已验证",
        "interview_conclusion": "建议进入终审",
        "errors": [],
    }


def _write_handoff(workspace: Path, item: dict) -> None:
    path = workspace / ".psi" / "resume-approval" / "interview-handoffs"
    path.mkdir(parents=True)
    handoff = {
        "schema_version": "2.0",
        "interview_record_id": item["interview_record_id"],
        "interview_table_id": TABLE_ID,
        "batch_id": item["batch_id"],
        "candidate_id": item["candidate_id"],
        "candidate_name": item["candidate_name"],
        "talent_record_id": item["talent_record_id"],
        "assessment_revision": item["assessment_revision"],
        "document_revisions": {
            "resume_scoring_sha256": "b" * 64,
            "role_information_sha256": "c" * 64,
        },
        "matched_role": item["matched_role"],
        "assessment": item["assessment"],
    }
    (path / f"{INTERVIEW_RECORD_ID}.json").write_text(json.dumps(handoff, ensure_ascii=False), encoding="utf-8")


def _inputs(item: dict) -> dict:
    return {
        "interview_evidence_items": [item],
        "interview_record_ids": [INTERVIEW_RECORD_ID],
        "feishu_config": {"interview_table_id": TABLE_ID},
    }


def test_real_a2_handoff_shape_validates_and_gets_a_short_interview_revision(
    tmp_path: Path,
) -> None:
    module = _load_module()
    item = _item()
    _write_handoff(tmp_path, item)

    result = module.run(_inputs(item), str(tmp_path))

    assert result["interview_validation_manifest"]["status"] == "complete"
    assert len(result["validated_interview_items"][0]["interview_revision"]) == 16
    assert result["validated_interview_items"][0]["assessment_revision"] == "d" * 64
    assert result["validated_interview_items"][0]["matched_role"] == _role()


def test_tampered_private_role_or_assessment_revision_is_blocked(tmp_path: Path) -> None:
    module = _load_module()
    item = _item()
    _write_handoff(tmp_path, item)
    tampered = copy.deepcopy(item)
    tampered["matched_role"]["hard_requirements"] = ["虚构要求"]
    tampered["assessment_revision"] = "e" * 64

    result = module.run(_inputs(tampered), str(tmp_path))

    assert result["interview_validation_manifest"]["status"] == "blocked"
    assert result["validated_interview_items"] == []
    assert any("private handoff" in error for error in result["interview_validation_manifest"]["errors"])


def test_duplicate_requested_record_is_blocked(tmp_path: Path) -> None:
    module = _load_module()
    item = _item()
    _write_handoff(tmp_path, item)
    inputs = _inputs(item)
    inputs["interview_record_ids"].append(INTERVIEW_RECORD_ID)

    result = module.run(inputs, str(tmp_path))

    assert result["interview_validation_manifest"]["status"] == "blocked"
    assert any("duplicated" in error for error in result["interview_validation_manifest"]["errors"])
