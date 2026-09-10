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
import _formal  # ty: ignore
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
        # 排最前的判据照抄参考实现:布尔键排在时间键之前,NULL 因此排最前
        assert "ORDER BY t.latest_progress_time IS NOT NULL, t.latest_progress_time, t.id" in sql
        assert "AS days_since" in sql and "::date)::int" in sql  # 按日期相减,不是 date - timestamp
        assert params[0] == "2026-08-15" and params[-1] == 200

    def test_stale_tasks_reported_only_drops_the_no_day_rows(self):
        sql, _params = o2.stale_tasks("2026-08-15", 30, reported_only=True)
        assert "t.latest_progress_time IS NOT NULL" in sql
        assert "t.latest_progress_time IS NULL\n" in sql  # 滞后判据仍在(含从未报过那半支)

    def test_stale_listing_columns_match_the_reference_query(self):
        sql, _params = o2.stale_tasks("2026-08-15", 30)
        assert "t.id, t.task_name, t.status," in sql
        assert "t.task_no" not in sql and "project_owner_name" not in sql

    def test_recent_reporters_has_no_status_gate(self):
        # 问的是"有没有报进展",不是"任务在不在办"
        sql, params = o2.recent_reporters("2026-08-15", 7)
        assert "t.status IN (0, 1)" not in sql
        assert "ORDER BY t.latest_progress_time DESC, t.id" in sql
        assert params == ("2026-08-15", "2026-08-15", 7, 200)

    def test_freshness_within_reports_all_three_numbers(self):
        sql, params = o2.freshness_within("2026-08-15", 7)
        assert "AS task_count" in sql and "newest_progress" in sql and "days_behind" in sql
        assert params == ("2026-08-15", "2026-08-15", 7)

    def test_lag_bands_read_each_board_from_its_own_table(self):
        sql, _params = o2.freshness_lag_bands("2026-08-15")
        assert "task_group_progress_history" in sql and "task_progress" in sql
        assert "'1 0-7 天'" in sql and "'5 无正式进展'" in sql
        assert "b.code = 'group'" in sql  # 看板用 code 判,不用 id(两库 id 不同)

    def test_lag_bands_need_the_group_history_grant(self):
        with pytest.raises(PermissionError):
            o2.freshness_lag_bands("2026-08-15", group_history_granted=False)

    def test_stale_axis_board_joins_the_board_even_without_a_filter(self):
        # 轴自己就要 JOIN:等调用方给看板过滤才 JOIN,board 轴会直接报缺表
        sql, params = o2.stale_grouped("2026-08-15", 90, "board", recent_end=False)
        assert "JOIN task_board b ON b.id = t.board_id" in sql
        assert "ORDER BY stale_pct DESC, bucket" in sql
        assert params[-1] == 200

    def test_stale_grouped_sorts_the_end_the_question_asks_about(self):
        sql, _params = o2.stale_grouped("2026-08-15", 90, "project_group", recent_end=True)
        assert "ORDER BY active_pct DESC, bucket" in sql
        assert "'(未填)'" in sql

    def test_drift_check_compares_denormalized_column_with_real_rows(self):
        sql, _params = o2.latest_progress_drift()
        assert "JOIN LATERAL" in sql and "max(p.report_time)" in sql
        assert "<>" in sql  # 不一致才算漂移


