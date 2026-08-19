from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = WORKFLOW_ROOT / "programs" / "persist_interview_stage_handoff.py"
BATCH_ID = "resume-20260810-120000-123456"
ROLE_KEY = "role-0123456789abcdef01234567"
ROLE_REVISION = "c" * 64
TALENT_RECORD_ID = "recTalent00001"
REJECTED_RECORD_ID = "recTalent00002"


def _questions() -> list[dict]:
    return [
        {
            "question": "请说明 Python 项目中你个人负责的关键工作。",
            "category": "真实性核验",
            "evidence_anchor": "使用 Python",
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
            "question": "请澄清生产经验的证据范围。",
            "category": "风险澄清",
            "evidence_anchor": "生产经验",
            "purpose": "澄清生产经验的证据缺口。",
            "positive_signal": "能够提供可验证案例。",
            "risk_signal": "案例缺少可验证结果。",
        },
    ]


def _load_module():
    spec = importlib.util.spec_from_file_location("persist_interview_stage_handoff", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assessment(candidate_id: str, candidate_name: str, revision: str) -> dict:
    return {
        "schema_version": "3.0",
        "status": "assessed",
        "batch_id": BATCH_ID,
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "grade": "B",
        "education": "硕士",
        "education_background": "硕士:测试大学",
        "total_score": 82,
        "matched_role_key": ROLE_KEY,
        "matched_role_name": "AI应用开发工程师",
        "match_points": [{"requirement": "Python", "resume_evidence": ["使用 Python"]}],
        "mismatch_points": [{"requirement": "生产经验", "risk": "证据不足"}],
        "resume_summary": ["- 完成 AI 应用项目"],
        "interview_recommendation": "建议面试",
        "interview_recommendation_reason": "岗位证据明确。",
        "verification_questions": _questions(),
        "document_revisions": {
            "resume_scoring_sha256": "b" * 64,
            "role_information_sha256": ROLE_REVISION,
        },
        "assessment_revision": revision,
    }


def _role() -> dict:
    return {
        "role_key": ROLE_KEY,
        "name": "AI应用开发工程师",
        "employment_type": "正式",
        "location": "示例城市",
        "headcount": 2,
        "status": "active",
        "responsibilities": ["构建 AI 应用"],
        "hard_requirements": ["Python"],
        "preferences": ["自驱"],
        "source_evidence": [{"section": "岗位", "text": "AI应用开发工程师"}],
    }


def _inputs() -> dict:
    approved = _assessment("a" * 16, "测试候选人", "d" * 64)
    rejected = _assessment("e" * 16, "未通过候选人", "f" * 64)
    return {
        "batch_id": BATCH_ID,
        "initial_decision_bundle": {
            "schema_version": "3.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "approved": [
                {
                    "assessment": approved,
                    "record_id": TALENT_RECORD_ID,
                    "initial_status": "通过",
                }
            ],
            "rejected": [
                {
                    "assessment": rejected,
                    "record_id": REJECTED_RECORD_ID,
                    "initial_status": "不通过",
                }
            ],
            "pending": [],
            "errors": [],
        },
        "validated_candidate_assessments": {
            "schema_version": "3.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "assessments": [copy.deepcopy(approved), copy.deepcopy(rejected)],
            "errors": [],
        },
        "talent_pool_manifest": {
            "schema_version": "4.0",
            "status": "complete",
            "batch_id": BATCH_ID,
            "expected_count": 2,
            "records": [
                {
                    "record_id": TALENT_RECORD_ID,
                    "candidate_id": approved["candidate_id"],
                    "assessment_revision": approved["assessment_revision"],
                },
                {
                    "record_id": REJECTED_RECORD_ID,
                    "candidate_id": rejected["candidate_id"],
                    "assessment_revision": rejected["assessment_revision"],
                },
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
            "identity": "bot",
        },
    }


def _remove_one_of_four_questions(data: dict) -> None:
    fourth_question = {
        "question": "请说明一次 Python 方案取舍及最终结果。",
        "category": "岗位匹配",
        "evidence_anchor": "Python",
        "purpose": "补充核验工程决策能力。",
        "positive_signal": "能够说明约束、取舍和结果。",
        "risk_signal": "只能描述方案且无法解释取舍。",
    }
    source_questions = data["validated_candidate_assessments"]["assessments"][0]["verification_questions"]
    decision_questions = data["initial_decision_bundle"]["approved"][0]["assessment"]["verification_questions"]
    source_questions.append(copy.deepcopy(fourth_question))
    decision_questions.append(copy.deepcopy(fourth_question))
    decision_questions.pop(1)


def _canonical_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def test_persists_exact_provenance_and_public_descriptor(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()

    result = module.run(inputs, str(tmp_path))

    descriptor = result["interview_stage_handoff"]
    assert result["interview_stage_handoff_manifest"] == {
        "schema_version": "1.0",
        "status": "complete",
        "approved_count": 1,
        "errors": [],
    }
    assert set(descriptor) == {
        "schema_version",
        "status",
        "batch_id",
        "approved_count",
        "path",
        "sha256",
        "next_workflow",
        "next_input",
    }
    assert descriptor["status"] == "complete"
    assert descriptor["approved_count"] == 1
    assert descriptor["next_workflow"] == "resume-interview-preparation"
    assert descriptor["next_input"] == "interview_stage_handoff"
    destination = tmp_path / descriptor["path"]
    payload_bytes = destination.read_bytes()
    assert payload_bytes.endswith(b"\n") and not payload_bytes.endswith(b"\n\n")
    assert hashlib.sha256(payload_bytes).hexdigest() == descriptor["sha256"]

    payload = json.loads(payload_bytes)
    assert set(payload) == {
        "schema_version",
        "status",
        "batch_id",
        "decision_contract",
        "destination",
        "role_catalog",
        "approved",
    }
    assert payload["approved"][0]["talent_record_id"] == TALENT_RECORD_ID
    assert payload["approved"][0]["assessment"] == inputs["initial_decision_bundle"]["approved"][0]["assessment"]
    assert payload["role_catalog"]["roles"] == [_role()]
    assert payload["decision_contract"] == {
        "schema_version": "3.0",
        "sha256": hashlib.sha256(_canonical_text(inputs["initial_decision_bundle"]).encode("utf-8")).hexdigest(),
        "expected_review_count": 2,
        "approved_count": 1,
        "rejected_count": 1,
        "pending_count": 0,
    }
    assert set(payload["destination"]) == {
        "app_token",
        "base_url",
        "talent_pool_table_id",
        "interview_table_id",
    }
    assert "user_key" not in payload["destination"]


def test_identical_rerun_reuses_the_same_bytes(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()

    first = module.run(inputs, str(tmp_path))["interview_stage_handoff"]
    path = tmp_path / first["path"]
    original = path.read_bytes()
    second = module.run(inputs, str(tmp_path))["interview_stage_handoff"]

    assert second == first
    assert path.read_bytes() == original


def test_conflicting_existing_handoff_is_not_overwritten(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / ".psi" / "resume-approval" / "interview-stage-handoffs" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="conflicting handoff"):
        module.run(_inputs(), str(tmp_path))

    assert path.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["initial_decision_bundle"].update(status="blocked"),
            "must be complete",
        ),
        (
            lambda data: data["initial_decision_bundle"]["approved"][0].update(initial_status="不通过"),
            "approved.*通过",
        ),
        (
            lambda data: (
                data["validated_candidate_assessments"]["assessments"][0].update(
                    matched_role_key="role-missing"
                ),
                data["initial_decision_bundle"]["approved"][0]["assessment"].update(
                    matched_role_key="role-missing"
                ),
            ),
            "active role",
        ),
        (
            lambda data: (
                data["validated_candidate_assessments"]["assessments"][0]["document_revisions"].update(
                    role_information_sha256="9" * 64
                ),
                data["initial_decision_bundle"]["approved"][0]["assessment"]["document_revisions"].update(
                    role_information_sha256="9" * 64
                ),
            ),
            "role document revision",
        ),
        (
            lambda data: data["initial_decision_bundle"]["pending"].append(
                data["initial_decision_bundle"]["rejected"].pop() | {"initial_status": "待审批"}
            ),
            "pending",
        ),
        (
            lambda data: data["initial_decision_bundle"]["rejected"].clear(),
            "exactly cover",
        ),
        (
            lambda data: data["initial_decision_bundle"]["rejected"].append(
                copy.deepcopy(data["initial_decision_bundle"]["approved"][0]) | {"initial_status": "不通过"}
            ),
            "duplicate",
        ),
        (
            lambda data: data["talent_pool_manifest"].update(expected_count=3),
            "expected_count",
        ),
        (
            lambda data: (
                data["talent_pool_manifest"]["records"][1].update(candidate_id="a" * 16),
                data["initial_decision_bundle"]["rejected"][0]["assessment"].update(candidate_id="a" * 16),
            ),
            "duplicate candidate",
        ),
        (
            lambda data: data["initial_decision_bundle"]["approved"][0]["assessment"][
                "verification_questions"
            ].pop(),
            "3 to 6",
        ),
        (
            lambda data: data["initial_decision_bundle"]["approved"][0]["assessment"][
                "verification_questions"
            ][0].update(question="请介绍一个与岗位无关的项目。"),
            "immutable validated assessment",
        ),
        (
            lambda data: data["initial_decision_bundle"]["approved"][0]["assessment"].update(total_score=99),
            "immutable validated assessment",
        ),
        (
            _remove_one_of_four_questions,
            "immutable validated assessment",
        ),
        (
            lambda data: data["initial_decision_bundle"]["approved"][0]["assessment"][
                "verification_questions"
            ].reverse(),
            "immutable validated assessment",
        ),
    ],
)
def test_invalid_or_unreviewed_source_is_rejected_before_write(tmp_path: Path, mutation, message: str) -> None:
    module = _load_module()
    inputs = _inputs()
    mutation(inputs)

    with pytest.raises(ValueError, match=message):
        module.run(inputs, str(tmp_path))

    assert not (tmp_path / ".psi" / "resume-approval" / "interview-stage-handoffs" / f"{BATCH_ID}.json").exists()


def test_zero_approved_candidates_is_a_complete_reviewed_handoff(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()
    item = inputs["initial_decision_bundle"]["approved"].pop()
    item["initial_status"] = "不通过"
    inputs["initial_decision_bundle"]["rejected"].append(item)

    descriptor = module.run(inputs, str(tmp_path))["interview_stage_handoff"]
    payload = json.loads((tmp_path / descriptor["path"]).read_text(encoding="utf-8"))

    assert descriptor["approved_count"] == 0
    assert payload["approved"] == []
    assert payload["role_catalog"]["roles"] == []
    assert payload["decision_contract"]["approved_count"] == 0
    assert payload["decision_contract"]["rejected_count"] == 2
    assert payload["decision_contract"]["pending_count"] == 0


def test_accepts_a2_initial_review_artifact_names(tmp_path: Path) -> None:
    module = _load_module()
    inputs = _inputs()
    inputs["initial_review_batch_id"] = inputs.pop("batch_id")
    inputs["initial_review_feishu_config"] = inputs.pop("feishu_config")

    descriptor = module.run(inputs, str(tmp_path))["interview_stage_handoff"]

    assert descriptor["batch_id"] == BATCH_ID
