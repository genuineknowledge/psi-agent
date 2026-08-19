from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

WORKFLOWS_ROOT = Path(__file__).resolve().parents[2]
PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "load_interview_stage.py"
BATCH_ID = "resume-20260810-120000-123456"
CANDIDATE_ID = "a" * 16
REVISION = "d" * 64
ROLE_REVISION = "c" * 64
ROLE_KEY = "role-0123456789abcdef01234567"


def _questions() -> list[dict]:
    return [
        {
            "question": "请说明 Python 项目中你个人负责的关键工作。",
            "category": "真实性核验",
            "evidence_anchor": "完成 AI 应用项目",
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
            "question": "请澄清当前风险证据的范围。",
            "category": "风险澄清",
            "evidence_anchor": "风险证据",
            "purpose": "澄清风险证据。",
            "positive_signal": "能够提供可验证案例。",
            "risk_signal": "案例缺少可验证结果。",
        },
    ]


def _load_module():
    spec = importlib.util.spec_from_file_location("load_interview_stage", PROGRAM_PATH)
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
        "education_background": "硕士:测试大学",
        "total_score": 82,
        "matched_role_key": ROLE_KEY,
        "matched_role_name": "AI应用开发工程师",
        "match_points": [],
        "mismatch_points": [],
        "resume_summary": ["- 完成 AI 应用项目"],
        "interview_recommendation": "建议面试",
        "interview_recommendation_reason": "岗位证据明确。",
        "verification_questions": _questions(),
        "document_revisions": {
            "resume_scoring_sha256": "b" * 64,
            "role_information_sha256": ROLE_REVISION,
        },
        "assessment_revision": REVISION,
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


def _document() -> dict:
    return {
        "schema_version": "1.0",
        "status": "complete",
        "batch_id": BATCH_ID,
        "decision_contract": {
            "schema_version": "3.0",
            "sha256": "e" * 64,
            "expected_review_count": 2,
            "approved_count": 1,
            "rejected_count": 1,
            "pending_count": 0,
        },
        "destination": {
            "app_token": "app-token",
            "base_url": "https://example.feishu.cn/base/app-token",
            "talent_pool_table_id": "tblTalent",
            "interview_table_id": "tblInterview",
        },
        "role_catalog": {
            "schema_version": "1.0",
            "source_document_sha256": ROLE_REVISION,
            "roles": [_role()],
        },
        "approved": [
            {
                "talent_record_id": "recTalent00001",
                "initial_status": "通过",
                "assessment": _assessment(),
            }
        ],
    }


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _write_case(workspace: Path, document: dict | None = None) -> tuple[dict, Path]:
    payload = copy.deepcopy(document if document is not None else _document())
    relative = Path(".psi/resume-approval/interview-stage-handoffs") / f"{BATCH_ID}.json"
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _canonical_bytes(payload)
    path.write_bytes(content)
    descriptor = {
        "schema_version": "1.0",
        "status": "complete",
        "batch_id": BATCH_ID,
        "approved_count": len(payload["approved"]),
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "next_workflow": "resume-interview-preparation",
        "next_input": "interview_stage_handoff",
    }
    defaults = workspace / "flows/workflows/resume-approval/resume-approval.defaults.json"
    defaults.parent.mkdir(parents=True, exist_ok=True)
    defaults.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "feishu_config": payload["destination"] | {"user_key": "local-only", "identity": "bot"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return descriptor, path


def _rehash(descriptor: dict, path: Path) -> None:
    descriptor["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_loads_verified_handoff_and_builds_authoritative_tasks(tmp_path: Path) -> None:
    module = _load_module()
    descriptor, _ = _write_case(tmp_path)

    result = module.run({"interview_stage_handoff": descriptor}, str(tmp_path))

    assert set(result) == {
        "interview_stage_bundle",
        "approved_interview_tasks",
        "batch_id",
        "feishu_config",
        "stage_load_manifest",
    }
    assert result["batch_id"] == BATCH_ID
    assert result["stage_load_manifest"] == {
        "schema_version": "1.0",
        "status": "complete",
        "approved_count": 1,
        "errors": [],
    }
    task = result["approved_interview_tasks"][0]
    assert task["candidate_id"] == CANDIDATE_ID
    assert task["talent_record_id"] == "recTalent00001"
    assert task["assessment"] == _assessment()
    assert task["matched_role"] == _role()
    assert "user_key" not in task
    assert result["feishu_config"]["user_key"] == "local-only"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda descriptor, _path: descriptor.update(status="blocked"), "descriptor.*complete"),
        (lambda descriptor, _path: descriptor.update(approved_count=2), "approved_count"),
        (lambda descriptor, _path: descriptor.update(batch_id="resume-other"), "batch"),
        (lambda descriptor, _path: descriptor.update(sha256="0" * 64), "hash"),
        (lambda descriptor, _path: descriptor.update(path="../escape.json"), "path"),
    ],
)
def test_rejects_invalid_descriptor_before_loading(tmp_path: Path, mutation, message: str) -> None:
    module = _load_module()
    descriptor, path = _write_case(tmp_path)
    mutation(descriptor, path)

    with pytest.raises((TypeError, ValueError), match=message):
        module.run({"interview_stage_handoff": descriptor}, str(tmp_path))


