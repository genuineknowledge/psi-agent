"""Unit tests for the ChatBI formal-source building blocks (no DB needed).

These assert the *rules* themselves (admission clauses, value domains,
year-explicitness, PG template shape) so the guards can never silently
regress while the real O2OA connection is still being provisioned.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mock-mcp"))

# mock-mcp is a sys.path tool dir, not a package: ty cannot resolve these
# statically, pytest can (path inserted above).  Same pattern as the tools.
import _admission as adm  # ty: ignore
import _o2oa_templates as o2  # ty: ignore

# Every builder must carry rule 1; a template that forgets it would answer
# drafts / in-flight tasks, which is the one failure the note calls out twice.
ALL_BUILDERS = (
    lambda: o2.published_task_list("tech"),
    lambda: o2.latest_formal_progress("group"),
    lambda: o2.historical_progress_versions(101),
    lambda: o2.task_detail_with_goals(101, 2026),
    lambda: o2.attachment_metadata(101, granted=True),
    # batch 2
    lambda: o2.task_search("tech"),
    lambda: o2.progress_range("tech", "2026-01-01", "2026-06-30"),
    lambda: o2.coverage_stats("publish_split"),
    lambda: o2.coverage_stats("unpublished"),
    lambda: o2.coverage_stats("never_reported"),
    lambda: o2.year_goal_list("tech", 2026),
    lambda: o2.milestone_list("tech"),
    lambda: o2.freshness_distribution("2026-08-15"),
    lambda: o2.freshness_overall("2026-08-15"),
    lambda: o2.stale_tasks("2026-08-15", 30),
    lambda: o2.latest_progress_drift(),
)


class TestAdmissionRules:
    def test_published_only(self):
        assert "workflow_status = 'published'" in adm.sql_task_admission("pg")
        assert "is_deleted = 0" in adm.sql_task_admission("pg")
        # dialect-neutral by design: demo and formal share the same wording
        assert adm.sql_task_admission("pg") == adm.sql_task_admission("mysql")

    def test_progress_uses_displayed_version_only(self):
        assert "is_published = 1" in adm.sql_published_progress("pg")

    def test_submission_only_published_round(self):
        assert "status = 'published'" in adm.sql_submission_published("pg")

    def test_workflow_status_domain(self):
        ok, err = adm.check_workflow_status("published")
        assert ok == "published" and err is None
        ok, err = adm.check_workflow_status("approved")  # not in domain
        assert ok is None and err and "值域" in err

    def test_observed_extra_status_is_accepted(self):
        """``cancelled`` shows up in oa-weekly data but not in the note's table.

        A distribution tool must be able to name it (otherwise those rows are
        silently dropped), while admission keeps matching only ``published``.
        """
        ok, err = adm.check_workflow_status("cancelled")
        assert ok == "cancelled" and err is None
        assert "cancelled" not in adm.WORKFLOW_STATUS_DOMAIN  # documented set unchanged
        assert adm.PUBLISHED not in adm.OBSERVED_EXTRA_WORKFLOW_STATUS

    def test_year_must_be_explicit(self):
        ok, err = adm.check_year(None)
        assert ok is None and "显式" in err
        ok, err = adm.check_year("2026")
        assert ok == 2026 and err is None

    def test_board_code_domain(self):
        ok, _err = adm.check_board_code("group")
        assert ok == "group"
        ok, _err = adm.check_board_code("tech")
        assert ok == "tech"
        ok, _err = adm.check_board_code("other")
        assert ok is None

    def test_historical_progress_requires_approved_version(self):
        clause = adm.sql_historical_progress("pg")
        assert f"status = {adm.PROGRESS_APPROVED}" in clause

    def test_ts_normalization_survives_text_columns(self):
        """``to_char(text)`` fails in PG, so the expression must cast first."""
        expr = adm.normalize_ts_sql("t.published_at", "pg")
        assert expr.startswith("to_char((") and ")::timestamp" in expr
        assert "YYYY-MM-DD" in expr

    def test_ts_parsing_for_comparisons(self):
        assert adm.parse_ts_sql("p.report_time", "pg") == "(p.report_time)::timestamp"
        assert "STR_TO_DATE" in adm.parse_ts_sql("p.report_time", "mysql")

    def test_date_normalization_casts_first(self):
        """``progress_date`` 在正式库是 date、在演示快照是 text,输出必须统一成字符串。"""
        expr = adm.normalize_date_sql("p.progress_date", "pg")
        assert expr == "to_char((p.progress_date)::date, 'YYYY-MM-DD')"

    def test_optional_table_degrades_instead_of_failing(self):
        hint = adm.require_optional_table("task_attachment", granted=False)
        assert hint and "未在本次只读授权范围内" in hint
        assert adm.require_optional_table("task_attachment", granted=True) is None
        # a granted-by-default table never degrades
        assert adm.require_optional_table("task", granted=False) is None


class TestO2oaTemplates:
    def test_every_template_carries_the_admission_guard(self):
        for build in ALL_BUILDERS:
            sql, _params = build()
            assert "workflow_status = 'published'" in sql, sql
            assert "is_deleted = 0" in sql, sql

    def test_published_task_list(self):
        sql, params = o2.published_task_list("tech", keyword="规划")
        assert "b.code = %s" in sql
        assert params[0] == "tech"
        assert "%规划%" in params  # ILIKE pattern
        assert "task_category" in sql
        assert params[-1] == 200  # LIMIT is the final parameter

    def test_board_code_required(self):
        with pytest.raises(ValueError):
            o2.published_task_list("all")

    def test_latest_formal_progress_uses_single_scan(self):
        """``DISTINCT ON`` replaces the per-row ``MAX`` correlated subquery."""
        sql, params = o2.latest_formal_progress("group")
        assert "DISTINCT ON (t.id)" in sql
        assert "ORDER BY t.id, p.version_no DESC" in sql
        assert "is_published = 1" in sql
        assert "MAX(" not in sql
        assert params == ("group", 200)

    def test_latest_formal_progress_is_ordered_for_reading(self):
        sql, _params = o2.latest_formal_progress("group")
        assert sql.rstrip().endswith("LIMIT %s")
        assert "ORDER BY s.task_name" in sql

    def test_historical_progress_is_year_and_status_bounded(self):
        sql, params = o2.historical_progress_versions(101, date_from="2026-01-01", date_to="2026-06-30")
        assert f"p.status = {adm.PROGRESS_APPROVED}" in sql
        assert "(p.progress_date)::timestamp >= %s" in sql
        assert "(p.progress_date)::timestamp <= %s" in sql
        assert params == (101, "2026-01-01", "2026-06-30", 50)

    def test_category_path_is_recursive_not_invented(self):
        sql, params = o2.category_path("group")
        assert "WITH RECURSIVE" in sql
        assert "cat.id = c.parent_id" in sql  # self-join upward
        assert "b.code = %s" in sql
        assert params == ("group",)

    def test_task_detail_needs_explicit_year(self):
        with pytest.raises(ValueError):
            o2.task_detail_with_goals(1, None)

    def test_task_detail_admission(self):
        sql, params = o2.task_detail_with_goals(101, 2026)
        assert "t.workflow_status = 'published'" in sql
        assert "m.is_deleted = 0" in sql  # milestone soft-delete guard
        assert params == (2026, 101)

    def test_attachment_metadata_degrades_when_not_granted(self):
        with pytest.raises(PermissionError) as exc:
            o2.attachment_metadata(101, granted=False)
        assert "task_attachment" in str(exc.value)

    def test_attachment_metadata_never_selects_the_file_body(self):
        sql, params = o2.attachment_metadata(101, granted=True)
        assert "storage_path" not in sql  # metadata only, never the object path
        assert "file_name" in sql
        assert params == (101, 50)


class TestBatch2Templates:
    """批次 2:检索 / 进展窗口 / 覆盖率 / 年度目标 / 里程碑 / 新鲜度。"""

    def test_task_search_filters_stay_inside_admission(self):
        sql, params = o2.task_search("tech", keyword="规划", category_name="一、规划", person="马跃进")
        assert "t.task_name ILIKE %s" in sql and "c.name = %s" in sql
        assert sql.count("%s") == len(params)
        # person 同时匹配负责人 / 牵头领导 / OA 用户号
        assert "t.project_owner_name ILIKE %s" in sql and "t.lead_owner_name ILIKE %s" in sql
        assert "t.owner_user_id = %s" in sql
        assert params[0] == "tech" and params[-1] == 200

    def test_progress_range_windows_on_displayed_versions(self):
        sql, params = o2.progress_range("tech", "2026-01-01", "2026-03-31")
        assert "is_published = 1" in sql
        assert "(p.progress_date)::timestamp >= %s" in sql
        assert "(p.progress_date)::timestamp <= %s" in sql
        assert "to_char((p.progress_date)::date, 'YYYY-MM-DD')" in sql  # 输出统一为字符串
        assert "history" not in sql.lower() and "status = 3" not in sql  # 不是历史版本那条路
        assert params == ("tech", "2026-01-01", "2026-03-31", 200)

    def test_coverage_scope_domains(self):
        with pytest.raises(ValueError):
            o2.coverage_stats("everything")  # 只有四个具名 scope

    def test_publish_split_counts_progress_rows_under_the_task_gate(self):
        """publish_split 数的是进展行、且带正式任务门(演示数据 943/123/1066)。

        不带门会读到 945/1068 —— 手工相加时最容易丢掉任务门,所以这条 SQL 必须
        有 task 的连接与准入条件。
        """
        sql, params = o2.coverage_stats("publish_split")
        assert "FROM task_progress p" in sql and "JOIN task t ON t.id = p.task_id" in sql
        assert "workflow_status = 'published'" in sql and "is_deleted = 0" in sql
        assert "FILTER (WHERE p.is_published = 1)" in sql
        assert params == ()

    def test_never_reported_uses_exists_not_null(self):
        """从未报进展必须按 NOT EXISTS 判:NULL 判据只能找出其中一部分。"""
        sql, params = o2.coverage_stats("never_reported")
        assert "NOT EXISTS" in sql and "p.is_published = 1" in sql
        assert "t.latest_progress_time IS NULL" not in sql
        assert params == (200,)

    def test_unpublished_splits_by_progress_own_status(self):
        sql, _params = o2.coverage_stats("unpublished")
        assert "p.status" in sql
        assert "count(DISTINCT p.task_id)" in sql  # 行数与任务数都要给
        assert "p.is_published = 0" in sql

    def test_import_split_uses_import_id_null(self):
        sql, _params = o2.coverage_stats("import_split")
        assert "p.import_id IS NULL" in sql
        assert "is_published = 1" in sql

    def test_year_goal_list_keeps_unfilled_rows(self):
        """LEFT JOIN 必须保留"已发布但当年目标未填写"的任务(答"未填写"而非消失)。"""
        sql, params = o2.year_goal_list("tech", 2026)
        assert "LEFT JOIN task_year_goal" in sql
        assert "(y.task_id IS NOT NULL) AS goal_filled" in sql
        assert params == (2026, "tech", 200)
        with pytest.raises(ValueError):
            o2.year_goal_list("tech", None)

    def test_milestone_list_guards_and_filters(self):
        sql, params = o2.milestone_list("group", year=2026, status=1)
        assert "m.is_deleted = 0" in sql
        assert "m.year = %s" in sql and "m.status = %s" in sql
        assert params == ("group", 2026, 1, 200)
        with pytest.raises(ValueError):
            o2.milestone_list("group", status=7)  # 只有 0/1

    def test_freshness_buckets_match_the_original_tool(self):
        """桶标签与排序必须与原工具一致(带序号前缀,按标签排序)。"""
        sql, params = o2.freshness_distribution("2026-08-15")
        assert "'4 从未报进展'" in sql and "'5 超过 180 天'" in sql
        assert "'1 30 天内'" in sql and "'2 31-90 天'" in sql and "'3 91-180 天'" in sql
        assert "ORDER BY freshness_bucket" in sql
        assert params == ("2026-08-15", "2026-08-15", "2026-08-15")

    def test_freshness_never_uses_the_wall_clock(self):
        """相对窗口一律以 as_of 为准:用 now() 会把窗口滑出数据(now_instead_of_as_of)。"""
        for build in (
            lambda: o2.freshness_distribution("2026-08-15"),
            lambda: o2.freshness_overall("2026-08-15"),
            lambda: o2.freshness_within("2026-08-15", 7),
            lambda: o2.stale_tasks("2026-08-15", 30),
        ):
            sql, params = build()
            assert "now()" not in sql, sql
            assert params[0] == "2026-08-15"
        with pytest.raises(ValueError):
            o2.freshness_distribution(None)  # 不给 as_of 直接拒绝
        with pytest.raises(ValueError):
            o2.stale_tasks("", 30)

    def test_freshness_in_flight_gate(self):
        sql, _params = o2.freshness_distribution("2026-08-15", in_flight_only=True)
        assert "t.status IN (0, 1)" in sql  # 在办 = 未开始 与 进行中

    def test_stale_tasks_put_never_reported_first(self):
        sql, params = o2.stale_tasks("2026-08-15", 30)
        assert "t.latest_progress_time IS NULL" in sql  # 从未报过的也算滞后
        assert "NULLS FIRST" in sql
        assert params[0] == "2026-08-15" and params[-1] == 200

    def test_drift_check_compares_denormalized_column_with_real_rows(self):
        sql, _params = o2.latest_progress_drift()
        assert "JOIN LATERAL" in sql and "max(p.report_time)" in sql
        assert "<>" in sql  # 不一致才算漂移