class TestCoverageScopes:
    """覆盖率其余 scope:summary / version_gaps / pending_review / unpublished_by_task / formal_coverage。"""

    def test_summary_scope_shape(self):
        sql, params = o2.coverage_stats("summary")
        assert "count(DISTINCT p.task_id)" in sql
        assert "avg_rounds_per_task" in sql and "NULLIF" in sql  # 分母为 0 时不能炸
        assert "is_published = 1" in sql
        assert params == ()

    def test_version_gaps_judged_on_the_aggregate(self):
        """缺号 = 最大期号 - 实际期数,判据是聚合结果,所以必须用 HAVING。"""
        sql, params = o2.coverage_stats("version_gaps")
        assert "HAVING max(p.version_no) - count(*) <> 0" in sql
        assert params == (200,)

    def test_pending_review_carries_public_version(self):
        sql, params = o2.coverage_stats("pending_review")
        assert "p.is_published = 0" in sql and "p.status = 1" in sql
        assert "AS public_version" in sql  # "对外还是上一期"这半句要靠它
        assert "(SELECT max(q.version_no)" in sql
        assert params == (200,)

    def test_unpublished_by_task_does_not_apply_the_publish_gate(self):
        """它的筛选条件是"提交单已发布",不是"任务已发布"——两者不能混。"""
        sql, params = o2.coverage_stats("unpublished_by_task")
        assert "s.status = 'published'" in sql
        assert "workflow_status" not in sql
        assert "count(DISTINCT p.version_no)" in sql  # 期数,不是行数
        assert params == (200,)

    def test_formal_coverage_is_the_union_of_both_progress_tables(self):
        sql, params = o2.formal_coverage(group_history_granted=True)
        assert "FROM task_progress p" in sql and "FROM task_group_progress_history h" in sql
        assert "coverage_pct" in sql
        assert params == ()

    def test_formal_coverage_degrades_when_group_table_not_granted(self):
        sql, _params = o2.formal_coverage(group_history_granted=False)
        assert "task_group_progress_history" not in sql  # 宁可少答一半也不把并集算错
        assert "FROM task_progress p" in sql

    def test_latest_round_is_one_row_per_task_not_full_history(self):
        sql, params = o2.latest_round()
        assert "row_number() OVER (PARTITION BY p.task_id" in sql
        assert "ORDER BY p.version_no DESC, p.id DESC) AS rn" in sql
        assert "p.rn = 1" in sql
        assert "p.next_work IS NOT NULL" in sql and "p.next_work <> ''" in sql
        assert "MAX(" not in sql
        assert params == (200,)

    def test_latest_round_supports_project_group(self):
        sql, params = o2.latest_round(project_group="算力网络组")
        assert "trim(t.project_group) = %s" in sql
        assert params == ("算力网络组", 200)

    def test_missing_next_only_looks_at_the_latest_round(self):
        sql, params = o2.missing_next()
        assert "p.rn = 1" in sql
        assert "(p.next_work IS NULL OR p.next_work = '')" in sql
        assert params == ()


class TestSubmissionScopes:
    """提交单 / 审批域:看板下推、在途枚举、驳回率分子分母。"""

    def test_submission_gate_has_no_publish_filter(self):
        """提交单域只加 t.is_deleted = 0:462 = 470 行减去 8 个软删任务下的单。"""
        for scope in ("by_kind", "external_ids", "inflight_count", "rounds_per_task"):
            sql, _params = o2.submission_stats(scope)
            assert "t.is_deleted = 0" in sql
            assert "workflow_status" not in sql, scope

    def test_inflight_is_enumerated_not_negated(self):
        """写成 status <> 'published' 会多算 cancelled 那张(60 vs 59)。"""
        sql, _params = o2.submission_stats("inflight_count")
        for status in o2.SUBMISSION_INFLIGHT:
            assert f"'{status}'" in sql
        assert "rejected" in o2.SUBMISSION_INFLIGHT  # 驳回也算在途
        assert "cancelled" not in o2.SUBMISSION_INFLIGHT
        assert "status <> 'published'" not in sql and "status != 'published'" not in sql

    def test_external_ids_exposes_the_three_identifier_columns(self):
        sql, _params = o2.submission_stats("external_ids")
        assert "o2_process_id" in sql and "o2_work_id" in sql and "o2_task_id" in sql
        assert sql.count("IS NOT NULL") == 3

    def test_rejected_by_board_keeps_numerator_and_denominator_on_submissions(self):
        sql, _params = o2.submission_stats("rejected_by_board")
        assert "s.status = 'rejected'" in sql
        assert "JOIN task_board b" in sql  # 看板从任务侧下推
        assert "count(*)" in sql  # 分母是该看板全部提交单
        assert "rejected_pct" in sql

    def test_board_pushdown_carries_the_filter(self):
        sql, params = o2.submission_stats("by_kind", board_code="group")
        assert "b.code = %s" in sql
        assert params == ("group",)

    def test_rounds_per_task_returns_both_sides(self):
        sql, _params = o2.submission_stats("rounds_per_task")
        assert "count(DISTINCT s.task_id)" in sql and "count(*)" in sql
        assert "rounds_per_task" in sql

    def test_inflight_by_kind_is_the_status_times_kind_axis(self):
        sql, _params = o2.submission_stats("inflight_by_kind")
        assert "s.status, s.submission_kind" in sql  # 与 inflight_by_board 不是同一根轴
        assert "GROUP BY s.status, s.submission_kind" in sql

    def test_unknown_scope_rejected(self):
        with pytest.raises(ValueError):
            o2.submission_stats("everything")


