from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PROGRAM_PATH = Path(__file__).resolve().parents[1] / "programs" / "build_user_facing_summary.py"
WORKFLOWS_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location("build_user_facing_summary", PROGRAM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_safe(summary: dict) -> None:
    serialized = json.dumps(summary, ensure_ascii=False)
    for private in (
        "13800138000",
        "candidate@example.com",
        "110101199001011234",
        "recPrivate123",
        "runPrivate123",
        "requestPrivate123",
        "0123456789abcdef0123456789abcdef01234567",
        "table=tblPrivate123",
    ):
        assert private not in serialized
    assert summary["schema_version"] == "1.0"
    assert summary["text"]
    assert "下一步:" in summary["text"]


def test_program_stdout_is_the_single_artifact_value_without_an_extra_envelope() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROGRAM_PATH)],
        input=json.dumps(
            {
                "inputs": {
                    "validated_candidate_assessments": {"status": "complete"},
                    "talent_pool_manifest": {
                        "status": "complete",
                        "records": [],
                        "failed_candidates": [],
                        "errors": [],
                    },
                }
            },
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        check=True,
    )

    summary = json.loads(completed.stdout)
    assert summary["schema_version"] == "1.0"
    assert summary["workflow"] == "resume-approval"
    assert "user_facing_summary" not in summary


def test_resume_approval_summary_is_complete_and_privacy_conscious() -> None:
    module = _load_module()
    summary = module.run(
        {
            "validated_candidate_assessments": {"status": "complete"},
            "talent_pool_manifest": {
                "status": "complete",
                "base_url": "https://example.feishu.cn/base/appSafe123?table=tblPrivate123",
                "failed_candidates": [],
                "errors": [],
                "records": [
                    {
                        "created": True,
                        "record_id": "recPrivate123",
                        "row_fingerprint": {
                            "姓名": "测试候选人",
                            "评级": "B",
                            "匹配岗位": "AI 应用工程师",
                            "面试建议": "建议面试",
                            "面试建议理由": "证据明确; 联系 candidate@example.com 获取更多材料。",
                        },
                    }
                ],
            },
            "initial_review_request": "internal request requestPrivate123",
            "feishu_config": {"user_key": "private-user"},
        }
    )

    assert summary["workflow"] == "resume-approval"
    assert summary["status"] == "complete"
    assert summary["counts"] == {"processed": 1, "succeeded": 1, "skipped": 0, "failed": 0}
    assert summary["candidates"][0]["姓名"] == "测试候选人"
    assert summary["candidates"][0]["理由"] == "详情已脱敏, 请在业务表中查看。"
    assert summary["resource_link"] == "https://example.feishu.cn/base/appSafe123"
    _assert_safe(summary)


def test_interview_preparation_summary_reports_created_and_reused_rows() -> None:
    module = _load_module()
    assessment = {
        "candidate_id": "0123456789abcdef",
        "candidate_name": "面试候选人",
        "grade": "A",
        "matched_role_name": "平台工程师",
        "interview_recommendation": "建议面试",
        "interview_recommendation_reason": "岗位证据充分。",
    }
    summary = module.run(
        {
            "interview_stage_bundle": {
                "status": "complete",
                "destination": {"base_url": "https://example.feishu.cn/base/appSafeInterview"},
                "approved": [{"assessment": assessment}],
            },
            "interview_manifest": {
                "status": "complete",
                "records": [{"created": True}, {"created": False}],
                "errors": [],
            },
            "interview_handoff_receipt": {"status": "complete", "errors": []},
            "feishu_config": {"app_token": "appPrivate123"},
        }
    )

    assert summary["workflow"] == "resume-interview-preparation"
    assert summary["status"] == "complete"
    assert summary["counts"] == {"processed": 2, "succeeded": 1, "skipped": 1, "failed": 0}
    assert summary["candidates"][0]["评级"] == "A"
    _assert_safe(summary)


def test_interview_conclusion_summary_uses_human_decision_without_internal_lineage() -> None:
    module = _load_module()
    conclusion = {
        "candidate_id": "0123456789abcdef",
        "assessment": {"grade": "B"},
        "matched_role": {"name": "数据工程师"},
        "interview_summary": "面试证据支持该结论。",
        "interview_record_id": "recPrivate123",
    }
    summary = module.run(
        {
            "validated_hiring_conclusions": {"status": "complete"},
            "final_decisions": {
                "status": "complete",
                "confirmed": [
                    {
                        "candidate_name": "终审候选人",
                        "decision": "录用",
                        "conclusion": conclusion,
                        "talent_record_id": "recTalentPrivate",
                    }
                ],
                "pending": [],
                "errors": [],
            },
            "result_write_receipt": {
                "status": "complete",
                "interview_table_url": "https://example.feishu.cn/base/appSafeFinal?table=tblPrivate123",
                "errors": [],
            },
            "report_result": {"status": "complete", "document_url": "https://example.feishu.cn/docx/docSafe"},
            "feishu_config": {"user_key": "private-user"},
        }
    )

    assert summary["workflow"] == "interview-conclusion"
    assert summary["status"] == "complete"
    assert summary["candidates"][0]["面试建议"] == "录用"
    assert summary["counts"] == {"processed": 1, "succeeded": 1, "skipped": 0, "failed": 0}
    _assert_safe(summary)


def test_blocked_summary_has_visible_diagnostic_and_next_action() -> None:
    module = _load_module()
    summary = module.run(
        {
            "validated_candidate_assessments": {"status": "blocked"},
            "talent_pool_manifest": {
                "status": "blocked",
                "records": [],
                "failed_candidates": ["0123456789abcdef"],
                "errors": [],
            },
        }
    )

    assert summary["status"] == "blocked"
    assert summary["counts"]["failed"] == 1
    assert "失败步骤:" in summary["text"]
    assert "诊断:" in summary["text"]
    _assert_safe(summary)


def test_required_stage_or_validation_status_cannot_be_bypassed() -> None:
    module = _load_module()
    interview_preparation = module.run(
        {
            "interview_stage_bundle": {"status": "blocked", "approved": []},
            "interview_manifest": {"status": "complete", "records": [], "errors": []},
            "interview_handoff_receipt": {"status": "complete", "errors": []},
        }
    )
    interview_conclusion = module.run(
        {
            "validated_hiring_conclusions": {"status": "blocked"},
            "final_decisions": {"status": "complete", "confirmed": [], "pending": [], "errors": []},
            "result_write_receipt": {"status": "complete", "errors": []},
            "report_result": {"status": "complete"},
        }
    )

    assert interview_preparation["status"] == "blocked"
    assert interview_conclusion["status"] == "blocked"


def test_all_recruitment_workflows_publish_summary_after_the_last_business_step() -> None:
    contracts = {
        "resume-approval/resume-approval.workflow": "assert_cleanup_program_ready_step",
        (
            "resume-interview-preparation/resume-interview-preparation.workflow"
        ): "assert_interview_handoff_ready_step",
        "interview-conclusion/interview-conclusion.workflow": "append_report_step",
    }
    for relative, predecessor in contracts.items():
        source = (WORKFLOWS_ROOT / relative).read_text(encoding="utf-8")
        assert "user_facing_summary" in source
        assert "build_user_facing_summary.py" in source
        assert f"depends_on(build_user_facing_summary_step, {predecessor}) == True;" in source

    conclusion = (WORKFLOWS_ROOT / "interview-conclusion/interview-conclusion.workflow").read_text(encoding="utf-8")
    assert conclusion.index("step_name(final_human_review_step)") < conclusion.index(
        "step_name(build_user_facing_summary_step)"
    )
