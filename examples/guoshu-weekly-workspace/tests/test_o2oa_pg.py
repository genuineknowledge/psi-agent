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
        # 列集合照抄参考查询:填没填看 current_year_goal 是否为空,不再自造 goal_filled
        assert "y.year, y.current_year_goal, y.milestone_summary" in sql
        assert "AS task_id" in sql and "goal_filled" not in sql
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
        # 列名照抄参考查询:avg_rounds / total_submissions / tasks(462 / 150 = 3.08)
        assert "AS avg_rounds" in sql and "AS total_submissions" in sql and "AS tasks" in sql
        assert "count(DISTINCT s.task_id)" in sql and "count(*)" in sql
        assert "LIMIT" not in sql  # 单行答案,不受行数封顶影响

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
        # 用真正未接线的工具做断言:已接线的工具会去连库,不能拿来当反例
        # (weekly_schema 接线后这条断言从"已接线"挪到了下面几个仍未迁移的工具上)
        assert _formal.dispatch("weekly_task_detail", task="1") is None
        assert _formal.dispatch("weekly_aggregate", by="board") is None
        assert _formal.dispatch("weekly_task_lifecycle") is None
        assert _formal.dispatch("weekly_group_stats") is None

    def test_wired_schema_routes_to_the_formal_source(self, monkeypatch):
        """weekly_schema 已接线:路由判定不连库(靠 board= 的取值域判定)。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_schema", board="技术看板") is None  # 看板名交给演示侧解析
        assert _formal.dispatch("weekly_schema", board="nope") is None
        assert "weekly_schema" in _formal._HANDLERS

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


class TestMilestoneStatsTemplates:
    """里程碑统计的三条判据都在 SQL 里钉住(真库数字由端到端 harness 覆盖)。"""

    def test_status_is_a_two_value_code_never_a_text_match(self):
        """「已完成」只能写成 m.status = 1;文本匹配会把取值换个写法就悄悄答错。"""
        sql, _params = o2.milestone_stats("summary")
        assert "FILTER (WHERE m.status = 1)" in sql
        assert "FILTER (WHERE m.status = 0)" in sql
        assert "sum(m.status" not in sql.lower()  # PG 没有 sum(bool)

    def test_deleted_scope_has_no_task_gate(self):
        """deleted 问的是表本身被软删了多少行:套上任务闸门会少算,这里刻意不带。"""
        sql, params = o2.milestone_stats("deleted")
        assert "workflow_status" not in sql and "JOIN task" not in sql
        assert params == ()
        assert "total_rows" in sql and "active" in sql and "deleted" in sql

    def test_fully_deleted_uses_not_exists(self):
        """全删 = NOT EXISTS 未删里程碑,不是「有软删行」—— 23 条 vs 3 条差一个量级。"""
        sql, params = o2.milestone_stats("fully_deleted")
        assert "NOT EXISTS" in sql and "m2.is_deleted = 0" in sql
        assert "is_deleted = 1" in sql
        assert params == (200,)

    def test_per_task_keeps_zero_milestone_tasks(self):
        """LEFT JOIN 而不是 INNER JOIN:零里程碑任务正是覆盖率的分母。"""
        sql, params = o2.milestone_per_task_rows()
        assert "LEFT JOIN task_milestone m ON m.task_id = t.id" in sql
        assert "count(m.id)" in sql
        assert params == (200,)

    def test_per_task_year_filter_sits_on_the_join(self):
        """年度条件进 WHERE 会把「没有该年度里程碑」的任务整行删掉,分母永远算成 100%。"""
        sql, params = o2.milestone_per_task_rows(2026)
        where_part, _, join_part = sql.partition("WHERE")
        assert "m.year = %s" in join_part
        assert "m.year" not in where_part
        assert params == (2026, 200)

    def test_per_task_ties_lists_params_in_sql_order(self):
        """并列数查两次同样的子查询,参数要按 SQL 文本顺序给一遍给每一处。"""
        sql, params = o2.milestone_per_task_ties(2026, "国家任务")
        assert sql.count("%s") == 4
        assert params == (2026, "国家任务", 2026, "国家任务")

    def test_per_task_summary_divides_by_all_tasks(self):
        sql, _params = o2.milestone_per_task_summary()
        assert "count(DISTINCT t.id) AS tasks" in sql
        assert "tasks_without_milestone" in sql and "coverage_pct" in sql

    def test_by_dimension_dims_are_whitelisted(self):
        with pytest.raises(ValueError, match="不支持的维度"):
            o2.milestone_stats("by_dimension", by="task_name")

    def test_primary_category_walks_up_one_level(self):
        """任务分类只到二级,一级要再跳一层 parent_id;两个 JOIN 各带软删。"""
        sql, _params = o2.milestone_stats("by_dimension", by="primary_category")
        assert "JOIN task_category c  ON c.id = t.category_id AND c.is_deleted = 0" in sql
        assert "JOIN task_category pc ON pc.id = c.parent_id  AND pc.is_deleted = 0" in sql
        assert "finish_rate_pct DESC, bucket" in sql  # 问「最高」时首行即答案

    def test_project_group_and_group_name_are_different_axes(self):
        sql_pg, _ = o2.milestone_stats("by_dimension", by="project_group")
        sql_gn, _ = o2.milestone_stats("by_dimension", by="group_name")
        assert "t.project_group" in sql_pg and "m.group_name" not in sql_pg
        assert "m.group_name" in sql_gn and "t.project_group" not in sql_gn

    def test_min_total_is_a_having_threshold(self):
        sql, params = o2.milestone_stats("by_dimension", by="category", min_total=5)
        assert "HAVING count(*) >= %s" in sql
        assert params == (5, 200)

    def test_mismatch_kinds_are_mirror_images(self):
        open_sql, _ = o2.milestone_stats("mismatch", kind="task_done_milestones_open")
        done_sql, _ = o2.milestone_stats("mismatch", kind="milestones_done_task_open")
        assert "t.status = 2" in open_sql and "FILTER (WHERE m.status = 1) < count(*)" in open_sql
        assert "t.status = 1" in done_sql and "FILTER (WHERE m.status = 1) = count(*)" in done_sql

    def test_unknown_scope_and_kind_rejected(self):
        with pytest.raises(ValueError, match="未知 scope"):
            o2.milestone_stats("nope")
        with pytest.raises(ValueError, match="不支持的比对"):
            o2.milestone_stats("mismatch", kind="nope")


class TestMilestoneStatsRouting:
    """路由判定不连库:未迁移/非法参数必须回落,不静默缩小问题范围。"""

    def test_unmigrated_arguments_fall_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_milestone_stats", scope="nope") is None
        assert _formal.dispatch("weekly_milestone_stats", scope="by_dimension", by="nope") is None
        assert _formal.dispatch("weekly_milestone_stats", scope="mismatch", kind="nope") is None

    def test_per_task_merges_three_envelopes(self, monkeypatch):
        """per_task 在演示源是三个信封拼的:逐任务清单 + 总览(一行)+ 并列数。"""
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"tied_at_top": 7, "coverage_pct": 87.5, "task_id": 8, "task_name": "甲", "milestones": 6}
            return {"ok": True, "columns": ["task_id"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal._milestone_stats({"scope": "per_task", "top": 8})
        assert got is not None and len(seen) == 3
        assert isinstance(got["summary"], dict) and got["summary"]["coverage_pct"] == 87.5
        assert got["top_tie_count"] == 7
        assert "7 条任务并列" in got["caliber"]

    def test_deleted_scope_drops_the_gate_note(self, monkeypatch):
        """deleted 的口径要写明「不加任务闸门」,否则模型会拿它当"已发布任务的里程碑数"。"""
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            return {"ok": True, "columns": ["active"], "rows": [{}], "row_count": 1}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal._milestone_stats({"scope": "deleted"})
        assert got is not None
        assert "不加任务闸门" in seen[0]["caliber"] and "fully_deleted" in seen[0]["caliber"]
        assert seen[0]["cap_last_param"] is False


class TestYearGoalStatsTemplates:
    """年度目标统计的两条判据:S 缺口类永远按正式任务算;coverage 必须用 EXISTS。"""

    def test_coverage_uses_exists_not_join(self):
        """没有目标行的任务正是要数的缺口,INNER JOIN 会把它们整行丢掉。"""
        sql, params = o2.year_goal_stats("coverage", year=2026)
        assert "EXISTS" in sql and "task_year_goal" in sql
        assert "JOIN task_year_goal" not in sql
        assert "count(*) FILTER (WHERE NOT has_goal) AS missing_goal" in sql
        assert params == (2026,)

    def test_whole_table_only_applies_to_counting_scopes(self):
        """by_year 放开闸门;缺口类口径即使传了 whole_table 也仍带闸门。"""
        sql_open, _ = o2.year_goal_stats("by_year", whole_table=True)
        sql_gated, _ = o2.year_goal_stats("by_year")
        assert "1 = 1" in sql_open and "workflow_status" not in sql_open
        assert "workflow_status = 'published'" in sql_gated
        for scope in ("coverage", "missing", "missing_by_group"):
            sql, _ = o2.year_goal_stats(scope, year=2026, whole_table=True)
            assert "workflow_status = 'published'" in sql, scope

    def test_multi_year_having_repeats_the_expression(self):
        """PG 只在 ORDER BY / GROUP BY 允许输出列别名,HAVING 里必须把表达式写全。"""
        sql, _params = o2.year_goal_stats("multi_year", year=2026, year_to=2025)
        assert "HAVING goal_year_1" not in sql and "HAVING goal_year_2" not in sql
        assert sql.count("max(CASE WHEN g.year = 2026 THEN g.current_year_goal END)") == 2
        assert sql.count("max(CASE WHEN g.year = 2025 THEN g.current_year_goal END)") == 2

    def test_span_uses_string_agg_and_takes_no_year(self):
        """year 是整数,string_agg 不转文本会报错;span 也不收 year(见模板 docstring)。"""
        sql, params = o2.year_goal_span_rows(3)
        assert "string_agg(g.year::text, ',' ORDER BY g.year)" in sql
        assert params == (3, 200)
        assert "g.year = " not in sql

    def test_board_filter_goes_through_the_board_code(self):
        sql, params = o2.year_goal_stats("by_year", board_code="tech")
        assert "task_board WHERE code = %s" in sql
        assert params == ("tech", 200)

    def test_invalid_scope_and_missing_year(self):
        with pytest.raises(ValueError, match="未知 scope"):
            o2.year_goal_stats("nope")
        # coverage / missing / missing_by_group 都必须显式给 year(rule 5)
        with pytest.raises(ValueError, match="year"):
            o2.year_goal_stats("coverage")
        # span 要走 year_goal_span_* 两条模板,落到这里说明路由写错了
        with pytest.raises(ValueError, match="请用 year_goal_span_avg"):
            o2.year_goal_stats("span")


class TestYearGoalStatsRouting:
    """路由与回落不连库:`envelope` 一律换成假信封,避免测试去连真 PG。"""

    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"avg_years": 2.45, "total_count": 11, "tasks": 117}
            return {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_span_with_year_falls_back(self, monkeypatch):
        """演示实现的 span 把 year 静默丢掉:正式源按年度过滤会返回范围更小的答案。"""
        self._capture(monkeypatch)
        assert _formal.dispatch("weekly_year_goal_stats", scope="span", year=2026) is None
        assert _formal.dispatch("weekly_year_goal_stats", scope="span", min_years=3) is not None

    def test_board_name_falls_back_to_the_demo_resolver(self, monkeypatch):
        """board= 接受看板名字(演示侧 resolve_board);正式源只认 tech/group 两个码。"""
        self._capture(monkeypatch)
        assert _formal.dispatch("weekly_year_goal_stats", scope="by_year", board="集团看板") is None
        assert _formal.dispatch("weekly_year_goal_stats", scope="by_year", board="nope") is None
        assert _formal.dispatch("weekly_year_goal_stats", scope="by_year", board="tech") is not None

    def test_missing_year_is_an_argument_error(self, monkeypatch):
        self._capture(monkeypatch)
        got = _formal.dispatch("weekly_year_goal_stats", scope="coverage")
        assert got is not None and got["ok"] is False
        assert got["error"]["code"] == "invalid_argument"
        got = _formal.dispatch("weekly_year_goal_stats", scope="multi_year", year=2026, year_to=2026)
        assert got is not None and got["ok"] is False
        assert "两个不同的年度" in got["error"]["message"]

    def test_span_merges_avg_and_rows(self, monkeypatch):
        """span 的均值与清单是两条查询,均值挂顶层(分母只含设过目标的任务)。"""
        seen = self._capture(monkeypatch)
        got = _formal._year_goal_stats({"scope": "span", "min_years": 3})
        assert got is not None and len(seen) == 2
        assert got["avg_years_per_task"] == 2.45 and got["min_years"] == 3
        assert "分母只含" in got["caliber"]

    def test_missing_hoists_total_count(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._year_goal_stats({"scope": "missing", "year": 2026})
        assert got is not None and len(seen) == 2
        assert got["total_count"] == 11
        assert "t.status IN (0, 1)" not in seen[0]["sql"]
        got = _formal._year_goal_stats({"scope": "missing", "year": 2026, "in_progress_only": True})
        assert got is not None and "t.status IN (0, 1)" in seen[2]["sql"]

    def test_multi_year_hoists_both_count_and_years(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._year_goal_stats({"scope": "multi_year", "year": 2026, "year_to": 2025})
        assert got is not None and len(seen) == 2
        assert got["tasks_in_both_years"] == 117 and got["years"] == [2026, 2025]

    def test_gap_scopes_keep_the_gate_even_with_include_informal(self, monkeypatch):
        """缺口类口径量的是"正式任务里有多少没设目标",放宽分母会让缺口失去意义。"""
        seen = self._capture(monkeypatch)
        _formal._year_goal_stats({"scope": "by_year", "include_informal": True})
        assert "1 = 1" in seen[0]["sql"]
        _formal._year_goal_stats({"scope": "coverage", "year": 2026, "include_informal": True})
        assert "workflow_status = 'published'" in seen[1]["sql"]
        assert "include_informal 对它无效" in seen[1]["caliber"]


class TestSchemaTemplates:
    """字段字典只列契约内 12 张表、剔除禁止外泄列;分类树带软删与看板收窄。"""

    def test_columns_query_excludes_blocked_fields_and_stray_tables(self):
        sql, params = o2.schema_columns()
        assert "information_schema.columns" in sql
        assert "pg_description" in sql  # PG 没有 COLUMN_COMMENT,注释要经 pg_class 的 oid 取
        assert "objsubid = c.ordinal_position" in sql
        assert "c.table_name = ANY(%s)" in sql
        schema, tables, blocked = params
        assert schema == "public"
        assert list(tables) == list(o2.CHATBI_TABLES) and len(tables) == 12
        assert list(blocked) == ["storage_path", "payload"]

    def test_categories_carry_soft_delete_and_optional_board(self):
        sql, params = o2.schema_categories()
        assert "c.is_deleted = 0" in sql and params == (200,)
        sql, params = o2.schema_categories("group")
        assert "code = %s" in sql and params == ("group", 200)
        with pytest.raises(ValueError, match=r"board\.code"):
            o2.schema_categories("nope")

    def test_boards_query(self):
        sql, params = o2.schema_boards()
        assert "is_deleted = 0" in sql and params == ()


class TestFreshnessSnapshotTemplates:
    """数据快照日期:两个口径(含未发布 vs 只看正式)+ 导入批次。"""

    def test_board_rows_keep_boards_without_tasks(self):
        sql, params = o2.freshness_board_latest("2026-08-15")
        assert "LEFT JOIN task" in sql
        assert "days_behind" in sql and params == ("2026-08-15",)

    def test_days_behind_is_two_dates_subtracted(self):
        """天数必须按日期相减:date - timestamp 会得到 interval,date_part 会少一天。"""
        sql, _params = o2.freshness_board_latest("2026-08-15")
        assert "::date - max(" in sql
        assert "date_part" not in sql and "extract(" not in sql

    def test_published_split_uses_board_code_not_id(self):
        """分流条件是 b.code,不是演示实现里写死的 b.id = 2(正式库 id 不保证一致)。"""
        sql, params = o2.freshness_published_per_board("2026-08-15", group_history_granted=True)
        assert "b.code = 'group'" in sql and "b.id = 2" not in sql
        assert "task_group_progress_history" in sql and "task_progress p" in sql
        assert params == ("2026-08-15",)
        with pytest.raises(PermissionError, match="未在本次只读授权范围内"):
            o2.freshness_published_per_board("2026-08-15", group_history_granted=False)

    def test_import_batches_only_count_finished(self):
        sql, params = o2.freshness_import_batches()
        assert "status = 1 THEN" in sql and "status <> 1 THEN" in sql
        assert params == ()


class TestFreshnessSnapshotRouting:
    """未授权可选表时的降级:键要保留,值给 null(删掉键就分不清两种"查不到")。"""

    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"newest": "2026-08-09 00:00:00", "days_behind": 6, "newest_finished_batch": "2026-07-31"}
            return {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_all_four_parts_present_when_granted(self, monkeypatch):
        self._capture(monkeypatch)
        monkeypatch.setenv("GUOSHU_AS_OF", "2026-08-15")
        monkeypatch.setenv(
            "TASK_BOARD_GRANTED_OPTIONAL_TABLES",
            "task_group_progress_history,task_progress_import",
        )
        got = _formal.dispatch("weekly_freshness")
        assert got is not None and len(got) >= 4
        assert got["as_of"] == "2026-08-15"
        assert isinstance(got["overall"], dict) and got["overall"]["days_behind"] == 6
        assert got["published_progress"] and isinstance(got["published_progress"], list)
        assert isinstance(got["tech_import"], dict)

    def test_ungranted_optional_tables_degrade_with_keys_kept(self, monkeypatch):
        self._capture(monkeypatch)
        monkeypatch.delenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", raising=False)
        got = _formal.dispatch("weekly_freshness")
        assert got is not None
        assert got["published_progress"] == []
        assert set(got["tech_import"]) == {
            "newest_finished_batch",
            "newest_batch_any_status",
            "newest_unfinished_batch",
        }
        assert all(value is None for value in got["tech_import"].values())
        assert "task_progress_import 未在本次只读授权范围内" in got["caliber"]
        assert "task_group_progress_history 未在本次只读授权范围内" in got["caliber"]


class TestFieldCompletenessTemplates:
    """完整度的三条判据:空串算未填、明细表用 LEFT JOIN、另给裸表口径。"""

    def test_empty_string_counts_as_missing(self):
        sql, params = o2.completeness_counts("project_owner_id")
        assert "t.project_owner_id IS NOT NULL AND t.project_owner_id <> ''" in sql
        assert "count(*) FILTER (WHERE NOT (" in sql
        assert params == ()

    def test_detail_table_fields_keep_tasks_without_a_row(self):
        """R-08:没有明细行的任务也算缺项,INNER JOIN 会把它们丢掉、填写率凭空变高。"""
        sql, _params = o2.completeness_counts("progress_effect")
        assert "LEFT JOIN task_group_detail d ON d.task_id = t.id" in sql
        sql, _params = o2.completeness_missing_rows("progress_effect")
        assert "LEFT JOIN task_group_detail d ON d.task_id = t.id" in sql
        assert "t.id, t.task_name, t.owner_user_id" in sql

    def test_raw_table_view_is_a_second_caliber(self):
        sql, params = o2.completeness_raw_table("implementation_measure")
        assert "FROM task_group_detail d" in sql
        assert "workflow_status" not in sql  # 裸表口径刻意不带任务闸门
        assert "raw_row_count" in sql and "raw_distinct_values" in sql
        assert params == ()
        with pytest.raises(ValueError, match="没有裸表口径"):
            o2.completeness_raw_table("overall_goal")

    def test_quality_query_returns_both_discrimination_numbers(self):
        """只报填写率不够:"同一句话复制 55 遍"的字段填写率可以是 100%。"""
        sql, _params = o2.completeness_quality("progress_effect")
        assert "count(*) AS distinct_values" in sql and "coalesce(max(g.n), 0) AS top_value_rows" in sql
        assert "GROUP BY val" in sql

    def test_unknown_field_rejected(self):
        with pytest.raises(ValueError, match="不支持的字段"):
            o2.completeness_counts("salary")

    def test_all_whitelisted_fields_have_a_table(self):
        assert set(o2.COMPLETENESS_FIELDS) == {
            "overall_goal", "annual_goals", "project_owner_name", "lead_owner_name",
            "project_group", "owner_user_id", "project_owner_id", "lead_owner_id",
            "target_result", "implementation_measure", "progress_effect", "completion_time",
        }
        assert {table for table, _label in o2.COMPLETENESS_FIELDS.values()} == {
            "task",
            "task_group_detail",
        }


class TestProgressHistoryTemplates:
    """单任务进展各期:相邻两期并排 + 间隔均值由服务端算 + 同名系列显式回报。"""

    def test_adjacent_versions_are_stacked_by_the_server(self):
        sql, params = o2.progress_history_rows(7)
        assert "lag(p.latest_progress) OVER (ORDER BY p.version_no) AS prev_progress" in sql
        assert "AS gap_days" in sql
        assert "ORDER BY p.version_no DESC, p.id DESC" in sql
        assert params == (7, 200)

    def test_published_only_switch(self):
        sql, _params = o2.progress_history_rows(7, published_only=True)
        assert "p.is_published = 1" in sql
        sql, _params = o2.progress_history_rows(7, published_only=False)
        assert "p.is_published = 1" not in sql

    def test_gap_summary_rounds_and_skips_the_first_period(self):
        sql, params = o2.progress_history_gap_summary(7)
        assert "round(avg(gap_days)::numeric, 1)" in sql
        assert "WHERE gap_days IS NOT NULL" in sql  # 首期没有上一期,不进分母
        assert params == (7,)

    def test_name_series_passes_the_regex_as_a_parameter(self):
        """正则含反斜杠与全角括号:拼进 SQL 文本要处理两层转义,还撞 psycopg 的 % 解析。"""
        sql, params = o2.name_series(3)
        assert "regexp_replace(t2.task_name, %s, '')" in sql
        assert params[0] == o2.SERIES_SUFFIX and params[1:] == (3, 3)
        assert "\\d" not in sql  # 正则不在 SQL 文本里
        assert "%s" not in sql.replace("%s", "", 2) or sql.count("%s") == 3


class TestFieldCompletenessRouting:
    """路由与打码:未支持的字段回落;支持字段清单由服务端给。"""

    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"total": 128, "filled": 119, "missing": 9, "filled_pct": 93.0,
                   "distinct_values": 3, "top_value_rows": 40,
                   "raw_row_count": 55, "raw_filled": 55, "raw_distinct_values": 1,
                   "total_count": 9}
            return {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_empty_field_lists_the_supported_ones(self, monkeypatch):
        self._capture(monkeypatch)
        got = _formal.dispatch("weekly_field_completeness")
        assert got is not None and got["ok"] is True
        assert set(got["supported_fields"]) == set(o2.COMPLETENESS_FIELDS)

    def test_unknown_field_falls_back(self, monkeypatch):
        self._capture(monkeypatch)
        assert _formal.dispatch("weekly_field_completeness", field="salary") is None

    def test_task_table_field_has_no_raw_caliber(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._field_completeness({"field": "project_owner_id"})
        assert got is not None and len(seen) == 2  # 计数 + 区分度,没有裸表那一次
        assert "raw_row_count" not in got
        assert "裸表口径" not in got["caliber"]

    def test_detail_field_adds_the_raw_caliber_and_the_quality_signal(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._field_completeness({"field": "progress_effect"})
        assert got is not None and len(seen) == 3  # 计数 + 区分度 + 裸表
        assert got["raw_row_count"] == 55 and got["raw_distinct_values"] == 1
        # 裸表口径里不同值只有 1 个 -> 必须报「同一份内容、不具备区分度」
        assert "只有 1 个不同的值" in got["caliber"]
        assert "不构成对项目或人员的绩效判断" in got["caliber"]

    def test_list_missing_hoists_total_count(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._field_completeness({"field": "project_owner_id", "list_missing": True})
        assert got is not None and len(seen) == 2
        assert got["total_count"] == 9 and got["field"] == "project_owner_id"


class TestProgressHistoryRouting:
    def test_missing_task_falls_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_progress_history", task="") is None

    def test_number_task_never_touches_the_resolver(self, monkeypatch):
        """task= 是数字时直接当 id 用,不该为了解析名字再查一次库。"""
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            # 假信封要像真行一样带 name_series 查询的那两列(调用方要拿 id / task_name 组句子)
            row = {"avg_gap_days": 30.3, "gap_count": 5, "task_id": 7, "task_name": "甲",
                   "id": 8, "review_comment": None}
            return {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal._progress_history({"task": "7", "limit": 3})
        assert got is not None
        assert got["gap_summary"]["avg_gap_days"] == 30.3
        assert seen[0]["params"][0] == 7 and seen[0]["params"][-1] == 3
        # 三次查询:明细 + 间隔均值 + 同名系列
        assert len(seen) == 3
        assert "FROM task_progress p" in seen[0]["sql"]

    def test_sensitive_comment_is_masked_not_dropped(self, monkeypatch):
        """敏感字段按权限打码而不是删列 —— 藏列会让调用方分不清「没有意见」与「没权限看」。"""
        def fake_envelope(**kwargs):
            row = {"review_comment": "原文", "avg_gap_days": 1.0, "gap_count": 1, "task_id": 7,
                   "id": 8, "task_name": "甲"}
            return {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        masked = _formal._progress_history({"task": "7"})
        assert masked is not None and masked["rows"][0]["review_comment"] == "[按权限不展示]"
        assert "review_comment" in masked["rows"][0]  # 键还在,只是值打码
        raw = _formal._progress_history({"task": "7", "can_read_sensitive": True})
        assert raw is not None and raw["rows"][0]["review_comment"] == "原文"