class TestTextCheckRules:
    """文本规则域:正则固化在服务端,且必须同时扫技术组与集团组两张进展表。"""

    def test_unknown_rule_rejected(self):
        with pytest.raises(ValueError):
            o2.text_check("magic")

    def test_scans_both_progress_tables(self):
        sql, _params = o2.text_check("number_conflict")
        assert "task_progress p" in sql
        assert "task_group_progress_history h" in sql  # 任务 103 V8 的冲突在这张表里
        assert "UNION ALL" in sql

    def test_degrades_when_group_history_not_granted(self):
        sql, _params = o2.text_check("number_conflict", group_history_granted=False)
        assert "task_group_progress_history" not in sql
        assert "task_progress p" in sql

    def test_latest_mode_uses_two_level_ordering(self):
        sql, _params = o2.text_check("availability")
        assert "ORDER BY q.version_no DESC, q.id DESC LIMIT 1" in sql
        assert "max(h2.version_no)" in sql  # 集团历史表按最大期号取最新

    def test_all_versions_mode_keeps_every_published_round(self):
        sql, _params = o2.text_check("number_conflict", all_versions=True)
        assert "ORDER BY q.version_no DESC, q.id DESC LIMIT 1" not in sql
        assert "max(h2.version_no)" not in sql
        assert sql.count("is_published = 1") >= 2

    def test_availability_rule_threshold(self):
        sql, params = o2.text_check("availability")
        # 字面 % 在 SQL 文本里必须转义成 %%(否则被 psycopg 当占位符)
        assert "可用性(\\d+(?:\\.\\d+)?)%%" in sql
        assert "::numeric < 90" in sql
        assert params == (200,)

    def test_keyword_rule_default_and_custom(self):
        sql, params = o2.text_check("keyword")
        assert "next_work ~ %s" in sql
        assert params == ("协调|协同|联动|牵头组织", 200)
        _sql2, params2 = o2.text_check("keyword", keyword="协同")
        assert params2 == ("协同", 200)

    def test_number_conflict_reports_three_labels(self):
        sql, params = o2.text_check("number_conflict")
        assert "hard_report_gt_draft" in sql
        assert "hard_consult_gt_draft" in sql
        assert "sum_anomaly" in sql
        assert "> 100" in sql  # 阶段数量之和异常的门槛
        assert "draft_cnt IS NOT NULL" in sql  # 没有草案数的行不判
        assert params == (200,)

    def test_task_filter_is_passed_to_both_sides(self):
        sql, params = o2.text_check("number_conflict", task_id=103)
        assert sql.count("t.id = %s") == 2  # 两支各自过滤
        assert params == (103, 103, 200)


