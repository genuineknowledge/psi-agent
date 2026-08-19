from __future__ import annotations

import json
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
RESUME_ROOT = WORKFLOW_ROOT.parent / "resume-approval"
FINAL_INSTRUCTION_NAMES = (
    "stage-final-review.md",
    "prepare-final-human-review.md",
    "collect-final-decisions.md",
    "persist-final-results.md",
    "append-report.md",
)
FINAL_STATUSES = ["录用", "不录用", "待定"]
ALL_INTERVIEW_STATUSES = ["待安排", "待面试", "待补充", "已完成", *FINAL_STATUSES]


def _schema_tables() -> dict[str, dict]:
    schema = json.loads((RESUME_ROOT / "feishu-schema.json").read_text(encoding="utf-8"))
    return {table["config_key"]: table for table in schema["tables"]}


def _instruction(name: str) -> str:
    return (WORKFLOW_ROOT / "instructions" / name).read_text(encoding="utf-8")


def test_schema_places_final_status_only_on_interview_records() -> None:
    tables = _schema_tables()
    talent_fields = {field["field_name"]: field for field in tables["talent_pool_table_id"]["fields"]}
    interview_fields = {field["field_name"]: field for field in tables["interview_table_id"]["fields"]}

    assert "面试状态" not in talent_fields
    assert [option["name"] for option in interview_fields["面试状态"]["property"]["options"]] == ALL_INTERVIEW_STATUSES


def test_final_review_stages_use_exact_interview_rows_for_status() -> None:
    stage = _instruction("stage-final-review.md")
    human = _instruction("prepare-final-human-review.md")
    collect = _instruction("collect-final-decisions.md")
    persist = _instruction("persist-final-results.md")

    assert "`interview_table_id`" in stage
    assert "`interview_record_id`" in stage
    assert "`talent_record_id`" in stage
    assert "only to validate" in stage
    assert "complete table with pagination" in stage
    assert "`姓名`" in stage and "`匹配岗位`" in stage and "`初审状态=通过`" in stage
    assert '"table_id": "<interview_table_id>"' in stage
    assert '"interview_record_id": "..."' in stage
    assert '"talent_record_id": "..."' in stage

    assert "面试记录" in human
    assert "`interview_record_id`" in human
    assert "`面试状态`" in human

    assert "`interview_table_id`" in collect
    assert "`interview_record_id`" in collect
    assert "`talent_record_id`" in collect
    assert "reject duplicate interview ids" in collect
    assert "A `talent_record_id` may repeat" in collect
    assert '"interview_record_id": "..."' in collect
    assert '"talent_record_id": "..."' in collect

    assert "`interview_table_id`" in persist
    assert "`interview_record_id`" in persist
    assert '"storage_model": "single-visible-interview-row"' in persist
    assert '"interview_table_url": "..."' in persist
    assert "configured `base_url` and `interview_table_id`" in persist
    assert '"interview_record_id": "..."' in persist
    assert '"talent_record_id": "..."' in persist

    combined = "\n".join(_instruction(name) for name in FINAL_INSTRUCTION_NAMES)
    for forbidden in (
        "ten-column",
        "single-visible-talent-row",
        '"talent_pool_url"',
        "talent row's `面试状态`",
        "talent rows' `面试状态`",
    ):
        assert forbidden not in combined


def test_final_decision_output_names_both_record_ids() -> None:
    collect = _instruction("collect-final-decisions.md")
    confirmed_shape = collect[collect.index('"confirmed"') : collect.index('"pending"')]

    assert '"interview_record_id"' in confirmed_shape
    assert '"talent_record_id"' in confirmed_shape
    assert '"record_id"' not in confirmed_shape.replace('"interview_record_id"', "").replace('"talent_record_id"', "")
    for status in FINAL_STATUSES:
        assert status in collect


def test_audit_report_documents_the_two_fact_sources() -> None:
    report = _instruction("append-report.md")

    assert "人才库" in report and "初审" in report
    assert "面试记录" in report and "最终录用状态" in report
    assert "not a source of truth" in report


def test_dashboard_example_splits_talent_and_interview_tables() -> None:
    spec = json.loads((RESUME_ROOT / "dashboard-spec.inputs.example.json").read_text(encoding="utf-8"))

    assert "source_table" not in spec
    dashboards = {item["config_key"]: item for item in spec["dashboards"]}
    assert set(dashboards) == {"talent_pool_table_id", "interview_table_id"}
    talent_text = json.dumps(dashboards["talent_pool_table_id"], ensure_ascii=False)
    interview_text = json.dumps(dashboards["interview_table_id"], ensure_ascii=False)
    assert "面试状态" not in talent_text
    assert "总分" in talent_text
    assert "面试状态" in interview_text
    assert "面试加权总分" in interview_text
    assert "招聘漏斗" not in json.dumps(spec, ensure_ascii=False)
