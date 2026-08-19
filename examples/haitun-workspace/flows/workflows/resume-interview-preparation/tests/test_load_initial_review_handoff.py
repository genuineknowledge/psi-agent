from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "load_initial_review_handoff.py"
BATCH_ID = "resume-20260810-120000-123456"
CANDIDATE_ID = "a" * 16
ASSESSMENT_REVISION = "d" * 64
ROLE_REVISION = "c" * 64
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
    spec = importlib.util.spec_from_file_location("load_initial_review_handoff", PROGRAM_PATH)
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


def _document() -> dict:
    assessment = _assessment()
    return {
        "schema_version": "1.0",
        "status": "ready_for_review",
        "batch_id": BATCH_ID,
        "destination": {
            "app_token": "app-token",
            "base_url": "https://example.feishu.cn/base/app-token",
            "talent_pool_table_id": "tblTalent",
            "interview_table_id": "tblInterview",
        },
        "role_catalog": {
            "schema_version": "1.0",
            "source_document_sha256": ROLE_REVISION,
            "roles": [
                {
                    "role_key": ROLE_KEY,
                    "name": "AI应用开发工程师",
                    "status": "active",
                }
            ],
        },
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
                    "record_id": "recTalent00001",
                    "candidate_id": CANDIDATE_ID,
                    "assessment_revision": ASSESSMENT_REVISION,
                    "row_fingerprint": _row_fingerprint(),
                    "created": True,
                }
            ],
            "errors": [],
        },
    }


def _apply_warning_only_values(document: dict) -> None:
    assessment = document["validated_candidate_assessments"]["assessments"][0]
    assessment.update(
        education="",
        education_background="",
        resume_summary=[],
        match_points=[],
        mismatch_points=[{"requirement": "", "resume_evidence": []}],
        interview_recommendation_reason="",
    )
    document["validated_candidate_assessments"]["constraint_warnings"] = ["content remains table-writeable"]
    document["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
        学历="",
        **{
            "毕业院校/背景": "",
            "简历摘要": "",
            "匹配点": "",
            "不匹配点": "- 风险\uff1a\uff1b依据\uff1a",
            "面试建议理由": "",
        },
    )


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _write_case(workspace: Path, document: dict | None = None) -> tuple[dict, Path]:
    payload = copy.deepcopy(document if document is not None else _document())
    relative = Path(".psi/resume-approval/initial-review-handoffs") / f"{BATCH_ID}.json"
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _canonical_bytes(payload)
    path.write_bytes(content)
    descriptor = {
        "schema_version": "1.0",
        "status": "ready_for_review",
        "batch_id": BATCH_ID,
        "expected_count": len(payload["talent_pool_manifest"]["records"]),
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "next_workflow": "resume-interview-preparation",
        "next_input": "initial_review_handoff",
    }
    defaults = workspace / "flows/workflows/resume-approval/resume-approval.defaults.json"
    defaults.parent.mkdir(parents=True, exist_ok=True)
    defaults.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "feishu_config": payload["destination"] | {"user_key": "local-only"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return descriptor, path


def test_loads_verified_review_source_for_live_decision_collection(tmp_path: Path) -> None:
    module = _load_module()
    descriptor, _ = _write_case(tmp_path)

    result = module.run({"initial_review_handoff": descriptor}, str(tmp_path))

    assert set(result) == {
        "initial_review_stage_bundle",
        "validated_candidate_assessments",
        "talent_pool_manifest",
        "role_catalog",
        "initial_review_batch_id",
        "initial_review_feishu_config",
        "initial_review_load_manifest",
    }
    assert result["initial_review_batch_id"] == BATCH_ID
    assert result["validated_candidate_assessments"] == _document()["validated_candidate_assessments"]
    assert result["talent_pool_manifest"] == _document()["talent_pool_manifest"]
    assert result["initial_review_feishu_config"]["user_key"] == "local-only"
    assert result["initial_review_load_manifest"] == {
        "schema_version": "1.0",
        "status": "complete",
        "expected_count": 1,
        "errors": [],
    }


def test_loads_warning_only_table_writeable_values(tmp_path: Path) -> None:
    module = _load_module()
    document = _document()
    _apply_warning_only_values(document)
    descriptor, _ = _write_case(tmp_path, document)

    result = module.run({"initial_review_handoff": descriptor}, str(tmp_path))

    assert result["validated_candidate_assessments"] == document["validated_candidate_assessments"]
    assert result["talent_pool_manifest"] == document["talent_pool_manifest"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda descriptor, _path: descriptor.update(status="complete"), "ready_for_review"),
        (lambda descriptor, _path: descriptor.update(expected_count=2), "expected_count"),
        (lambda descriptor, _path: descriptor.update(sha256="0" * 64), "hash"),
        (lambda descriptor, _path: descriptor.update(path="../escape.json"), "path"),
    ],
)
def test_rejects_invalid_descriptor(tmp_path: Path, mutation, message: str) -> None:
    module = _load_module()
    descriptor, path = _write_case(tmp_path)
    mutation(descriptor, path)

    with pytest.raises((TypeError, ValueError), match=message):
        module.run({"initial_review_handoff": descriptor}, str(tmp_path))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda doc: doc["talent_pool_manifest"]["records"][0].update(candidate_id="e" * 16),
            "cover",
        ),
        (
            lambda doc: doc["talent_pool_manifest"]["records"][0].update(assessment_revision="f" * 64),
            "revision",
        ),
        (
            lambda doc: doc["talent_pool_manifest"]["records"][0]["row_fingerprint"].pop("面试建议理由"),
            "row_fingerprint",
        ),
        (
            lambda doc: doc["validated_candidate_assessments"]["assessments"][0][
                "verification_questions"
            ].pop(),
            "3 to 6",
        ),
        (
            lambda doc: doc["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(
                问题库="1. [真实性核验] 被篡改的问题"
            ),
            "问题库",
        ),
        (
            lambda doc: doc["talent_pool_manifest"]["records"][0]["row_fingerprint"].update(总分=99),
            "总分",
        ),
        (
            lambda doc: doc["validated_candidate_assessments"].update(assessments=[]),
            "non-empty",
        ),
    ],
)
def test_rejects_forged_private_review_source(tmp_path: Path, mutation, message: str) -> None:
    module = _load_module()
    document = _document()
    mutation(document)
    descriptor, _ = _write_case(tmp_path, document)

    with pytest.raises((TypeError, ValueError), match=message):
        module.run({"initial_review_handoff": descriptor}, str(tmp_path))


def test_rejects_changed_local_destination(tmp_path: Path) -> None:
    module = _load_module()
    descriptor, _ = _write_case(tmp_path)
    defaults = tmp_path / "flows/workflows/resume-approval/resume-approval.defaults.json"
    current = json.loads(defaults.read_text(encoding="utf-8"))
    current["feishu_config"]["talent_pool_table_id"] = "tblChanged"
    defaults.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(ValueError, match="destination"):
        module.run({"initial_review_handoff": descriptor}, str(tmp_path))
