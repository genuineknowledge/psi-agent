from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> str:
    return (ROOT / "interview-conclusion.workflow").read_text(encoding="utf-8")


def test_final_stage_names_and_prompts_bind_the_interview_record() -> None:
    workflow = _workflow()

    assert 'step_name(stage_final_review_step) == "Prepare existing interview rows for final review";' in workflow
    assert 'step_name(persist_final_results_step) == "Verify persisted final interview conclusions";' in workflow

    final_review_prompt = next(
        line for line in workflow.splitlines() if line.startswith("    agent_system_prompt(final_review_agent)")
    )
    persistence_prompt = next(
        line for line in workflow.splitlines() if line.startswith("    agent_system_prompt(persistence_agent)")
    )
    report_prompt = next(
        line for line in workflow.splitlines() if line.startswith("    agent_system_prompt(report_agent)")
    )

    for required in ("interview_table_id", "interview_record_id", "talent_record_id"):
        assert required in final_review_prompt
    assert "validation only" in final_review_prompt
    assert "interview_table_id" in persistence_prompt
    assert "interview_record_id" in persistence_prompt
    assert "talent row" not in persistence_prompt
    assert "initial-review" in report_prompt
    assert "final-hiring-status" in report_prompt


def test_final_agents_are_read_only_and_human_makes_the_status_change() -> None:
    workflow = _workflow()

    assert "allowed_tool(final_review_agent, feishu_bitable_search_records);" in workflow
    assert "allowed_tool(persistence_agent, feishu_bitable_search_records);" in workflow
    assert "allowed_tool(final_review_agent, feishu_bitable_create_records);" not in workflow
    assert "allowed_tool(persistence_agent, feishu_bitable_create_records);" not in workflow
    assert "allowed_tool(final_review_agent, feishu_bitable_update_records);" not in workflow
    assert "allowed_tool(persistence_agent, feishu_bitable_update_records);" not in workflow

    human = (ROOT / "instructions" / "prepare-final-human-review.md").read_text(encoding="utf-8")
    assert "Human" in human
    assert "面试记录" in human
    assert "`面试状态`" in human


def test_human_checkpoint_is_followed_by_exact_interview_row_readback() -> None:
    workflow = _workflow()

    human_index = workflow.index("step_name(final_human_review_step)")
    collect_index = workflow.index("step_name(collect_final_decisions_step)")
    persist_index = workflow.index("step_name(persist_final_results_step)")
    assert human_index < collect_index < persist_index
    assert (
        "consumes(collect_final_decisions_step) == "
        "[final_human_response, validated_hiring_conclusions, conclusion_run_id, feishu_config];"
    ) in workflow

    collect = (ROOT / "instructions" / "collect-final-decisions.md").read_text(encoding="utf-8")
    assert "chat reply is only a trigger" in collect
    assert "Never join by name" in collect
    assert "exact `interview_record_id`" in collect


def test_interview_validation_receives_authoritative_table_configuration() -> None:
    workflow = _workflow()

    assert (
        "consumes(validate_interview_evidence_step) == [\n"
        "        interview_evidence_items,\n"
        "        interview_record_ids,\n"
        "        feishu_config\n"
        "    ];"
    ) in workflow
    assert "schema 2.0 private handoff and schema 3.0 assessment" in workflow


def test_each_workflow_artifact_has_at_most_one_producer() -> None:
    workflow = _workflow()
    produced: list[str] = []
    for match in re.finditer(r"produces\([^)]*\) == \[([^]]+)\];", workflow):
        produced.extend(item.strip() for item in match.group(1).split(","))

    duplicates = sorted({artifact for artifact in produced if produced.count(artifact) > 1})
    assert duplicates == []
