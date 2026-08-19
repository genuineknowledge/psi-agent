from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_a2_graph_starts_from_pre_review_handoff_and_keeps_interview_outputs() -> None:
    workflow = (ROOT / "resume-interview-preparation.workflow").read_text(encoding="utf-8")

    assert "input_workflow(resume_interview_preparation) == [initial_review_handoff];" in workflow
    assert "output_workflow(resume_interview_preparation) == [" in workflow
    output = workflow[
        workflow.index("output_workflow(resume_interview_preparation)") : workflow.index(
            "];", workflow.index("output_workflow(resume_interview_preparation)")
        )
    ]
    assert "interview_manifest" in output
    assert "interview_record_ids" in output
    assert "interview_handoff_receipt" in output
    assert "initial_decision_bundle" in workflow
    assert "collect_initial_decisions_step" in workflow
    assert "persist_interview_stage_handoff_step" in workflow


def test_draft_generation_is_per_candidate_and_tool_permissions_are_separated() -> None:
    workflow = (ROOT / "resume-interview-preparation.workflow").read_text(encoding="utf-8")

    assert "foreach_item(prepare_interview_draft_step, approved_interview_tasks) == interview_task;" in workflow
    assert "max_concurrency(resume_interview_preparation) == 4;" in workflow
    assert "allowed_tool(interview_draft_agent, feishu_bitable_search_records);" in workflow
    assert "allowed_tool(interview_draft_agent, feishu_bitable_create_records);" not in workflow
    assert "allowed_tool(interview_write_agent, feishu_bitable_search_records);" in workflow
    assert "allowed_tool(interview_write_agent, feishu_bitable_create_records);" in workflow
    assert "allowed_tool(initial_decision_agent, feishu_bitable_search_records);" in workflow
    assert "allowed_tool(initial_decision_agent, feishu_bitable_create_records);" not in workflow


def test_program_boundaries_are_guarded_before_agent_or_downstream_use() -> None:
    workflow = (ROOT / "resume-interview-preparation.workflow").read_text(encoding="utf-8")

    expected = {
        "assert_initial_review_load_ready_step": (
            "load_initial_review_handoff_step",
            "collect_initial_decisions_step",
        ),
        "assert_interview_stage_handoff_ready_step": (
            "persist_interview_stage_handoff_step",
            "load_interview_stage_step",
        ),
        "assert_stage_load_ready_step": ("load_interview_stage_step", "prepare_interview_draft_step"),
        "assert_write_batch_ready_step": ("assemble_interview_write_batch_step", "write_interview_records_step"),
        "assert_interview_handoff_ready_step": ("persist_interview_handoffs_step", None),
    }
    for guard, (producer, consumer) in expected.items():
        assert f"step_executor({guard}) == program_error_assertion;" in workflow
        assert f"depends_on({guard}, {producer}) == True;" in workflow
        if consumer is not None:
            assert f"depends_on({consumer}, {guard}) == True;" in workflow


def test_decisions_are_reread_before_any_interview_draft_or_write() -> None:
    workflow = (ROOT / "resume-interview-preparation.workflow").read_text(encoding="utf-8")

    assert (
        "consumes(collect_initial_decisions_step) == "
        "[talent_pool_manifest, validated_candidate_assessments, "
        "initial_review_batch_id, initial_review_feishu_config];"
    ) in workflow
    assert (
        "consumes(persist_interview_stage_handoff_step) == "
        "[initial_decision_bundle, validated_candidate_assessments, talent_pool_manifest, role_catalog, "
        "initial_review_batch_id, initial_review_feishu_config];"
    ) in workflow
    assert "depends_on(load_interview_stage_step, assert_interview_stage_handoff_ready_step) == True;" in workflow
    assert workflow.index("step_name(collect_initial_decisions_step)") < workflow.index(
        "step_name(prepare_interview_draft_step)"
    )


def test_each_a2_artifact_has_at_most_one_producer() -> None:
    workflow = (ROOT / "resume-interview-preparation.workflow").read_text(encoding="utf-8")
    produced: list[str] = []
    for match in re.finditer(r"produces\([^)]*\) == \[([^]]+)\];", workflow):
        produced.extend(item.strip() for item in match.group(1).split(","))

    duplicates = sorted({artifact for artifact in produced if produced.count(artifact) > 1})
    assert duplicates == []


def test_prompts_enforce_read_only_generation_and_exact_content_writing() -> None:
    draft = (ROOT / "instructions" / "prepare-interviews.md").read_text(encoding="utf-8")
    writer = (ROOT / "instructions" / "write-interview-records.md").read_text(encoding="utf-8")

    assert "exactly one" in draft
    assert "generated_fields" not in draft
    assert "historical_record_count" not in draft
    assert '"warnings"' not in draft
    assert "interview_task_ref" not in draft
    assert "面试前摘要" in draft and "面试重点" in draft and "风险提示" in draft and "建议问题" in draft
    assert "feishu_bitable_create_records" not in draft
    assert "same-role" in draft
    assert "not newly generated" in draft
    assert "exact source order" in draft
    assert "The write-batch Program rejects any mismatch" in draft
    assert all(field in draft for field in ("evidence_anchor", "purpose", "positive_signal", "risk_signal"))

    assert "must not rewrite" in writer
    assert "six-field fingerprint" in writer
    assert "more than one" in writer
    assert "same-name" in writer
    assert "待安排" in writer
    assert "zero records" in writer
    assert "query again" in writer


def test_writer_consumes_only_program_validated_batch_and_local_config() -> None:
    workflow = (ROOT / "resume-interview-preparation.workflow").read_text(encoding="utf-8")

    assert "consumes(write_interview_records_step) == [interview_write_batch, feishu_config];" in workflow
    assert "consumes(persist_interview_handoffs_step) == [interview_manifest, interview_stage_bundle];" in workflow


def test_documentation_explains_explicit_a2_invocation_and_fresh_retry() -> None:
    a2_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    a_readme = (ROOT.parent / "resume-approval" / "README.md").read_text(encoding="utf-8")

    example = (ROOT / "resume-interview-preparation.inputs.example.json").read_text(encoding="utf-8")

    assert "initial_review_handoff" in a2_readme
    assert "sole input" in a2_readme
    assert "shape-only" in a2_readme
    assert "fresh A2 run" in a2_readme
    assert "same descriptor" in a2_readme
    assert "待审批" in a2_readme
    assert "rereads" in a2_readme
    assert "initial_review_handoff" in example
    assert "interview_stage_handoff" not in example
    assert "Workflow A ends" in a_readme
    assert "no workflow remains active" in a_readme
    assert "run_flow_resume" in a_readme
    assert "resume-interview-preparation" in a_readme
