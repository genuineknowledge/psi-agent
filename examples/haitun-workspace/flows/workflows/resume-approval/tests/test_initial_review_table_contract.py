from __future__ import annotations

import json
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
AI_FIELDS = [
    "姓名",
    "评级",
    "学历",
    "毕业院校/背景",
    "简历摘要",
    "总分",
    "匹配岗位",
    "匹配点",
    "不匹配点",
    "面试建议",
    "面试建议理由",
    "问题库",
]
ALL_FIELDS = [
    "姓名",
    "简历附件",
    "评级",
    "学历",
    "毕业院校/背景",
    "总分",
    "备注",
    "匹配岗位",
    "匹配点",
    "不匹配点",
    "面试建议",
    "面试建议理由",
    "问题库",
    "初审状态",
    "简历摘要",
]


def _schema_table() -> dict:
    schema = json.loads((WORKFLOW_ROOT / "feishu-schema.json").read_text(encoding="utf-8"))
    return next(table for table in schema["tables"] if table["config_key"] == "talent_pool_table_id")


def test_public_contract_uses_runtime_table_ids_without_deployment_values() -> None:
    """The reusable bundle must obtain deployment resources from runtime configuration."""
    example = json.loads((WORKFLOW_ROOT / "resume-approval.defaults.inputs.example.json").read_text(encoding="utf-8"))
    schema = json.loads((WORKFLOW_ROOT / "feishu-schema.json").read_text(encoding="utf-8"))
    prompt = (WORKFLOW_ROOT / "instructions" / "stage-initial-review.md").read_text(encoding="utf-8")

    assert example["feishu_config"]["talent_pool_table_id"] == "tblExampleTalentPool001"
    assert example["feishu_config"]["interview_table_id"] == "tblExampleInterview001"
    required = [table for table in schema["tables"] if table["required_by_workflow"]]
    assert required
    assert all("table_id" not in table for table in required)
    assert "non-empty runtime `feishu_config.talent_pool_table_id`" in prompt
    assert '"table_id": "exact feishu_config.talent_pool_table_id"' in prompt
    assert "=tbl" not in prompt


def test_schema_matches_the_reusable_15_field_attachment_contract() -> None:
    """Field drift must be visible before an Agent attempts a create call."""
    table = _schema_table()

    assert table["table_name"] == "候选人才库"
    assert "table_id" not in table
    assert table["default_view_name"] == "候选人看板"
    assert [field["field_name"] for field in table["fields"]] == ALL_FIELDS
    assert [field["type"] for field in table["fields"]] == [1, 17, 3, 1, 1, 2, 1, 1, 1, 1, 3, 1, 1, 3, 1]
    by_name = {field["field_name"]: field for field in table["fields"]}
    assert [item["name"] for item in by_name["评级"]["property"]["options"]] == list("ABCDEF")
    assert [item["name"] for item in by_name["面试建议"]["property"]["options"]] == [
        "建议面试",
        "不建议面试",
    ]
    assert [item["name"] for item in by_name["初审状态"]["property"]["options"]] == [
        "待审批",
        "通过",
        "不通过",
    ]
    assert "岗位方向" not in by_name


def test_stage_prompt_maps_15_fields_and_fingerprints_only_12_ai_fields() -> None:
    """Human fields and unstable attachment tokens must stay outside row identity."""
    prompt = (WORKFLOW_ROOT / "instructions" / "stage-initial-review.md").read_text(encoding="utf-8")

    for field in ALL_FIELDS:
        assert f"`{field}`" in prompt
    assert len(AI_FIELDS) == 12
    assert "问题库" in AI_FIELDS
    assert {"简历附件", "备注", "初审状态"}.isdisjoint(AI_FIELDS)
    assert "12 个 AI 所有字段" in prompt
    assert "`简历附件` does not enter the fingerprint" in prompt
    assert "`备注` and `初审状态` do not enter the fingerprint" in prompt
    assert "set `备注` to an empty string" in prompt
    assert "`初审状态` to `待审批`" in prompt
    assert "岗位方向" not in prompt
    assert "`简历摘要`: `resume_summary`" in prompt
    assert '"\\n".join(resume_summary)' in prompt
    assert "`- 要求\uff1a…\uff1b证据\uff1a…`" in prompt
    assert "`- 风险\uff1a…\uff1b依据\uff1a…`" in prompt
    assert "join multiple `resume_evidence` entries with `、` in source order" in prompt
    assert "<1-based index>. [<category>] <question>" in prompt
    assert "Never expose `evidence_anchor`, `purpose`, `positive_signal`, or `risk_signal`" in prompt
    assert "use an empty string for an empty list" in prompt
    assert prompt.count("use an empty string for an empty point list") == 2
    assert "候选人看板" in prompt
    for legacy in ("`评分`", "`学校`", "`基础画像`", "`能力画像`", "`面试状态`"):
        assert legacy not in prompt