class TestFormalBackend:
    """正式源适配层(_formal):只测不连库的判定与回落,连库部分由端到端 harness 覆盖。"""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("TASK_BOARD_DATA_SOURCE", raising=False)
        assert _formal.enabled() is False
        assert _formal.dispatch("weekly_task_query", board="tech") is None

    def test_unknown_tool_falls_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.enabled() is True
        assert _formal.dispatch("weekly_schema", board="tech") is None
        # 用真正未接线的工具做断言:已接线的工具会去连库,不能拿来当反例
        assert _formal.dispatch("weekly_aggregate", by="board") is None
        assert _formal.dispatch("weekly_task_lifecycle") is None

    def test_unmigrated_arguments_fall_back_instead_of_narrowing(self, monkeypatch):
        """未迁移的参数组合必须回落演示路径,绝不能返回一个范围更小的答案。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        # by= 只给分组轴而没有天数:参考实现是**明确报错并指路**,不是静默回退
        by_only = _formal.dispatch("weekly_freshness_distribution", by="board")
        assert by_only is not None and by_only["ok"] is False
        assert by_only["error"]["code"] == "invalid_argument"
        assert "stale_days 或 recent_days" in by_only["error"]["message"]
        assert _formal.dispatch("weekly_freshness_distribution", by="不存在") is None
        assert _formal.dispatch("weekly_freshness_distribution", task="1") is None
        assert _formal.dispatch("weekly_progress_coverage", scope="latest_status") is None
        assert _formal.dispatch("weekly_task_query", status="9") is None  # 非法状态交给演示路径报错

    def test_source_tables_extraction(self):
        tables = _formal.source_tables("SELECT 1 FROM task t JOIN task_progress p ON p.task_id = t.id")
        assert tables == ["task", "task_progress"]

    def test_as_of_prefers_explicit_env(self, monkeypatch):
        monkeypatch.setenv("GUOSHU_AS_OF", "2026-08-15")
        assert _formal.as_of() == "2026-08-15"
        monkeypatch.delenv("GUOSHU_AS_OF")
        assert _formal.as_of()  # 正式活库默认取当天

    def test_formal_snapshot_note_is_not_the_demo_one(self):
        assert "正式只读源" in _formal.FORMAL_SNAPSHOT_NOTE
        assert "演示" in _formal.FORMAL_SNAPSHOT_NOTE  # 明确写出"非演示数据"


class TestRankShapes:
    """两个排名工具各有一份参考查询,集合与列名都不同,不能共用一种形状。"""

    def test_weekly_rank_shape_keeps_zero_rows(self):
        # weekly_rank 问得到"最少",零值任务必须在场(inner_join_drops_zero)
        sql, _params = o2.rank_tasks(metric="progress_rounds", mode="cut", top=3)
        assert "LEFT JOIN task_progress x" in sql
        assert "AS metric_value" in sql
        assert "AS cnt" not in sql

    def test_ranking_shape_is_inner_join_with_reference_column_names(self):
        # weekly_task_ranking 的参考查询是 JOIN + COUNT(*) AS cnt + ORDER BY cnt DESC, t.id
        sql, _params = o2.rank_tasks(
            metric="attachments", mode="cut", top=3, shape="count", granted_optional=("task_attachment",)
        )
        assert "JOIN task_attachment x" in sql and "LEFT JOIN task_attachment" not in sql
        assert "AS id" in sql and "AS cnt" in sql
        assert "ORDER BY cnt DESC, id" in sql
        assert "total_count" not in sql
        assert "AS metric_value" not in sql

    def test_ranking_shape_caps_with_the_last_param(self):
        # 行数上限必须是最后一个参数,信封靠 limit+1 判 has_more
        _sql, params = o2.rank_tasks(metric="milestones", mode="cut", top=7, shape="count")
        assert params[-1] == 7

    def test_ranking_shape_rejects_tie_and_ascending_modes(self):
        with pytest.raises(ValueError):
            o2.rank_tasks(metric="milestones", mode="keep_ties", shape="count")
        with pytest.raises(ValueError):
            o2.rank_tasks(metric="milestones", mode="cut", ascending=True, shape="count")

    def test_ranking_shape_needs_a_child_table(self):
        with pytest.raises(ValueError):
            o2.rank_tasks(metric="project_team_size", mode="cut", shape="count")

    def test_unknown_shape_rejected(self):
        with pytest.raises(ValueError):
            o2.rank_tasks(metric="milestones", mode="cut", shape="count_all")

    def test_cut_carries_both_window_counts(self):
        # 先按任务算度量再开窗:PARTITION BY count(x.id) 是聚合套窗口,PG 会直接报错
        sql, _params = o2.rank_tasks(metric="progress_rounds", mode="cut", top=3)
        assert "count(*) OVER () AS total_count" in sql
        assert "count(*) OVER (PARTITION BY metric_value) AS tie_count" in sql
        assert "WITH per_task AS" in sql and "), ranked AS (" in sql

    def test_ranking_shape_carries_ties(self):
        sql, _params = o2.rank_tasks(metric="milestones", mode="cut", top=3, shape="count")
        assert "count(*) OVER (PARTITION BY cnt) AS tie_count" in sql

    def test_cut_hoists_total_count_and_ties_to_the_top_level(self, monkeypatch):
        """两个自检数都提成顶层键:行内多一列会被当成分页信息读。"""

        def fake_envelope(**_kwargs):
            return {
                "ok": True,
                "columns": ["task_id", "task_name", "metric_value", "total_count", "tie_count"],
                "rows": [{"task_id": 4, "task_name": "x", "metric_value": 18, "total_count": 128, "tie_count": 12}],
                "row_count": 1,
            }

        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal._rank({"metric": "progress_rounds", "mode": "cut", "top": 3})
        assert (got["total_count"], got["tied_at_top"]) == (128, 12)
        assert got["columns"] == ["task_id", "task_name", "metric_value"]

    def test_ranking_hoists_ties_to_the_top_level(self, monkeypatch):
        def fake_envelope(**kwargs):
            return {
                "ok": True,
                "columns": ["id", "task_name", "cnt", "tie_count"],
                "rows": [{"id": 73, "task_name": "x", "cnt": 20, "tie_count": 2}],
                "row_count": 1,
                **kwargs.get("extra", {}),
            }

        monkeypatch.setenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", "task_attachment")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal._task_ranking({"metric": "attachments", "top": 3})
        assert got["tied_at_top"] == 2 and got["columns"] == ["id", "task_name", "cnt"]
        assert got["metric_label"] == "附件数"

    def test_team_size_metric_excludes_empty_owners(self):
        # 空负责人不是"1 人团队"而是没有团队可比:少了这一条,答案尾巴会混进假 1 人
        sql, _params = o2.rank_tasks(metric="project_team_size", mode="cut", top=3)
        assert "t.project_owner_name IS NOT NULL AND t.project_owner_name <> ''" in sql
        assert "coalesce(t.project_owner_name, '')" in sql  # 表达式自己仍要兜 NULL

    def test_ranking_metrics_map_to_the_tool_labels(self):
        # 标签照抄该工具的参考地图(progress 在这里叫「正式进展版本数」)
        assert _formal._RANKING_METRICS["progress"] == ("progress_rounds", "正式进展版本数")
        assert set(_formal._RANKING_METRICS) == {"attachments", "progress", "milestones", "submissions"}

    def test_ranking_unknown_metric_falls_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_task_ranking", metric="keyword_hits") is None

    def test_progress_range_unmigrated_arguments_fall_back(self, monkeypatch):
        """缺一端、要给分档/峰值、或换成填报时间轴,都交给演示路径(不连库即可判定)。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_progress_range", date_from="2026-08-01") is None
        assert _formal.dispatch("weekly_progress_range", by="month") is None
        assert _formal.dispatch("weekly_progress_range", peak=True) is None
        assert _formal.dispatch("weekly_progress_range", date_field="report_time") is None

    def test_progress_range_rejects_impossible_windows(self, monkeypatch):
        """不成立的窗口要报口径错,不能静默返回 0 行 —— 0 行的意思是「这段里没有进展」。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        for kwargs in (
            {"last_days": -3},
            {"date_from": "2026/07/01", "date_to": "2026-08-15"},
            {"date_from": "2026-08-15", "date_to": "2026-07-01"},
        ):
            got = _formal.dispatch("weekly_progress_range", **kwargs)
            assert got is not None and got["ok"] is False, kwargs
            assert got["error"]["code"] == "invalid_argument", kwargs


class TestProgressRangeTemplates:
    """时间轴出口的列集合、总数与"短窗口 0 行"的解释都要与参考实现同形。"""

    def test_columns_match_the_reference_query(self):
        sql, params = o2.progress_range(None, "2026-07-01", "2026-08-15", limit=200)
        assert "AS lag_days" in sql
        # 进展正文与填报人不在这个出口里:它答的是"哪些任务在窗口内报过"
        assert "latest_progress" not in sql and "next_work" not in sql and "reporter_id" not in sql
        assert params == ("2026-07-01", "2026-08-15", 200)

    def test_lag_days_is_report_date_minus_period_date(self):
        sql, _params = o2.progress_range(None, "2026-07-01", "2026-08-15")
        assert "AS lag_days" in sql
        assert sql.count("::date - ") == 1  # 减法只出现一次,别把窗口条件也改成日期相减

    def test_totals_are_a_separate_unpaged_query(self):
        sql, params = o2.progress_range_totals(None, "2026-01-01", "2026-08-15")
        assert "total_rows" in sql and "total_tasks" in sql
        assert "LIMIT" not in sql
        assert params == ("2026-01-01", "2026-08-15")

    def test_recency_query_reads_the_publish_gate(self):
        sql, params = o2.published_progress_recency()
        assert "latest_progress_date" in sql and "published_rows" in sql
        assert "is_published = 1" in sql
        assert params == ()


class TestFreshnessRouting:
    """新鲜度各分支的**路由**用假信封测:真连库的部分由端到端 harness 覆盖。"""

    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"x": 1, "total_count": 1, "never_reported_count": 1, "task_total": 1}
            return {"ok": True, "columns": ["x"], "rows": [row], "row_count": 1}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_recent_days_lists_without_a_status_gate(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._freshness({"recent_days": 7, "limit": 200})
        assert got is not None and got["ok"] is True
        assert "ORDER BY t.latest_progress_time DESC, t.id" in seen[0]["sql"]
        assert "t.status IN (0, 1)" not in seen[0]["sql"]

    def test_stale_listing_comes_with_its_two_self_checks(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._freshness({"stale_days": 30})
        assert len(seen) == 2  # 明细 + 自检总数
        assert got is not None and got["total_count"] == 1 and got["never_reported_count"] == 1
        assert "ORDER BY t.latest_progress_time IS NOT NULL" in seen[0]["sql"]

    def test_grouped_axis_returns_the_totals_row(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._freshness({"stale_days": 90, "by": "board"})
        assert len(seen) == 2
        assert got is not None and got["totals"]["total_count"] == 1  # 合计行原样挂在 totals 下
        assert "ORDER BY stale_pct DESC, bucket" in seen[0]["sql"]

    def test_within_days_returns_the_three_number_row(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._freshness({"within_days": 7})
        assert got is not None and seen[0]["limit"] == 1
        assert "AS days_behind" in seen[0]["sql"]

    def test_lag_bands_need_the_group_history_grant(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.delenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", raising=False)
        got = _formal.dispatch("weekly_freshness_distribution", lag_bands=True)
        assert got is not None and got["ok"] is False
        assert got["error"]["code"] == "table_not_granted"

    def test_by_without_days_is_a_guided_error(self, monkeypatch):
        self._capture(monkeypatch)
        got = _formal._freshness({"by": "board"})
        assert got is not None and got["ok"] is False
        assert got["error"]["code"] == "invalid_argument"
        assert "stale_days=90" in got["error"]["message"]