def test_rejects_absolute_path(tmp_path: Path) -> None:
    module = _load_module()
    descriptor, path = _write_case(tmp_path)
    descriptor["path"] = str(path.resolve())
    with pytest.raises(ValueError, match="path"):
        module.run({"interview_stage_handoff": descriptor}, str(tmp_path))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda doc: doc["decision_contract"].update(pending_count=1), "pending_count"),
        (lambda doc: doc["decision_contract"].update(expected_review_count=3), "review count"),
        (lambda doc: doc["decision_contract"].update(approved_count=2), "approved_count"),
        (lambda doc: doc["approved"][0].update(initial_status="待审批"), "通过"),
        (lambda doc: doc["approved"][0]["assessment"].update(batch_id="resume-other"), "batch"),
        (lambda doc: doc["approved"][0]["assessment"].update(assessment_revision="invalid"), "revision"),
        (lambda doc: doc["approved"][0]["assessment"].update(matched_role_key="role-other"), "role"),
        (
            lambda doc: doc["approved"][0]["assessment"]["verification_questions"].pop(),
            "3 to 6",
        ),
        (
            lambda doc: doc["approved"][0]["assessment"]["document_revisions"].update(role_information_sha256="0" * 64),
            "role document",
        ),
    ],
)
def test_rejects_forged_or_unreviewed_private_document(tmp_path: Path, mutation, message: str) -> None:
    module = _load_module()
    document = _document()
    mutation(document)
    descriptor, _ = _write_case(tmp_path, document)

    with pytest.raises((TypeError, ValueError), match=message):
        module.run({"interview_stage_handoff": descriptor}, str(tmp_path))


def test_rejects_duplicate_approved_candidate(tmp_path: Path) -> None:
    module = _load_module()
    document = _document()
    document["approved"].append(copy.deepcopy(document["approved"][0]))
    document["decision_contract"].update(
        expected_review_count=3,
        approved_count=2,
        rejected_count=1,
    )
    descriptor, _ = _write_case(tmp_path, document)

    with pytest.raises(ValueError, match="duplicate"):
        module.run({"interview_stage_handoff": descriptor}, str(tmp_path))


def test_rejects_changed_local_destination_identity(tmp_path: Path) -> None:
    module = _load_module()
    descriptor, _ = _write_case(tmp_path)
    defaults = tmp_path / "flows/workflows/resume-approval/resume-approval.defaults.json"
    current = json.loads(defaults.read_text(encoding="utf-8"))
    current["feishu_config"]["interview_table_id"] = "tblChanged"
    defaults.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(ValueError, match="destination"):
        module.run({"interview_stage_handoff": descriptor}, str(tmp_path))


def test_zero_approved_is_complete_and_produces_no_tasks(tmp_path: Path) -> None:
    module = _load_module()
    document = _document()
    document["approved"] = []
    document["role_catalog"]["roles"] = []
    document["decision_contract"].update(approved_count=0, rejected_count=2)
    descriptor, _ = _write_case(tmp_path, document)

    result = module.run({"interview_stage_handoff": descriptor}, str(tmp_path))

    assert result["approved_interview_tasks"] == []
    assert result["stage_load_manifest"]["status"] == "complete"
    assert result["stage_load_manifest"]["approved_count"] == 0


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privileges are not guaranteed")
def test_rejects_symlink_escape(tmp_path: Path) -> None:
    module = _load_module()
    descriptor, path = _write_case(tmp_path)
    outside = tmp_path.parent / "outside-handoff.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    _rehash(descriptor, path)
    with pytest.raises(ValueError, match="path"):
        module.run({"interview_stage_handoff": descriptor}, str(tmp_path))