def test_attachment_contract_is_sha_bound_idempotent_and_read_back() -> None:
    """The Agent contract is the executable boundary for native attachment tool calls."""
    prompt = (WORKFLOW_ROOT / "instructions" / "stage-initial-review.md").read_text(encoding="utf-8")

    assert "assessment.source.sha256" in prompt
    assert "staged_resume_file.sha256" in prompt
    assert "Require exactly one matching descriptor" in prompt
    assert "Never associate a file by candidate name" in prompt
    assert "20971520" in prompt
    assert "feishu_drive_upload(file_path=<internal path>" in prompt
    assert 'parent_type="bitable_file"' in prompt
    assert '`[{"file_token": "<returned token>"}]`' in prompt
    assert "Resolve the complete fingerprint before uploading" in prompt
    assert "reuse the row. Do not upload or update anything" in prompt
    assert '`{"简历附件": [{"file_token": "<returned token>"}]}`' in prompt
    assert "Passing any AI field, `备注`, or `初审状态`" in prompt
    assert "After every create or attachment backfill attempt" in prompt
    assert "attachment object containing the exact returned `file_token`" in prompt
    assert '"attachment_persisted": true' in prompt
    assert "never contains an attachment token, original upload filename, local path, temporary URL" in prompt


def test_collect_prompt_joins_exact_record_ids_and_accepts_only_two_decisions() -> None:
    """A chat reply or same-name row must never substitute for the stored Feishu record id."""
    prompt = (
        WORKFLOW_ROOT.parent / "resume-interview-preparation" / "instructions" / "collect-initial-decisions.md"
    ).read_text(encoding="utf-8")

    assert "exact Feishu `record_id`" in prompt
    assert "Never join by name" in prompt
    assert "`通过` or `不通过`" in prompt
    assert "12-field" in prompt
    assert "chat" not in prompt.lower()


def test_talent_agent_receives_staged_files_and_cleanup_waits_for_persistence() -> None:
    """The graph must keep staged bytes alive through upload and immutable handoff validation."""
    workflow = (WORKFLOW_ROOT / "resume-approval.workflow").read_text(encoding="utf-8")
    system_line = next(
        line for line in workflow.splitlines() if line.startswith("    agent_system_prompt(talent_pool_agent)")
    )
    start = workflow.index("step_name(stage_initial_review_step)")
    end = workflow.index("step_name(persist_initial_review_handoff_step)", start)
    block = workflow[start:end]

    assert "15-field" in system_line
    assert "12-field" in system_line
    for forbidden in (
        "14-field",
        "13-field",
        "11-field",
        "10-field",
        "role direction",
        "ten-column",
        "面试状态",
        "基础画像",
        "能力画像",
    ):
        assert forbidden not in system_line
    assert "target_role" not in block
    assert (
        "consumes(stage_initial_review_step) == "
        "[validated_candidate_assessments, staged_resume_files, batch_id, feishu_config];"
    ) in block
    assert "allowed_tool(talent_pool_agent, feishu_drive_upload);" in workflow
    assert "allowed_tool(talent_pool_agent, feishu_bitable_update_record);" in workflow
    assert workflow.index("step_name(stage_initial_review_step)") < workflow.index(
        "step_name(cleanup_temporary_files_step)"
    )
    assert (
        "depends_on(cleanup_temporary_files_step, assert_initial_review_handoff_ready_step) == True;" in workflow
    )
    assert (
        "depends_on(cleanup_temporary_files_step, assert_repair_merge_round_2_program_ready_step)" not in workflow
    )
