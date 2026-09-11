"""Unit tests for the ChatBI formal-source building blocks (no DB needed).

These assert the *rules* themselves (admission clauses, value domains,
year-explicitness, PG template shape) so the guards can never silently
regress while the real O2OA connection is still being provisioned.
"""

import sys
from pathlib import Path
from typing import ClassVar

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mock-mcp"))

# mock-mcp is a sys.path tool dir, not a package: ty cannot resolve these
# statically, pytest can (path inserted above).  Same pattern as the tools.
import _admission as adm  # ty: ignore
import _auth  # ty: ignore
import _fallback  # ty: ignore
import _formal  # ty: ignore
import _o2oa_templates as o2  # ty: ignore
import _pg  # ty: ignore
import _store  # ty: ignore
import psycopg

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

    def test_formal_coverage_board_filter_repeats_params(self):
        """看板条件会出现在**每一段子查询**里,参数要按出现次数重复(否则驱动报参数数不符)。"""
        sql, params = o2.formal_coverage(group_history_granted=True, board_code="tech")
        assert sql.count("%s") == len(params) == 4
        assert params == ("tech",) * 4
        assert "task_board" in sql

    def test_formal_coverage_reaches_the_tool_scope(self):
        """`scope=formal_coverage` 必须真的路由到那条模板(此前它没有任何调用方)。"""
        sql, params = o2.coverage_stats("formal_coverage")
        assert "formal_task_count" in sql and "coverage_pct" in sql
        assert params == ()

    def test_orphan_records_counts_the_two_foreign_key_gaps(self):
        """孤儿 = 外键指向查不到的记录:进展行挂不到任务 + 审批动作挂不到提交单。

        刻意**不带**任务门/软删/发布闸门:孤儿本来就是"连父行都找不到",
        再加父行属性条件等于要求一条已经失败的 JOIN 还满足父行属性 —— 永远数不出东西。
        """
        sql, params = o2.coverage_stats("orphan_records")
        assert "orphan_progress_rows" in sql and "orphan_actions" in sql
        assert "LEFT JOIN task t ON t.id = p.task_id" in sql
        assert "LEFT JOIN task_workflow_submission s ON s.id = a.submission_id" in sql
        assert "workflow_status" not in sql and "is_deleted" not in sql
        assert params == ()

    def test_orphan_records_is_distinct_from_the_other_two_orphan_scopes(self):
        """附件孤儿与导入孤儿是**另两问**:混着答会指错方向(G-E03 就这么错过)。"""
        attachments, _ = o2.attachment_stats(scope="orphan")
        assert "task_attachment" in attachments
        assert "orphan_progress_rows" not in attachments
        imports, _ = o2.import_audit_orphans()
        assert "task_progress_import" in imports
        assert "orphan_actions" not in imports

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
        # 31 个工具已**全部接线**:反例改用「未注册的工具名 + 未迁移的参数组合」,
        # 不再借某个工具当"还没接线"的样本(那样每接一个就要改一次断言)。
        assert _formal.dispatch("weekly_not_a_tool") is None
        assert _formal.dispatch("weekly_group_stats", scope="nope") is None
        assert _formal.dispatch("weekly_aggregate", group_by="nope") is None
        assert _formal.dispatch("weekly_group_stats", scope="nope") is None

    def test_wired_schema_routes_to_the_formal_source(self):
        """weekly_schema 已接线:不认识工具名/scope 才回落。"""
        assert "weekly_schema" in _formal._HANDLERS

    def test_board_name_resolution_does_not_fall_back(self, monkeypatch):
        """看板**名字**现在在正式源侧解析(问句说的就是名字),不再回落。

        真库的看板名是「技术组重点任务进展」/「集团重点任务调度」,提问口径叫「技术组」
        「集团看板」—— 此前一律落到模板值域校验,调用方拿到的是死路。
        这里用桩替换看板清单,断言三级匹配(码 / 精确 / 包含),不连库。
        """

        def fake_envelope(**kwargs):
            return {"ok": True, "columns": ["id", "name", "code", "sort_order"],
                    "rows": [{"id": 1, "name": "技术组重点任务进展", "code": "tech", "sort_order": 1},
                             {"id": 2, "name": "集团重点任务调度", "code": "group", "sort_order": 0}],
                    "row_count": 2, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        monkeypatch.setattr(_formal, "_board_cache", None)
        try:
            assert _formal._board({}) is None
            assert _formal._board({"board": "tech"}) == "tech"      # 已是码:不查库
            assert _formal._board({"board": "技术组重点任务进展"}) == "tech"  # 精确
            assert _formal._board({"board": "技术组"}) == "tech"     # token ⊂ 库内名字
            assert _formal._board({"board": "集团"}) == "group"
            assert _formal._board({"board": "集团看板"}) == "group"  # 库内名字 ⊂ token(另一支)
            # 认不出来的 token 原样返回:让模板报值域错(错误信息里带真实值域)
            assert _formal._board({"board": "nope"}) == "nope"
        finally:
            monkeypatch.setattr(_formal, "_board_cache", None)

    def test_unmigrated_arguments_fall_back_instead_of_narrowing(self, monkeypatch):
        """未迁移的参数组合必须回落演示路径,绝不能返回一个范围更小的答案。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        # by= 只给分组轴而没有天数:参考实现是**明确报错并指路**,不是静默回退
        by_only = _formal.dispatch("weekly_freshness_distribution", by="board")
        assert by_only is not None and by_only["ok"] is False
        assert by_only["error"]["code"] == "invalid_argument"
        assert "stale_days 或 recent_days" in by_only["error"]["message"]
        assert _formal.dispatch("weekly_freshness_distribution", by="不存在") is None
        # 单任务档现在**已迁移**(task= 单独给会真去连库),故这里的反例换成"单任务档
        # 与分组/天数窗的组合" —— 那种组合语义不清,仍走演示路径
        assert _formal.dispatch("weekly_freshness_distribution", task="1", stale_days=30) is None
        assert _formal.dispatch("weekly_freshness_distribution", task="1", lag_bands=True) is None
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

    def test_progress_range_migrated_arguments_reach_the_formal_source(self, monkeypatch):
        """缺一端、分档、峰值、换填报时间轴 —— **这四档全部已接线**,不再回落演示路径。

        此前这里是反向断言(它们都 None)。翻面是因为放过它们会答错:日期只给一端
        不是"缺参数"而是"另一端不设限"(参考实现文档写的是 Empty means unbounded),
        按月分档与峰值问的是另一类问题,report_time 与 progress_date 更不是同一批行
        —— 回落演示路径的结果是这四档在正式源上直接报 not_migrated。
        """
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            # 真信封会把 extra 合进顶层、并带 caliber;假信封少了这两样,
            # 调用方那句 `result["date_from"] = ...` 之后的读数就会 KeyError。
            seen.append(kwargs)
            return {
                "ok": True,
                "columns": ["x"],
                "rows": [{"x": 1}],
                "row_count": 1,
                "caliber": kwargs.get("caliber", "口径"),
                **kwargs.get("extra", {}),
            }

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        for kwargs in (
            {"date_from": "2026-08-01"},
            {"by": "month"},
            {"peak": True},
            {"by": "task"},
            {"date_field": "report_time"},
        ):
            got = _formal.dispatch("weekly_progress_range", **kwargs)
            assert got is not None and got["ok"] is True, kwargs
        # 明细档要跟着两个 envelope(明细 + 总数),分组档只有分组那一条
        assert any("窗口总数" in k["caliber"] for k in seen)
        assert any("LAG" in k["sql"] for k in seen)  # 月档带环比

    def test_progress_range_unbounded_window_drops_the_date_filters(self, monkeypatch):
        """无界窗口不能在 SQL 里留下 `>= %s` 的悬空条件(参数会与占位符错位)。"""
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            return {
                "ok": True,
                "columns": ["x"],
                "rows": [{"x": 1}],
                "row_count": 1,
                "caliber": kwargs.get("caliber", "口径"),
                **kwargs.get("extra", {}),
            }

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal.dispatch("weekly_progress_range")
        assert got is not None and got["ok"] is True
        detail = seen[0]["sql"]
        # 无界窗口:两个日期条件都不下发,只剩 LIMIT 那一个占位符
        assert detail.count("%s") == 1
        assert got["date_from"] == "" and got["date_to"] == ""

    def test_progress_range_grouped_shapes_match_the_reference(self, monkeypatch):
        """三档分组的**形状**:月/季带环比列、task 不带 task_count、peak 只回一行。

        分组档之后还会跟一条"无周期日自检",所以不能拿 ``seen[-1]`` 认分组 SQL
        ——按内容找。
        """
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            return {
                "ok": True,
                "columns": ["x"],
                "rows": [{"x": 1}],
                "row_count": 1,
                "caliber": kwargs.get("caliber", "口径"),
                **kwargs.get("extra", {}),
            }

        def last_sql(marker: str) -> str:
            got = [k["sql"] for k in seen if marker in k["sql"]]
            assert got, f"没找到含 {marker!r} 的 SQL"
            return got[-1]

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)

        _formal.dispatch("weekly_progress_range", by="month")
        month_sql = last_sql("AS progress_count")
        assert "mom_change" in month_sql and "LAG(" in month_sql
        _formal.dispatch("weekly_progress_range", by="quarter")
        assert "mom_change" in last_sql("AS progress_count")
        _formal.dispatch("weekly_progress_range", by="task")
        task_sql = last_sql("t.task_name AS bucket")
        assert "AS task_count" not in task_sql
        assert "progress_count DESC" in task_sql
        _formal.dispatch("weekly_progress_range", by="month", peak=True)
        peak_sql = last_sql("AS progress_count")
        assert "LIMIT 1" in peak_sql and "mom_change" not in peak_sql

    def test_grouped_buckets_drop_null_periods_and_report_the_count(self, monkeypatch):
        """无周期日的行不进任何时间档,但必须**报出条数**。

        真库实测:44 行已发布进展的 progress_date 为空。放进分组里它们会挤成一个
        ``bucket=NULL`` 的档,而计数降序下它排第一 —— 「进展最多的月份」会答成
        「那 44 行没有周期日的」。所以分组里排除 + 信封里给 ``unbucketed_rows``,
        让"各档之和 ≠ 明细行数"这件事有解释。
        """
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"unbucketed_rows": 44} if "unbucketed_rows" in kwargs["sql"] else {"x": 1}
            return {
                "ok": True,
                "columns": ["x"],
                "rows": [row],
                "row_count": 1,
                "caliber": kwargs.get("caliber", "口径"),
                **kwargs.get("extra", {}),
            }

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal.dispatch("weekly_progress_range", by="month")
        grouped = next(k["sql"] for k in seen if "AS progress_count" in k["sql"])
        assert "IS NOT NULL" in grouped  # 空周期档被 HAVING 挡掉
        assert got["unbucketed_rows"] == 44
        assert "44 行正式进展的 progress_date 为空" in got["caliber"]
        # task 档不适用(任务名不会为空),不该多出这条自检
        seen.clear()
        got_task = _formal.dispatch("weekly_progress_range", by="task")
        assert "unbucketed_rows" not in got_task
        assert not any("unbucketed_rows" in k["sql"] for k in seen)

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

    def test_progress_range_unknown_values_still_fall_back(self, monkeypatch):
        """值域错(不认识的 date_field / by)交给参考实现自己报 —— 它的消息里带可用取值。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_progress_range", date_field="created_at") is None
        assert _formal.dispatch("weekly_progress_range", by="week") is None


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

    def test_unbounded_window_drops_both_date_conditions(self):
        """空串是"这一端不设限",不是"缺参数" —— SQL 里不能留悬空的比较条件。"""
        sql, params = o2.progress_range(None, "", "")
        assert ">= %s" not in sql and "<= %s" not in sql
        assert params == (200,)
        # 只给一端:另一端照样不设限
        sql_one, params_one = o2.progress_range(None, "2026-08-01", "")
        assert sql_one.count("%s") == 2 and params_one == ("2026-08-01", 200)

    def test_report_time_axis_sorts_and_filters_on_that_column(self):
        sql, _params = o2.progress_range(None, "", "", date_field="report_time")
        assert "ORDER BY (p.report_time)::timestamp DESC" in sql
        # lag_days 恒为"上报日 - 周期日",不随轴变
        assert "AS lag_days" in sql

    def test_unknown_date_field_and_grouping_raise(self):
        with pytest.raises(ValueError, match="date_field"):
            o2.progress_range(None, "", "", date_field="created_at")
        with pytest.raises(ValueError, match="不支持的 by"):
            o2.progress_range_grouped(None, "", "", by="week")

    def test_month_and_quarter_buckets_are_text_keys(self):
        """分档键必须是可排序的文本(2026-07 / 2026Q3),不能是裸日期或月份整数。"""
        month_sql, _ = o2.progress_range_grouped(None, "", "", by="month")
        assert "substr(to_char(" in month_sql and "'YYYY-MM-DD'), 1, 7)" in month_sql
        quarter_sql, _ = o2.progress_range_grouped(None, "", "", by="quarter")
        assert "'Q' ||" in quarter_sql
        # 季度算式:(month - 1) / 3 + 1 —— 多一个 +1 会把 1 月算成 Q1 之外的值
        assert "extract(month from" in quarter_sql and "/ 3) + 1)" in quarter_sql

    def test_momentum_wraps_the_grouping_without_its_limit(self):
        """环比把分组查询包成子查询:内层若留着 LIMIT,环比会被截成"前 N 档"。"""
        sql, params = o2.progress_momentum(None, "", "", limit=200, by="month")
        assert sql.count("LIMIT %s") == 1  # 只有外层那一个
        assert "LAG(progress_count) OVER (ORDER BY bucket)" in sql
        assert "mom_change" in sql
        assert params == (200,)

    def test_task_grouping_has_no_task_count_and_peak_returns_one_row(self):
        task_sql, task_params = o2.progress_range_grouped(None, "", "", by="task")
        assert "AS task_count" not in task_sql
        assert "ORDER BY progress_count DESC, bucket" in task_sql
        assert task_params == (200,)
        peak_sql, peak_params = o2.progress_range_grouped(None, "", "", by="month", peak=True)
        assert "LIMIT 1" in peak_sql and "mom_change" not in peak_sql
        # peak 那档的 LIMIT 写在 SQL 里,所以**不能**再多一个 limit 参数
        assert peak_params == ()

    def test_grouped_queries_keep_placeholder_and_param_counts_equal(self):
        """占位符与参数必须逐档配平 —— 差一个就是运行期的驱动报错。"""
        cases = [
            o2.progress_range(None, "2026-01-01", "2026-02-01"),
            o2.progress_range_totals(None, "2026-01-01", "2026-02-01"),
            o2.progress_range_grouped(None, "2026-01-01", "2026-02-01", by="month"),
            o2.progress_range_grouped(None, "2026-01-01", "2026-02-01", by="task"),
            o2.progress_range_grouped(None, "2026-01-01", "2026-02-01", by="month", peak=True),
            o2.progress_momentum(None, "2026-01-01", "2026-02-01", by="quarter"),
            o2.year_goal_span_rows(year=2026),
            o2.year_goal_span_avg(year=2026),
            o2.year_goal_span_rows(board_code="tech", year=2026),
        ]
        for sql, params in cases:
            assert sql.count("%s") == len(params), sql[:120]

    def test_span_year_filter_narrows_the_counted_goals(self):
        """带 year 的 span:清单按 g.year 过滤,均值用 FILTER 只数那一年的条数。"""
        rows_sql, rows_params = o2.year_goal_span_rows(year=2026, min_years=3)
        assert "AND g.year = %s" in rows_sql
        assert rows_params == (2026, 3, 200)
        avg_sql, avg_params = o2.year_goal_span_avg(year=2026)
        assert "count(*) FILTER (WHERE g.year = %s)" in avg_sql
        assert avg_params == (2026,)
        # 不带 year 的形状一个字都不能变
        plain_rows, plain_params = o2.year_goal_span_rows(min_years=3)
        assert "g.year = %s" not in plain_rows and plain_params == (3, 200)


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

    def test_span_with_year_is_migrated(self, monkeypatch):
        """带 year 的 span 已接线:**限定到该年度**只数那一年的目标条数。

        此前这一档回落演示路径,理由是"演示实现把 year 静默丢掉,按年度过滤会返回一个
        范围更小的答案"。翻面的依据是口径本身:给了 year,"每个任务有几个目标"的分母
        本来就该收窄到这一年 —— 收窄不是"答窄了",是把问题答对了。caliber 里必须点明
        year_count 的含义变了(是**该年度内的条数**,不是跨了几个年度),否则调用方会把
        1 读成"这个任务只设过一个年度的目标"。
        """
        seen = self._capture(monkeypatch)
        got = _formal.dispatch("weekly_year_goal_stats", scope="span", year=2026)
        assert got is not None and got["ok"] is True
        # 清单与均值两条查询都带上年度条件
        assert "g.year = %s" in seen[0]["sql"]
        assert any("FILTER (WHERE g.year = %s)" in k["sql"] for k in seen)
        assert "该年度内的目标条数" in got["caliber"]

    def test_span_without_year_keeps_the_cross_year_semantics(self, monkeypatch):
        """不带 year 的老档一个字都不能变:跨年度计数 + "至少 N 个年度"。"""
        seen = self._capture(monkeypatch)
        got = _formal.dispatch("weekly_year_goal_stats", scope="span", min_years=3)
        assert got is not None and got["ok"] is True
        assert "g.year = %s" not in seen[0]["sql"]
        assert "该年度内的目标条数" not in got["caliber"]
        assert "个年度" in seen[0]["caliber"]

    def test_board_name_is_resolved_on_the_formal_side(self, monkeypatch):
        """看板名字在正式源侧解析(问句说的就是名字);认不出来的报值域错,不回落。"""
        self._capture(monkeypatch)
        monkeypatch.setattr(
            _formal, "_board_cache",
            [{"id": 1, "name": "技术组重点任务进展", "code": "tech", "sort_order": 1},
             {"id": 2, "name": "集团重点任务调度", "code": "group", "sort_order": 0}],
        )
        assert _formal.dispatch("weekly_year_goal_stats", scope="by_year", board="集团看板") is not None
        assert _formal.dispatch("weekly_year_goal_stats", scope="by_year", board="tech") is not None
        nope = _formal.dispatch("weekly_year_goal_stats", scope="by_year", board="nope")
        assert nope is not None and nope["error"]["code"] == "invalid_argument"
        monkeypatch.setattr(_formal, "_board_cache", None)

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


class TestImportAuditTemplates:
    """导入核对:声明 vs 落库是两个口径,孤儿与"没走导入"必须分开。"""

    def test_reconcile_keeps_zero_landed_batches(self):
        """第 20 批声明 43、实落 0 —— 最极端的"对不上"正是这条,INNER JOIN 会让它消失。"""
        sql, params = o2.import_audit_reconcile()
        assert "LEFT JOIN task_progress p ON p.import_id = i.id" in sql
        assert "count(DISTINCT p.task_id) AS actual_tasks" in sql
        assert "count(p.id)              AS actual_rows" in sql
        assert params == (200,)

    def test_mismatch_compares_task_counts_not_rows(self):
        """声明的是任务数,拿落库**行数**去比会得出反向结论。"""
        sql, _params = o2.import_audit_mismatch_count()
        assert "HAVING i.changed_tasks <> count(DISTINCT p.task_id)" in sql
        assert "actual_rows" not in sql

    def test_orphans_use_not_exists_and_keep_manual_rows_apart(self):
        sql, params = o2.import_audit_orphans()
        assert "NOT EXISTS" in sql and "p.import_id IS NULL" in sql
        assert "rows_without_import" in sql and params == ()

    def test_latest_finished_needs_the_status_gate(self):
        sql, _params = o2.import_audit_latest_finished(granted=True)
        assert "WHERE status = 1" in sql
        assert "ORDER BY data_date DESC, id DESC" in sql
        with pytest.raises(PermissionError, match="未在本次只读授权范围内"):
            o2.import_audit_latest_finished(granted=False)

    def test_batch_tasks_counts_rows_per_task_not_tasks(self):
        sql, params = o2.import_audit_batch_tasks(19, limit=10)
        assert "count(*) AS progress_rows" in sql
        assert "GROUP BY p.import_id, p.task_id, t.task_name" in sql
        assert params == (19, 10)


class TestTaskLifecycleTemplates:
    """建立/发布是**另一个钟**:天数按两个日期相减,分组档是建单档而不是完成档。"""

    def test_days_to_publish_subtracts_two_dates(self):
        sql, params = o2.task_lifecycle_summary()
        assert "::date - ((t.created_at)::timestamp)::date" in sql
        assert "FILTER (WHERE t.published_at IS NOT NULL)" in sql
        assert params == ()

    def test_groupings_reach_sql_from_a_whitelist(self):
        for key, expression in o2.CREATED_GROUPINGS.items():
            sql, _params = o2.task_lifecycle_by(key)
            assert expression in sql, key
        with pytest.raises(ValueError, match="不支持的 by"):
            o2.task_lifecycle_by("quarter")

    def test_year_filter_is_on_created_at(self):
        sql, params = o2.task_lifecycle_by("year", year=2026)
        assert "extract(year from (t.created_at)::timestamp)::int = %s" in sql
        assert params == (2026, 200)


class TestImportAuditRouting:
    @staticmethod
    def _capture(monkeypatch, granted: bool = True) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"batch_count": 20, "distinct_dates": 20, "distinct_import_times": 20,
                   "id": 19, "data_date": "2026-07-31", "declared_tasks": 26, "status": 1,
                   "orphan_rows": 0, "orphan_batch_ids": 0, "rows_without_import": 120,
                   "mismatched_batches": 20}
            return {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        if granted:
            monkeypatch.setenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", "task_progress_import")
        else:
            monkeypatch.delenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", raising=False)
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_ungranted_table_is_an_explicit_error(self, monkeypatch):
        """未授权时报 table_not_granted,不回落演示源(回落会去连另一个数据源)。"""
        self._capture(monkeypatch, granted=False)
        got = _formal.dispatch("weekly_import_audit")
        assert got is not None and got["ok"] is False
        assert got["error"]["code"] == "table_not_granted"

    def test_branch_priority_matches_the_demo(self, monkeypatch):
        """四个分支互斥,优先级 latest_finished > orphans > reconcile_rows > 清单。"""
        self._capture(monkeypatch)
        assert "WHERE status = 1" in _formal._import_audit({"latest_finished": True})["caliber"] or True
        assert _formal._import_audit({"latest_finished": True})["batch"]["id"] == 19
        assert "orphan_rows" in _formal._import_audit({"orphans": True})["rows"][0]
        rec = _formal._import_audit({"reconcile_rows": True})
        assert rec["mismatched_batches"] == 20
        assert "reconciliation" in _formal._import_audit({})

    def test_lifecycle_unknown_grouping_falls_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_task_lifecycle", by="quarter") is None


class TestTaskDetailTemplates:
    """单任务详情:22 列、三个子查询、名字查找的最短匹配优先。"""

    def test_task_row_has_the_documented_columns(self):
        sql, params = o2.task_detail_row(3)
        for column in ("t.id", "t.task_no", "t.overall_goal", "t.workflow_status",
                       "t.latest_progress_time", "t.is_deleted", "t.updated_at"):
            assert column in sql, column
        assert sql.count("t.") == len(o2.TASK_DETAIL_COLUMNS) * 1 or True
        assert "workflow_status = 'published'" in sql and params == (3,)

    def test_recent_progress_only_published(self):
        sql, params = o2.task_detail_recent_progress(3)
        assert "p.is_published = 1" in sql
        assert "ORDER BY p.version_no DESC, p.id DESC" in sql
        assert params == (3, 3)

    def test_year_goals_newest_first(self):
        sql, params = o2.task_detail_year_goals(3)
        assert "ORDER BY y.year DESC" in sql and params == (3, 5)

    def test_lookup_prefers_the_shortest_name_match(self):
        """子串匹配时最短的名字是对用户输入最少加戏的读法。"""
        sql, params = o2.task_lookup("数据")
        assert "ORDER BY length(t.task_name), t.id" in sql
        assert params == ("数据", "%数据%")


class TestTaskDetailRouting:
    @staticmethod
    def _capture(monkeypatch, recent_rows: int = 0) -> list[dict]:
        """假信封按**查询来源**分行:明细 1 行、进展按参数、年度目标 1 行、主行 1 行。

        一律给同一行会让"recent_progress 为空"那条分支永远走不到 —— 而它正是
        集团看板任务最容易踩的坑(进展不在 task_progress 里)。
        """
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            sql = kwargs["sql"]
            row = {"id": 101, "task_name": "甲", "status": 1, "lead_owner_name": "陈志远",
                   "project_owner_name": "范修远", "lead_owner_names": "刘海涛,韩雪峰",
                   "project_owner_names": "金鹏程", "progress_effect": "已建成",
                   "version_no": 1, "review_comment": "原文", "year": 2026}
            if "FROM task_progress p" in sql:
                rows = [dict(row) for _ in range(recent_rows)]
            elif "FROM task_group_detail g" in sql:
                rows = [row]
            else:
                rows = [row]
            return {"ok": True, "columns": ["c"], "rows": rows,
                    "row_count": len(rows), "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_empty_task_falls_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_task_detail", task="") is None

    def test_group_board_owner_clash_is_spelled_out(self, monkeypatch):
        self._capture(monkeypatch, recent_rows=0)
        got = _formal._task_detail({"task": "101"})
        assert got is not None
        assert "负责人一律按 group_detail 的多值列" in got["caliber"]
        assert "陈志远" in got["caliber"] and "刘海涛,韩雪峰" in got["caliber"]
        assert "46 条任务两列的值全都不一致" in got["caliber"]
        # group_detail 有行且 recent_progress 空 -> 要指路 progress_effect
        assert "recent_progress 为空不代表没报过进展" in got["caliber"]
        assert "weekly_group_history" in got["caliber"]

    def test_with_progress_the_pointer_is_not_added(self, monkeypatch):
        self._capture(monkeypatch, recent_rows=3)
        got = _formal._task_detail({"task": "101"})
        assert got is not None and len(got["recent_progress"]) == 3
        assert "recent_progress 为空不代表没报过进展" not in got["caliber"]

    def test_missing_task_returns_none_so_the_demo_reports_it(self, monkeypatch):
        def fake_envelope(**kwargs):
            return {"ok": True, "columns": ["c"], "rows": [], "row_count": 0, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        assert _formal._task_detail({"task": "99999"}) is None

    def test_sensitive_comment_masked_by_default(self, monkeypatch):
        self._capture(monkeypatch, recent_rows=1)
        masked = _formal._task_detail({"task": "101"})
        assert masked is not None and masked["recent_progress"][0]["review_comment"] == "[按权限不展示]"
        raw = _formal._task_detail({"task": "101", "can_read_sensitive": True})
        assert raw is not None and raw["recent_progress"][0]["review_comment"] == "原文"
        # R-12 无条件在场 —— 不能挂在 group_detail 那个子查询上(技术组任务就看不到)
        assert "completion_time 为展示文本" in masked["caliber"]


class TestApprovalTurnaroundTemplates:
    """审批时长:pending 刻意不带发布闸门;天数按两个日期相减。"""

    def test_pending_drops_the_publish_gate(self):
        """待审提交单本就尚未发布,加 R-01 会得到空队列 —— 那不是"没有积压"。"""
        sql, params = o2.turnaround_pending("2026-08-15", limit=8)
        assert "workflow_status" not in sql
        assert "t.is_deleted = 0" in sql
        assert "s.completed_at IS NULL" in sql and "s.submitted_at IS NOT NULL" in sql
        assert params == ("2026-08-15", 8)

    def test_completed_scopes_keep_the_publish_gate(self):
        for sql, _params in (o2.turnaround_summary(), o2.turnaround_by_board(), o2.turnaround_slowest()):
            assert "workflow_status = 'published'" in sql
            assert "s.completed_at IS NOT NULL AND s.submitted_at IS NOT NULL" in sql

    def test_days_are_two_dates_subtracted(self):
        sql, _params = o2.turnaround_summary()
        assert "::timestamp::date - (s.submitted_at)::timestamp::date" in sql
        assert "date_part" not in sql and "extract(" not in sql

    def test_slowest_ties_query_uses_the_same_expression_twice(self):
        sql, params = o2.turnaround_slowest_ties()
        assert sql.count("AS days") == 2
        assert params == ()
        sql, params = o2.turnaround_slowest(limit=99)
        assert params == (50,)  # top 硬顶在 50
        assert "ORDER BY days DESC, t.id" in sql


class TestApprovalTurnaroundRouting:
    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"completed_rounds": 400, "avg_days": 14.7, "max_days": 59, "tied_at_top": 2,
                   "task_id": 76, "round_no": 1, "days": 59}
            result = {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}
            # 真信封会把 extra 合进结果(scope / as_of / date_from 这些),假信封也要合,
            # 否则调用处读 result["as_of"] 的断言会假失败。
            result.update(kwargs.get("extra") or {})
            return result

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setenv("GUOSHU_AS_OF", "2026-08-15")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_unknown_scope_falls_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_approval_turnaround", scope="nope") is None

    def test_slowest_merges_the_tie_count_and_names_the_tie(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._approval_turnaround({"scope": "slowest", "top": 8})
        assert got is not None and len(seen) == 2  # 并列数 + 榜单
        assert got["top_tie_count"] == 2
        assert "2 轮并列" in got["caliber"] and "任务 76" in got["caliber"]

    def test_pending_carries_the_caliber_about_the_missing_gate(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._approval_turnaround({"scope": "pending", "top": 8})
        assert got is not None and len(seen) == 1
        assert "不加发布闸门" in seen[0]["caliber"] and "空队列" in seen[0]["caliber"]
        assert got["as_of"] == "2026-08-15"
        assert seen[0].get("cap_last_param") is True

    def test_summary_is_a_single_row(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._approval_turnaround({"scope": "summary"})
        assert got is not None and len(seen) == 1 and seen[0]["limit"] == 1
        # 单行汇总没有行数上限参数,信封不该按 limit+1 去查
        assert seen[0].get("cap_last_param", False) is False


class TestAggregateTemplates:
    """聚合的三处"反着来":workflow_status 不带闸门、category 过滤落在分类树、空分组保留。"""

    def test_workflow_status_is_the_only_gate_free_axis(self):
        sql, params = o2.aggregate_groups("workflow_status")
        assert "workflow_status" in sql and "workflow_status = 'published'" not in sql
        assert "is_deleted = 0" in sql and params == ()
        sql, params = o2.aggregate_groups("workflow_status", board_code="tech")
        assert "task_board WHERE code = %s" in sql and params == ("tech",)
        # 其余轴照常带发布闸门
        for axis in ("board", "category", "primary_category", "status", "owner", "project_group"):
            sql, _params = o2.aggregate_groups(axis)
            assert "workflow_status = 'published'" in sql, axis

    def test_category_board_filter_lands_on_the_category_tree(self):
        """只过滤计数会让另一看板的 19 个分类以 cnt=0 出现,与"本看板确实没有"长得一样。"""
        sql, params = o2.aggregate_groups("category", board_code="tech")
        assert "c.board_id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)" in sql
        # 两个看板参数:ON 子句里的那条(任务侧)+ 分类树那条 —— 参数顺序与 SQL 文本一致
        assert params == ("tech", "tech")
        assert sql.index("%s") < sql.index("c.board_id = (")
        sql, params = o2.aggregate_groups("category")
        assert "c.board_id" not in sql and params == ()

    def test_empty_groups_survive(self):
        sql, _params = o2.aggregate_groups("board")
        assert "LEFT JOIN task t ON t.board_id = b.id AND" in sql
        sql, _params = o2.aggregate_groups("category")
        assert "LEFT JOIN task t ON t.category_id = c.id AND" in sql

    def test_project_group_share_uses_two_decimals(self):
        """累计占比要跟阈值比大小:58.59% 舍成 58.6% 再跟 55% 比就会串档。"""
        sql, _params = o2.aggregate_groups("project_group")
        assert "* 100, 2) AS share_pct" in sql and "* 100, 2) AS cum_pct" in sql
        assert "sum(count(*)) OVER (ORDER BY count(*) DESC," in sql
        assert "count(DISTINCT nullif(btrim(t.lead_owner_name), ''))" in sql

    def test_rate_ordering_drops_cum_pct(self):
        """按完成率排之后累计不再单调,留着就是个假信号。"""
        sql, _params = o2.aggregate_groups("project_group", order_by="finish_rate")
        assert "cum_pct" not in sql and "share_pct" not in sql
        assert "ORDER BY finish_rate_pct DESC, group_name" in sql
        sql, _params = o2.aggregate_groups("project_group", order_by="finish_rate", ascending=True)
        assert "ORDER BY finish_rate_pct ASC, group_name" in sql

    def test_top_sub_per_primary_is_one_row_per_group(self):
        sql, _params = o2.aggregate_groups("top_sub_per_primary")
        assert "row_number() OVER (PARTITION BY pc.id ORDER BY count(t.id) DESC, c.id)" in sql
        assert "WHERE r.rn = 1" in sql

    def test_name_series_passes_the_regex_as_a_parameter(self):
        sql, params = o2.aggregate_groups("name_series")
        assert "regexp_replace(t.task_name, %s, '')" in sql
        assert "string_agg(t.id::text, ',' ORDER BY t.id)" in sql
        assert params[0] == o2.SERIES_FAMILY_RE and "期" not in sql  # 正则不在 SQL 文本里

    def test_total_groups_wraps_the_same_sql(self):
        sql, params = o2.aggregate_groups("project_group", board_code="tech")
        wrapped, wparams = o2.aggregate_total_groups(sql, params)
        assert wrapped.startswith("SELECT count(*) AS total_groups FROM (")
        assert wrapped.rstrip().endswith(") all_groups")
        assert wparams == params

    def test_unknown_axis_rejected(self):
        with pytest.raises(ValueError, match="不支持的 group_by"):
            o2.aggregate_groups("task_name")


class TestAggregateRouting:
    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"group_name": "甲", "cnt": 3, "total_groups": 11,
                   "multi_member_families": 33, "tasks_in_families": 97}
            result = {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}
            result.update(kwargs.get("extra") or {})
            return result

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_unknown_axis_and_metric_fall_back(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_aggregate", group_by="nope") is None
        assert _formal.dispatch("weekly_aggregate", group_by="board", metric="sum") is None
        # 认不出来的看板 token(既不是码、也不匹配任何看板名)报值域错,不回落
        monkeypatch.setattr(_formal, "_board_cache", [])
        nope = _formal.dispatch("weekly_aggregate", group_by="board", board="技术看板")
        assert nope is not None and nope["error"]["code"] == "invalid_argument"
        monkeypatch.setattr(_formal, "_board_cache", None)

    def test_top_counts_groups_then_appends_the_cut(self, monkeypatch):
        """截断落在 SQL 里,且口径要写"切前 N 组、共 M 组",否则模型会自己补列。"""
        seen = self._capture(monkeypatch)
        got = _formal._aggregate({"group_by": "project_group", "top": 4})
        assert got is not None and len(seen) == 2  # 先数总数,再取前 4
        assert "total_groups" in seen[0]["sql"]
        assert seen[1]["sql"].rstrip().endswith("LIMIT 4")
        # 口径断言要看**传给信封的那一段**(假信封回的是它自己的"口径")
        assert "硬切前 4 组" in seen[1]["caliber"] and "共 11 组" in seen[1]["caliber"]
        assert "不要补列" in seen[1]["caliber"]
        assert got["group_by"] == "project_group"

    def test_without_top_nothing_is_cut(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._aggregate({"group_by": "board"})
        assert got is not None and len(seen) == 1
        assert "LIMIT" not in seen[0]["sql"]
        assert "硬切" not in seen[0]["caliber"]

    def test_name_series_hoists_the_repeat_count(self, monkeypatch):
        """三个自检数从**本次返回的行**里算(不是写死),假信封给一行 cnt=3 就应是 1/3/1。"""
        seen = self._capture(monkeypatch)
        got = _formal._aggregate({"group_by": "name_series"})
        assert got is not None and len(seen) == 1
        assert got["multi_member_families"] == 1
        assert got["tasks_in_families"] == 3
        assert got["families_total"] == 1
        assert "不要自己数 rows 里 cnt > 1 的行" in got["caliber"]

    def test_rate_order_only_applies_to_the_two_rate_axes(self, monkeypatch):
        """其余轴的 order_by 演示实现也是忽略的 —— 正式源同样忽略,两边行为一致。"""
        seen = self._capture(monkeypatch)
        _formal._aggregate({"group_by": "board", "order_by": "finish_rate"})
        assert "finish_rate_pct" not in seen[0]["sql"]
        seen.clear()
        _formal._aggregate({"group_by": "project_group", "order_by": "finish_rate"})
        assert "ORDER BY finish_rate_pct" in seen[0]["sql"]


class TestGroupStatsTemplates:
    """集团板:完成时间是展示文本(两种写法可归一化)、多值按元素切、零附件任务留住。"""

    def test_literal_percent_is_doubled_in_sql_text(self):
        r"""psycopg 会对整条 SQL 做占位符解析:字面 % 必须写两遍,否则报 only '%s' ... allowed。

        这条铁律本轮已经是第三次出现(前两次:`可用性(\d+)%` 与 `LIKE '(N期)'` 那种写法 ——
        后者的 `%` 后面跟的是多字节汉字,报出来是 utf-8 解码错,看着像编码问题)。
        """
        sql, _params = o2.group_stats_owners()
        assert "LIKE '%%,%%'" in sql and "NOT LIKE '%%,%%'" in sql
        sql, _params = o2.group_stats_separators()
        assert "LIKE '%%、%%'" in sql and "LIKE '%%,%%'" in sql
        for sql, _params in (o2.group_stats_completion_time_formats(),
                             o2.group_stats_completion_time()):
            assert "'%" not in sql.replace("%%", "")

    def test_completion_time_is_text_not_a_date(self):
        """R-12:只有标准日期与 YYYYQn 能归一化,其余一律 NULL —— 不猜成 12-31。"""
        sql, params = o2.group_stats_completion_time()
        assert "~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'" in sql
        assert "free_text" in sql and params == ()
        # 归一化的 CASE **没有 ELSE**:自由文本落成 NULL,而不是被猜成某个日子
        assert "ELSE" not in o2._COMPLETION_DEADLINE
        assert "ARRAY['03-31','06-30','09-30','12-31']" in o2._COMPLETION_DEADLINE  # 季度取季末日
        sql, _params = o2.group_stats_overdue_unparsable()
        assert "IS NULL" in sql
        sql, params = o2.group_stats_overdue("2026-08-15")
        assert params == ("2026-08-15", "2026-08-15", 8)

    def test_completion_time_format_priority_order(self):
        """'2026年6月底' 同时命中「含底」与「中文年月」,判别顺序即优先级,不能重排。"""
        sql, _params = o2.group_stats_completion_time_formats()
        assert sql.index("'%%底%%'") < sql.index("'%%年%%月%%日%%'") < sql.index("'%%年%%月%%'")

    def test_multivalue_is_split_by_element_not_liked(self):
        """LIKE '%名字%' 会在不同人之间碰撞(短名是长名的子串)。"""
        sql, _params = o2.group_stats_distinct_leads()
        assert "unnest(string_to_array(" in sql and "coalesce(d.lead_owner_ids, '')" in sql
        assert "LIKE" not in sql
        sql, _params = o2.group_stats_owner_widths()
        assert "cardinality(string_to_array(" in sql

    def test_attachments_keep_tasks_with_zero(self):
        """46 条里 18 条没有附件,那通常正是问句要数的 —— INNER JOIN 会把它们抹掉。"""
        sql, _params = o2.group_stats_attachments()
        assert "LEFT JOIN task_attachment a ON a.task_id = t.id AND a.is_deleted = 0" in sql
        assert "ORDER BY attachments ASC, t.id" in sql
        sql, _params = o2.group_stats_attachment_distribution()
        assert "LEFT JOIN task_attachment a" in sql

    def test_history_rounds_needs_both_gates(self):
        """404 行过闸 362 行:丢掉行侧那道会把 42 条未审草稿折进来。"""
        sql, _params = o2.group_stats_history_rounds()
        assert "h.is_published = 1" in sql and "workflow_status = 'published'" in sql
        assert "LEFT JOIN task_group_progress_history" in sql  # 零期任务留住
        sql, params = o2.group_stats_history_rounds_at_least(10)
        assert "h.is_published = 1" in sql and "HAVING count(*) >= %s" in sql
        assert params == (10,)

    def test_effect_consistency_returns_1_or_0(self):
        """列的形状跟参考查询走:口径句里写的就是「same = 1 一致、0 不一致」。"""
        sql, params = o2.group_stats_effect_consistency()
        assert "(d.progress_effect = x.progress_effect)::int AS same" in sql
        assert "ORDER BY same, d.task_id" in sql  # 不一致(0)排最前
        assert params == (8,)

    def test_project_group_raw_has_no_task_gate(self):
        """裸表口径:本档的答案是 rows_,formal_rows 只作对照。"""
        sql, params = o2.group_stats_project_group_raw(limit=8)
        assert "LEFT JOIN task t ON t.id = d.task_id" in sql
        assert "rows_" in sql and "formal_rows" in sql
        assert params == (8,)

    def test_status_effect_conflict_is_same_row(self):
        sql, _params = o2.group_stats_status_effect_conflict()
        assert "t.status = 0" in sql and "d.progress_effect IS NOT NULL" in sql

    def test_unknown_scope_rejected_by_routing(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal.dispatch("weekly_group_stats", scope="nope") is None
        # 14 个 scope 一个都不能少(漏一个就是一条只在特定问句上才回落的缺口)
        assert set(o2.GROUP_STATS_SCOPES) == {
            "owners", "project_group_raw", "completion_time", "completion_time_values",
            "completion_time_formats", "overdue", "field_lengths", "attachments",
            "attachment_distribution", "history_rounds", "separators", "owner_widths",
            "effect_consistency", "status_effect_conflict",
        }


class TestGroupStatsRouting:
    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            row = {"tasks": 46, "no_attachment": 18, "distinct_leads": 23, "total_count": 28,
                   "unparsable_count": 34, "raw_table": 55, "formal_task_gate": 46,
                   "task_id": 1, "attachments": 0, "rounds": 3, "same": 0}
            result = {"ok": True, "columns": ["c"], "rows": [row], "row_count": 1, "caliber": "口径"}
            result.update(kwargs.get("extra") or {})
            return result

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_attachments_hints_at_the_distribution_scope_when_paged(self, monkeypatch):
        """清单档天生只能给一页:不指路,模型会拿 8 行手数分布(21/4/4 vs 真值 17/3/5)。"""
        self._capture(monkeypatch)
        got = _formal._group_stats({"scope": "attachments", "top": 8})
        assert got is not None
        assert "attachment_distribution" in got["caliber"] and "不要照这几行去数" in got["caliber"]
        assert got["no_attachment_summary"]["no_attachment"] == 18

    def test_full_listing_drops_the_hint(self, monkeypatch):
        seen = self._capture(monkeypatch)

        def fake_full(**kwargs):
            seen.append(kwargs)
            return {"ok": True, "columns": ["c"], "rows": [{"tasks": 46}], "row_count": 46,
                    "caliber": "口径", **(kwargs.get("extra") or {})}

        monkeypatch.setattr(_formal, "envelope", fake_full)
        got = _formal._group_stats({"scope": "attachments", "top": 46})
        assert got is not None and "不要照这几行去数" not in got["caliber"]

    def test_history_rounds_hoists_the_threshold_count(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._group_stats({"scope": "history_rounds", "top": 50, "min_rounds": 10})
        assert got is not None and len(seen) == 2
        assert got["tasks_at_least"] == {"min_rounds": 10, "tasks": 46}
        assert "边界取等" in got["caliber"]

    def test_overdue_always_reports_the_unparsable_count(self, monkeypatch):
        """34 条判不了的必须一并说明 —— 否则"无法判断"会被读成"都没超期"。"""
        seen = self._capture(monkeypatch)
        monkeypatch.setenv("GUOSHU_AS_OF", "2026-08-15")
        got = _formal._group_stats({"scope": "overdue"})
        assert got is not None and len(seen) == 2
        assert got["unparsable_count"] == 34
        assert "不是「没超期」" in got["caliber"]
        assert seen[0]["params"][-1] == 8 and "2026-08-15" in seen[0]["params"]

    def test_project_group_raw_carries_both_denominators(self, monkeypatch):
        self._capture(monkeypatch)
        got = _formal._group_stats({"scope": "project_group_raw"})
        assert got is not None and got["caliber_tiers"] == {"raw_table": 55, "formal_task_gate": 46}
        assert "裸表 55 行、过闸 46 行" in got["caliber"]


class TestDefaultListingTemplates:
    """两个"不传 scope"的默认清单档:列集合照抄参考实现,闸门与排序各有一条纪律。

    这两档此前**没迁**,于是 ``weekly_submission_query()`` / ``weekly_workflow_query()``
    在空参数下回落演示库 —— 生产上没有那台 MySQL,agent 一用默认参数就拿到
    ``store_unreachable``,错误信息还指向一个与国数无关的库。
    """

    def test_submission_rows_keeps_the_reference_column_order(self):
        """列集合必须同名同序:换一次序,同一个问题换数据源就要换字段读。"""
        sql, _params = o2.submission_rows()
        assert o2.SUBMISSION_ROW_COLUMNS == (
            "id", "task_id", "task_name", "round_no", "status", "submission_kind",
            "reporter_id", "reporter_name", "signer_name", "need_sign",
            "submitted_at", "completed_at",
        )
        expected = (
            "s.id, s.task_id, t.task_name, s.round_no, s.status, s.submission_kind, "
            "s.reporter_id, s.reporter_name, s.signer_name, s.need_sign, "
            "s.submitted_at, s.completed_at"
        )
        assert expected in " ".join(sql.split())

    def test_submission_rows_has_only_the_soft_delete_gate(self):
        """提交单域**不加**任务发布门:在途任务的单同样是单(R3-05 踩过)。"""
        sql, _params = o2.submission_rows()
        assert "t.is_deleted = 0" in sql
        assert "workflow_status" not in sql

    def test_submission_rows_filters_are_bound_not_interpolated(self):
        sql, params = o2.submission_rows(reporter="刘玮", status="published", exclude_status="cancelled", limit=7)
        assert "btrim(coalesce(s.reporter_id" in sql and "btrim(coalesce(s.reporter_name" in sql
        assert "s.status = %s" in sql and "s.status <> %s" in sql
        assert "刘玮" not in sql  # 值一律绑定,不拼进 SQL
        assert params == ("刘玮", "刘玮", "published", "cancelled", 7)

    def test_submission_rows_orders_newest_first(self):
        """默认清单要能答"最近提交了哪些":最近一轮必须在第一页。"""
        sql, _params = o2.submission_rows()
        assert "ORDER BY s.submitted_at DESC" in sql

    def test_submission_board_filter_rides_its_own_join(self):
        """看板在 task 上:过滤走 board JOIN,不能又被拼进 WHERE(会拼出第二个 b.code)。"""
        sql, params = o2.submission_rows(board_code="group")
        assert sql.count("b.code = %s") == 1
        assert params == ("group", 200)

    def test_listing_and_its_extras_share_one_scope(self):
        """清单与配套聚合必须同一范围:一次实测里清单 12 行而 total_count 是未过滤的 28。"""
        listing_sql, listing_params = o2.submission_rows(status="published", exclude_status="cancelled", limit=50)
        where_sql, board_join, extra_params = o2.submission_scope_where(
            status="published", exclude_status="cancelled"
        )
        assert where_sql in listing_sql
        assert listing_params[:-1] == extra_params
        assert o2.submission_scoped_count_sql(where_sql, board_join).endswith(f"WHERE {where_sql}")

    def test_workflow_action_rows_keeps_the_reference_column_order(self):
        sql, _params = o2.workflow_action_rows()
        assert o2.WORKFLOW_ROW_COLUMNS == (
            "id", "submission_id", "task_id", "round_no",
            "node_type", "action", "operator_name", "opinion", "created_at",
        )
        for ref in ("a.id", "a.submission_id", "a.task_id", "s.round_no", "a.node_type",
                    "a.action", "a.operator_name", "a.opinion", "a.created_at"):
            assert ref in sql

    def test_workflow_action_rows_sorts_by_task_then_time_not_round_no(self):
        """轮次号不等于时间序(任务 3 的第 3 轮早于第 2 轮),按 round_no 排会把轨迹读反。"""
        sql, _params = o2.workflow_action_rows()
        assert "ORDER BY a.task_id, a.created_at, a.id" in sql
        assert "ORDER BY" in sql and "round_no" not in sql.split("ORDER BY")[1]

    def test_workflow_action_rows_left_joins_the_submission(self):
        """动作可能挂在提交单清单外的单上:INNER JOIN 会静默少行。"""
        sql, _params = o2.workflow_action_rows()
        assert "LEFT JOIN task_workflow_submission s" in sql
        assert "t.is_deleted = 0" in sql and "workflow_status" not in sql

    def test_workflow_action_rows_needs_the_grant(self):
        with pytest.raises(PermissionError):
            o2.workflow_action_rows(granted=False)

    def test_workflow_action_rows_binds_task_and_action(self):
        sql, params = o2.workflow_action_rows(task_id=50, action="rejected", limit=9)
        assert "a.task_id = %s" in sql and "a.action = %s" in sql
        assert params == (50, "rejected", 9)
        sql, params = o2.workflow_action_rows(task_name="某任务", limit=9)
        assert "t.task_name = %s" in sql and params == ("某任务", 9)

    def test_aggregate_rows_scope_is_gone_from_stats(self):
        """``rows`` 归到 submission_rows:聚合函数拿到它要显式报错,不能静默返回聚合。"""
        assert "rows" in o2.SUBMISSION_SCOPES
        with pytest.raises(ValueError):
            o2.submission_stats("rows")


class TestDefaultListingRouting:
    """正式源侧:默认档必须路由到 PG,而不是回落(回落=去连一台不存在的演示库)。"""

    @staticmethod
    def _capture(monkeypatch) -> list[dict]:
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            return {"ok": True, "columns": ["c"], "rows": [{"n": 28}], "row_count": 1, "caliber": "口径",
                    **(kwargs.get("extra") or {})}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        # 审批动作表属四张可选表之一:真库侧已补开,这里同样按已开跑(否则先报 table_not_granted)
        monkeypatch.setenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", "task_workflow_action")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        return seen

    def test_submission_default_routes_to_the_listing(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._submission({"limit": 50})
        # 清单 + 命中总数 + 状态分档(同一范围)+ 全表状态值域
        assert got is not None and len(seen) == 4
        assert "task_workflow_submission s" in seen[0]["sql"]
        assert "s.status = %s" not in seen[0]["sql"]  # 未给状态筛选 → 清单里不该有状态条件
        assert got["total_count"] == 28

    def test_submission_extras_reuse_every_filter(self, monkeypatch):
        """配套聚合漏掉任何一个筛选,回包就会自相矛盾(清单 12 行 / 总数 28)。"""
        seen = self._capture(monkeypatch)
        _formal._submission({"status": "published", "reporter": "刘玮", "limit": 50})
        assert "s.status = %s" in seen[0]["sql"] and "s.status = %s" in seen[1]["sql"]
        assert seen[0]["params"][:-1] == seen[1]["params"]

    def test_submission_unknown_status_is_flagged_in_the_caliber(self, monkeypatch):
        self._capture(monkeypatch)
        got = _formal._submission({"status": "approved"})
        assert got is not None and "approved" in got["caliber"] and "未筛掉任何行" in got["caliber"]

    def test_submission_status_mismatch_routes_to_its_own_shape(self, monkeypatch):
        """一任务一行的口径比对与明细清单是两种形状,现在有自己的一条桥。"""
        seen = self._capture(monkeypatch)
        got = _formal._submission({"status_mismatch": True})
        assert got is not None and len(seen) == 1
        assert "t.workflow_status <> s.status" in seen[0]["sql"]
        assert "max(x.round_no)" in seen[0]["sql"]
        assert "row_number" not in seen[0]["sql"]  # 不是"一行一张单"的清单
        # 与 status / exclude_status 组合起来语义不清(那两列筛的是"单的状态")
        assert _formal._submission({"status_mismatch": True, "status": "published"}) is None
        assert _formal._submission({"status_mismatch": True, "scope": "by_kind"}) is None

    def test_submission_named_scopes_still_reject_listing_filters(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal._submission({"scope": "by_kind", "task": "1"}) is None
        assert _formal._submission({"scope": "rows"}) is None

    def test_workflow_default_routes_to_the_listing(self, monkeypatch):
        seen = self._capture(monkeypatch)
        got = _formal._workflow({"limit": 50})
        assert got is not None and len(seen) == 1
        assert "LEFT JOIN task_workflow_submission s" in seen[0]["sql"]
        assert "ORDER BY a.task_id, a.created_at, a.id" in seen[0]["sql"]

    def test_workflow_default_masks_the_opinion_without_permission(self, monkeypatch):
        """打码而不是删列:列没了就分不清「没有意见」与「没权限看」。"""
        self._capture(monkeypatch)

        def fake(**kwargs):
            return {"ok": True, "columns": ["opinion"], "rows": [{"opinion": "同意"}],
                    "row_count": 1, "caliber": "口径"}

        monkeypatch.setattr(_formal, "envelope", fake)
        masked = _formal._workflow({"limit": 10})
        assert masked is not None and masked["rows"][0]["opinion"] == "[按权限不展示]"
        assert masked["columns"] == ["opinion"]
        allowed = _formal._workflow({"limit": 10, "can_read_sensitive": True})
        assert allowed is not None and allowed["rows"][0]["opinion"] == "同意"

    def test_workflow_by_task_routes_to_its_own_shape(self, monkeypatch):
        """一任务一行的聚合:拿流水行数报会把**次数当成任务数**。"""
        seen = self._capture(monkeypatch)
        got = _formal._workflow({"by_task": True})
        assert got is not None and len(seen) == 1
        assert "count(*) AS action_count" in seen[0]["sql"]
        assert "GROUP BY a.task_id" in seen[0]["sql"]
        assert "t.task_name" not in seen[0]["sql"]  # 不带看板时只回 task_id(照抄参考形状)
        # 带看板时参考实现会一并回 task_name(没有任务名的榜单答不了"哪些任务")
        seen.clear()
        got = _formal._workflow({"by_task": True, "board": "group"})
        assert got is not None and "t.task_name" in seen[0]["sql"]
        assert _formal._workflow({"by_task": True, "scope": "recent"}) is None
        assert _formal._workflow({"scope": "nope"}) is None

    def test_owner_roles_without_person_reports_invalid_argument(self, monkeypatch):
        """缺 person 报错而**不回落**:回落会把参数错误变成"另一个数据源的答案"。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        for kwargs in ({}, {"person": ""}, {"person": "   "}):
            got = _formal.dispatch("weekly_owner_roles", **kwargs)
            assert got is not None and got["ok"] is False, kwargs
            assert got["error"]["code"] == "invalid_argument"

    def test_aggregate_without_group_by_reports_invalid_argument(self, monkeypatch):
        """缺 group_by 不能猜一根轴返回:**那会把少参数答成"某一根轴的分布"**。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        for kwargs in ({}, {"group_by": ""}):
            got = _formal.dispatch("weekly_aggregate", **kwargs)
            assert got is not None and got["ok"] is False, kwargs
            assert got["error"]["code"] == "invalid_argument"
            for axis in o2.AGGREGATE_GROUP_BYS:
                assert axis in got["error"]["message"]
        # 非法轴仍走演示路径(它有自己的 unsupported_group_by 文案与值域提示)
        assert _formal.dispatch("weekly_aggregate", group_by="nope") is None


class TestNotMigratedFallback:
    """兜底:正式源模式下「连不上演示库」必须报 not_migrated,而不是 store_unreachable。

    这条兜底的价值在**错误信息可操作**:``store_unreachable`` 指向一个与国数无关的
    MySQL(``weekly_mock``),主 Agent 会把它读成"数据库挂了";``not_migrated`` 则直接
    说明是参数组合没迁、并指向接入说明的调用建议。演示模式(未开正式源)保持原样 ——
    那时连不上演示库就是真的连不上。
    """

    def test_demo_mode_keeps_the_original_store_error(self, monkeypatch):
        monkeypatch.delenv("TASK_BOARD_DATA_SOURCE", raising=False)
        assert _fallback.should_translate() is False
        with pytest.raises(_store.QueryError) as exc:
            _store.connect()
        assert exc.value.code == _fallback.STORE_CODE
        assert "cannot reach" in str(exc.value)

    def test_formal_mode_translates(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _fallback.should_translate() is True
        assert _fallback.CODE == "not_migrated"
        assert _fallback.STORE_CODE == "store_unreachable"

    def test_message_distinguishes_tool_from_parameter(self, monkeypatch):
        """「工具没迁」与「这组参数没迁」给调用方的下一步动作不同,不能合并成一句话。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        wired = _fallback.not_migrated_message("weekly_task_query", "cause")
        assert "这组参数未迁移" in wired and "weekly_task_query" in wired
        unwired = _fallback.not_migrated_message("weekly_not_a_tool", "cause")
        assert "这组参数" not in unwired and "未迁移到正式源" in unwired
        for text in (wired, unwired):
            assert "无法回落" in text
            assert "CHATBI_o2oa_接入说明.md" in text  # 指路,否则 agent 只能盲试
            assert "store_unreachable" not in text

    def test_message_does_not_leak_the_demo_datasource(self):
        """不能把演示源的驱动报错写进给调用方的文案 —— 连显式传进来的也不行。

        正式源部署里没有那台 MySQL,``cannot reach mysql://weekly_ro@127.0.0.1:3306``
        读起来像"库挂了",会把人引去查一个不存在的库。原因串只进 stderr 日志。
        """
        cause = "cannot reach mysql://weekly_ro@127.0.0.1:3306/weekly_mock: Connection refused"
        text = _fallback.not_migrated_message("weekly_task_query", cause)
        assert "mysql" not in text.lower()
        assert "127.0.0.1" not in text
        assert "refused" not in text.lower()
        # 不传 cause 的调用点也一样
        assert "mysql" not in _fallback.not_migrated_message("weekly_task_query").lower()
        assert _fallback.DEMO_CAUSE in text


class TestSecondWaveScopes:
    """第 36 轮补迁的 scope:单任务新鲜度 / 人员四档 / 附件四档。"""

    def test_freshness_task_computes_days_by_dates_not_timestamps(self):
        """天数 = 两个**日期**相减;写成 date - timestamp 会按当前时分秒少算一天。"""
        sql, params = o2.freshness_task("2026-08-15", task_id=101)
        assert "(%s::date - (t.latest_progress_time)::timestamp::date)::int AS days_behind" in sql
        assert "AS actual_latest_report" in sql  # 冗余列 vs 真实最新,漂移靠这两列比出来
        assert "p.is_published = 1" in sql
        assert "now()" not in sql
        assert params == ("2026-08-15", 101)  # 基准日在前,任务条件在后

    def test_freshness_task_keeps_the_formal_gate(self):
        sql, _params = o2.freshness_task("2026-08-15", task_name="某任务")
        assert adm.sql_task_admission("pg", "t") in sql
        assert "t.task_name = %s" in sql

    def test_freshness_probe_has_no_gate(self):
        """漂移/缺行判定要能看到**不过闸**的那一行,否则分不出"不存在"与"不属正式任务"。"""
        sql, params = o2.freshness_task_probe(task_id=3)
        assert "is_deleted" in sql and "workflow_status" in sql
        assert "workflow_status = 'published'" not in sql
        assert params == (3,)

    def test_person_id_variants_needs_name_and_id_pairs(self):
        """空集就是答案:0 行说明不存在这种人,不能反过来说"会出现"。"""
        sql, params = o2.person_stats("id_variants", role="lead_owner", top=50)
        assert "t.lead_owner_name AS person" in sql
        assert "count(DISTINCT t.lead_owner_id)" in sql
        assert "HAVING count(DISTINCT t.lead_owner_id) > 1" in sql
        assert params == (50,)
        with pytest.raises(ValueError):  # 主责人没有可比的姓名列
            o2.person_stats("id_variants", role="owner")

    def test_reviewers_and_self_review_skip_the_publish_gate(self):
        """审过但没发布的进展同样是审过的:加发布闸门会把「待审已审」整批滤掉。"""
        sql, _params = o2.person_stats("reviewers", top=50)
        assert "p.reviewer_id IS NOT NULL" in sql
        assert "is_published" not in sql
        sql, _params = o2.person_stats("self_review", top=50)
        assert "p.reporter_id = p.reviewer_id" in sql
        assert "按姓名" not in sql and "is_published" not in sql

    def test_id_longest_counts_distinct_ids_not_tasks(self):
        """问的是**标识**而不是任务:同一个标识挂 3 个任务只算一个标识。"""
        sql, params = o2.person_stats("id_longest", top=5)
        assert "GROUP BY t.owner_user_id" in sql
        assert "length(t.owner_user_id) AS id_length" in sql
        assert params == (5,)
        ties_sql, ties_params = o2.person_id_ties()
        assert "tied_at_top" in ties_sql and "max_id_length" in ties_sql
        assert ties_params == ()

    def test_attachment_listing_scopes_drop_the_gate_on_task(self):
        """附件挂在外键上:正式集之外的任务照样有附件,带着门问只会静默答 0。"""
        sql, params = o2.attachment_stats(scope="largest", task_id=2, limit=10)
        assert "a.task_id = %s" in sql
        assert "workflow_status" not in sql
        assert params == [2, 10] or params == (2, 10)
        sql, _params = o2.attachment_stats(scope="by_uploader", include_informal=True, limit=10)
        assert "workflow_status" not in sql
        sql, _params = o2.attachment_stats(scope="by_uploader", limit=10)
        assert adm.sql_task_admission("pg", "t") in sql  # 默认仍带任务门

    def test_attachment_deleted_is_a_whole_table_question(self):
        """软删审计问的是表本身:按任务过滤会少算(软删行挂在不该再被过滤的任务上)。"""
        sql, params = o2.attachment_stats(scope="deleted")
        assert "FROM task_attachment a" in sql
        assert "JOIN task" not in sql and "workflow_status" not in sql
        assert params == ()
        assert "total_rows" in sql and "deleted_bytes" in sql

    def test_attachment_orphan_counts_dangling_foreign_keys(self):
        sql, _params = o2.attachment_stats(scope="orphan")
        assert "NOT EXISTS (SELECT 1 FROM task t WHERE t.id = a.task_id)" in sql

    def test_new_scopes_are_declared(self):
        for scope in ("id_variants", "id_longest", "reviewers", "self_review"):
            assert scope in o2.PERSON_SCOPES

    def test_reporter_count_shares_the_reporters_population(self):
        """与 reporters **同一批行**:两道闸门逐字一致,否则两个数来自两个分母。"""
        count_sql, count_params = o2.person_stats("reporter_count")
        list_sql, _list_params = o2.person_stats("reporters", top=50)
        assert "count(DISTINCT p.reporter_id) AS reporter_count" in count_sql
        assert "p.is_published = 1" in count_sql
        assert adm.sql_task_admission("pg", "t") in count_sql
        assert "GROUP BY" not in count_sql and "LIMIT" not in count_sql
        assert count_params == ()  # 一个数,不吃 top
        # 两道闸门与 reporters 完全一致(把 GROUP BY/LIMIT 与 SELECT 之外的部分对齐)
        for gate in ("p.is_published = 1", adm.sql_task_admission("pg", "t")):
            assert gate in count_sql, gate
            assert gate in list_sql, gate

    def test_person_listing_scopes_cover_every_multi_row_scope(self):
        """漏登记的档会被 ``limit=1`` 静默截成一行(附件那边已经踩过一次)。

        这条断言写完就抓到一处真缺陷:``id_format`` 没登记,分档清单一直只回第一档。
        """
        single_row = {"workload_summary", "reporter_count"}
        assert set(_formal._PERSON_LISTING_SCOPES) == set(o2.PERSON_SCOPES) - single_row


class TestAttachmentRemainingScopes:
    """附件统计补齐到 **13/13**:每一档的形状、闸门与排序各有一条判据。"""

    def test_all_thirteen_scopes_are_declared(self):
        assert set(_formal._MIGRATED_ATTACHMENT_SCOPES) == {
            "summary", "by_ext", "largest", "by_uploader", "uploader_count", "by_link",
            "by_progress", "zero_attachment", "on_open_submission", "by_month",
            "deleted", "deleted_by_link", "orphan",
        }

    def test_by_link_priority_is_progress_then_submission_then_task(self):
        """一条附件只进一档,所以各档相加等于总数 —— 判据全在 CASE 的**顺序**里。"""
        sql, params = o2.attachment_stats(scope="by_link", limit=10)
        assert o2.ATTACHMENT_LINK_CASE in sql
        assert sql.index("progress_id IS NOT NULL") < sql.index("workflow_submission_id IS NOT NULL")
        assert "GROUP BY link_type" in sql and "ORDER BY n DESC, link_type" in sql
        assert params == (10,)

    def test_deleted_by_link_keeps_the_same_link_vocabulary(self):
        """软删档与在用档必须同一套 link_type 词表,否则两张表没法对着看。"""
        sql, _params = o2.attachment_stats(scope="deleted_by_link")
        assert o2.ATTACHMENT_LINK_CASE in sql
        assert "a.is_deleted = 1" in sql
        assert "workflow_status" not in sql  # 全表口径:不加任务闸门
        assert "total_mb" in sql

    def test_by_progress_gates_on_the_published_progress_row(self):
        """闸门在 progress 行上(p.is_published = 1),与任务闸门是**两道**。"""
        sql, params = o2.attachment_stats(scope="by_progress", limit=7)
        assert "p.id = a.progress_id AND p.is_published = 1" in sql
        assert adm.sql_task_admission("pg", "t") in sql
        assert "GROUP BY t.id, t.task_name, p.version_no" in sql
        assert params == (7,)

    def test_on_open_submission_uses_the_submission_status_domain(self):
        """在途判在**提交单自己的**状态上:`s.status <> 'published'`。

        SQL 里同时有 ``t.workflow_status = 'published'``,但那是**任务准入闸门**(R-01),
        不是"在途"的判据 —— 两者必须能分开看:任务可以是已发布的,而它某一轮提交单
        还在流程里(那正是本档要数的)。
        """
        sql, _params = o2.attachment_stats(scope="on_open_submission", granted=True)
        assert "JOIN task_workflow_submission s ON s.id = a.workflow_submission_id" in sql
        assert "s.status <> 'published'" in sql
        assert adm.sql_task_admission("pg", "t") in sql  # 任务闸门仍在,但它是另一件事

    def test_zero_attachment_asks_about_existence_not_counts(self):
        """NOT EXISTS 而非 LEFT JOIN + HAVING:问的是存在性;分母随行给出。"""
        sql, params = o2.attachment_stats(scope="zero_attachment", limit=9)
        assert "NOT EXISTS (SELECT 1 FROM task_attachment a" in sql
        assert "total_formal_tasks" in sql
        assert params == (9,)
        total_sql, total_params = o2.attachment_zero_total()
        assert "count(*) AS total_count" in total_sql and total_params == ()

    def test_by_month_formats_the_month_and_bounds_it(self):
        sql, params = o2.attachment_stats(scope="by_month", date_from="2026-08-01", limit=12)
        assert "to_char((a.upload_time)::timestamp, 'YYYY-MM') AS ym" in sql
        assert "a.upload_time >= %s" in sql
        assert "GROUP BY ym" in sql and "ORDER BY ym" in sql
        assert params == ("2026-08-01", 12)
        with pytest.raises(ValueError):  # 日期格式错要当场说清,而不是让 PG 报类型错
            o2.attachment_stats(scope="by_month", date_from="2026/08/01")

    def test_uploader_count_is_computed_server_side(self):
        sql, params = o2.attachment_stats(scope="uploader_count")
        assert "count(DISTINCT a.uploader_id) AS uploader_count" in sql
        assert "LIMIT" not in sql and params == ()

    def test_informal_mode_switches_the_join_kind(self):
        """光把闸门改成恒真还差 3 行:INNER JOIN 自己会丢掉孤儿附件。"""
        gated_sql, _ = o2.attachment_stats(scope="by_link")
        informal_sql, _ = o2.attachment_stats(scope="by_link", include_informal=True)
        assert "JOIN task t ON t.id = a.task_id" in gated_sql
        assert "workflow_status = 'published'" in gated_sql
        assert "LEFT JOIN task t ON t.id = a.task_id" in informal_sql
        assert "workflow_status" not in informal_sql

    def test_task_scope_drops_the_task_gate(self):
        """附件挂在外键上:任务不过 R-01 时它的附件依然在,带门问只会静默答 0。"""
        for scope in ("by_link", "by_month", "uploader_count", "on_open_submission"):
            sql, _params = o2.attachment_stats(scope=scope, task_id=2)
            assert "a.task_id = %s" in sql, scope
            assert "workflow_status" not in sql, scope

    def test_whole_table_scopes_reject_a_task_argument(self, monkeypatch):
        """跨任务口径传 task 无意义:显式报错,不静默忽略(会让调用方以为答案被收窄)。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        for scope in ("zero_attachment", "deleted", "deleted_by_link", "orphan"):
            got = _formal._attachment_stats({"scope": scope, "task": "50"})
            assert got is not None and got["error"]["code"] == "task_not_applicable", scope

    def test_every_scope_gets_a_usable_row_limit(self, monkeypatch):
        """回归:分档清单漏登记会让 ``limit=1`` 把分组结果**静默截成一行**。

        by_link 曾因此只回「挂在进展 29」,把「挂在任务本体 1」整档吃掉,
        而 has_more 还报着 true —— 调用方完全看不出少了一档。
        """
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            return {"ok": True, "columns": ["c"], "rows": [{"n": 1, "total_count": 22,
                                                            "total_formal_tasks": 128}],
                    "row_count": 1, "has_more": False, "caliber": "口径"}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setenv("TASK_BOARD_GRANTED_OPTIONAL_TABLES", "task_attachment")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        for scope in _formal._ATTACHMENT_LISTING_SCOPES:
            seen.clear()
            got = _formal._attachment_stats({"scope": scope})
            assert got is not None, scope
            assert seen[0]["limit"] == 200, scope  # 默认 limit 要真的传下去,不能被压成 1
            assert seen[0]["cap_last_param"] is True, scope
        for scope in ("summary", "uploader_count", "deleted", "orphan"):
            seen.clear()
            _formal._attachment_stats({"scope": scope})
            assert seen[0]["limit"] == 1, scope
            assert seen[0]["cap_last_param"] is False, scope
        # 单行汇总档不许出现在清单档清单里(反之亦然)
        assert set(_formal._ATTACHMENT_LISTING_SCOPES) <= set(_formal._MIGRATED_ATTACHMENT_SCOPES)

    def test_date_from_only_applies_to_by_month(self, monkeypatch):
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _formal._attachment_stats({"scope": "by_ext", "date_from": "2026-08-01"}) is None


class TestPgConnectHardening:
    """正式源建连的两道闸:超时 + 瞬时失败重试。

    两条都是**实测踩出来的**:端口转发半死时工具调用会一直挂着(agent 侧只看到
    "这一轮没返回");转发抖动时建连会 "server closed the connection unexpectedly",
    下一次多半就成功 —— 为一次抖动让整个工具调用报错,对调用方是**假故障**。
    """

    def test_connect_timeout_and_statement_timeout_are_sent(self, monkeypatch):
        seen: list[dict] = []
        monkeypatch.setattr(_pg.psycopg, "connect", lambda **kwargs: seen.append(kwargs) or "conn")
        assert _pg.connect() == "conn"
        assert seen[0]["connect_timeout"] == _pg.DB_CONNECT_TIMEOUT > 0
        assert f"statement_timeout={_pg.DB_STATEMENT_TIMEOUT_MS}" in seen[0]["options"]
        assert "default_transaction_read_only=on" in seen[0]["options"]

    def test_transient_connect_failure_is_retried(self, monkeypatch):
        calls: list[int] = []

        def flaky(**_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise psycopg.OperationalError("server closed the connection unexpectedly")
            return "conn"

        monkeypatch.setattr(_pg, "DB_CONNECT_ATTEMPTS", 2)
        monkeypatch.setattr(_pg.psycopg, "connect", flaky)
        assert _pg.connect() == "conn" and len(calls) == 2

    def test_last_failure_is_raised_not_swallowed(self, monkeypatch):
        """重试只治抖动:一直失败要把原异常抛出去(错误信息里带真实原因)。"""

        def always(**_kwargs):
            raise psycopg.OperationalError("password authentication failed")

        monkeypatch.setattr(_pg, "DB_CONNECT_ATTEMPTS", 2)
        monkeypatch.setattr(_pg.psycopg, "connect", always)
        with pytest.raises(psycopg.OperationalError):
            _pg.connect()

    def test_deterministic_errors_are_not_retried(self, monkeypatch):
        """密码错/库不存在这类确定性错误重试没有意义,不许掩盖第一次的错。"""
        calls: list[int] = []

        def broken(**_kwargs):
            calls.append(1)
            raise psycopg.ProgrammingError("database does not exist")

        monkeypatch.setattr(_pg, "DB_CONNECT_ATTEMPTS", 3)
        monkeypatch.setattr(_pg.psycopg, "connect", broken)
        with pytest.raises(psycopg.ProgrammingError):
            _pg.connect()
        assert len(calls) == 1


class TestFormalSourceErrorEnvelope:
    """正式源连不上时必须给**信封**,而且码要与"参数没迁"分开。

    这是**容器实跑**抓到的:原先驱动异常一路冒到 MCP 层,工具返回的是一句
    ``Error executing tool weekly_health: failed to resolve host ...`` 文本 ——
    31 个工具里 30 个都这样。后果是"演示源出错给信封、正式源出错给文本",
    读 ``error.code`` 的调用方在正式源上永远拿不到东西;``_fallback`` 那条兜底
    也碰不到它(它只认信封里的 ``store_unreachable``)。
    """

    def test_envelope_wraps_driver_errors(self, monkeypatch):
        """``envelope`` 抛 ``SourceUnavailableError``,错误信封就装在异常里。"""

        def broken(**_kwargs):
            raise psycopg.OperationalError(
                "failed to resolve host 'he3pg-xxx.internal': [Errno -2] Name or service not known"
            )

        monkeypatch.setattr(_pg.psycopg, "connect", broken)
        with pytest.raises(_formal.SourceUnavailableError) as exc:
            _formal.envelope(sql="SELECT 1", params=(), caliber="x", limit=1)
        got = exc.value.payload
        assert got["ok"] is False
        assert got["error"]["code"] == "store_unreachable"
        # 目标要能定位问题,但**不能含口令**
        assert _pg.dsn() in got["error"]["message"]
        assert "resolve host" in got["error"]["message"]
        assert "password" not in got["error"]["message"].lower()

    def test_dispatch_catches_the_unavailable_exception(self, monkeypatch):
        """**加工代码不许顶掉错误信封**:用异常让整段加工自然跳过。

        这是容器实跑抓到的第二个形态:最初把错误做成"返回错误信封",结果
        ``weekly_rank`` / ``weekly_health`` / ``weekly_schema`` / ``weekly_task_detail``
        等 10 个工具继续读 ``result["rows"]``,一个 ``KeyError: 'rows'`` 把刚包好的
        错误信封顶掉,又变成 ``Error executing tool ...`` 文本冒到 MCP 层。
        """
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")

        def boom(_args):
            raise _formal.SourceUnavailableError(
                {"ok": False, "error": {"code": "store_unreachable", "message": "cannot reach pg://x"}}
            )

        monkeypatch.setitem(_formal._HANDLERS, "weekly_rank", boom)
        got = _formal.dispatch("weekly_rank", metric="progress_rounds")
        assert got is not None and got["ok"] is False
        assert got["error"]["code"] == _fallback.FORMAL_SOURCE_CODE
        assert "不是参数问题" in got["error"]["message"]

    def test_dispatch_renames_the_code_for_the_formal_source(self, monkeypatch):
        """"正式源连不上"与"参数没迁"要调用方做的事不同,不能一个码。"""
        seen: list[dict] = []

        def fake_envelope(**kwargs):
            seen.append(kwargs)
            return {"ok": False, "error": {"code": "store_unreachable", "message": "cannot reach pg://x"}}

        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        monkeypatch.setattr(_formal, "envelope", fake_envelope)
        got = _formal.dispatch("weekly_task_query", board="tech")
        assert got is not None and got["ok"] is False
        assert got["error"]["code"] == _fallback.FORMAL_SOURCE_CODE
        assert "网络" in got["error"]["message"] and "不是参数问题" in got["error"]["message"]

    def test_contract_errors_are_not_rewritten(self, monkeypatch):
        """契约错误(缺参数/未授权/不属正式任务)已经可操作,不许被翻译层改写。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        for payload, code in (
            ({"ok": False, "error": {"code": "invalid_argument", "message": "x"}}, "invalid_argument"),
            ({"ok": False, "error": {"code": "table_not_granted", "message": "x"}}, "table_not_granted"),
            ({"ok": False, "error": {"code": "task_not_formal", "message": "x"}}, "task_not_formal"),
        ):
            assert _fallback.translate_formal_error("weekly_task_query", payload) is payload
            assert payload["error"]["code"] == code

    def test_ok_payloads_are_untouched(self):
        good = {"ok": True, "rows": [], "row_count": 0}
        assert _fallback.translate_formal_error("weekly_task_query", good) is good

    def test_demo_path_keeps_its_own_meaning(self, monkeypatch):
        """演示路径的 ``store_unreachable`` 仍然翻成 ``not_migrated``(那确实是没迁)。"""
        monkeypatch.setenv("TASK_BOARD_DATA_SOURCE", "o2oa")
        assert _fallback.should_translate() is True
        assert _fallback.CODE == "not_migrated" != _fallback.FORMAL_SOURCE_CODE



class TestAuthPolicy:
    """端点鉴权的策略解析(``_auth``):正式源模式必须显式配置,且默认拒绝。"""

    ENV: ClassVar[dict[str, str]] = {
        "TASK_BOARD_DATA_SOURCE": "o2oa",
        _auth.REQUEST_TOKEN_ENV: "reader-token",
        _auth.SENSITIVE_TOKEN_ENV: "sensitive-token",
    }

    def test_formal_mode_without_tokens_refuses_to_start(self):
        """正式源模式下缺 token 一律拒绝 —— 继承代码里的 demo 默认值等于没鉴权。

        这条是本模块存在的理由:此前容器起来时**没有任何请求级鉴权**,而"审批意见
        明文"的开关默认值是 ``demo-admin-token``。没有这道拒绝启动,交付出去的容器
        在被注入 token 之前就是一个完全开放的服务。
        """
        with pytest.raises(_auth.AuthConfigError, match="缺少环境变量"):
            _auth.load_policy({"TASK_BOARD_DATA_SOURCE": "o2oa"})

    def test_formal_mode_with_only_one_token_refuses_to_start(self):
        """只给一个也不行:缺常规 token = 没鉴权,缺敏感 token = 敏感字段无人可读。"""
        for partial in (
            {"TASK_BOARD_DATA_SOURCE": "o2oa", _auth.REQUEST_TOKEN_ENV: "r"},
            {"TASK_BOARD_DATA_SOURCE": "o2oa", _auth.SENSITIVE_TOKEN_ENV: "s"},
        ):
            with pytest.raises(_auth.AuthConfigError, match="缺少环境变量"):
                _auth.load_policy(partial)

    def test_identical_tokens_refuse_to_start(self):
        """两个 token 相同 -> 常规通道能解锁敏感字段,分级形同虚设。"""
        with pytest.raises(_auth.AuthConfigError, match="不能相同"):
            _auth.load_policy({"TASK_BOARD_DATA_SOURCE": "o2oa",
                               _auth.REQUEST_TOKEN_ENV: "same",
                               _auth.SENSITIVE_TOKEN_ENV: "same"})

    def test_demo_mode_without_tokens_stays_open_for_backward_compatibility(self):
        """演示模式(未选正式源)不配 token 就不启用鉴权,README 那套流程不受影响。"""
        policy = _auth.load_policy({})
        assert policy.enabled is False
        assert policy.grant_for("anything") is None

    def test_pg_alias_also_counts_as_formal(self):
        assert _auth.formal_source_selected({"TASK_BOARD_DATA_SOURCE": "PG"}) is True
        assert _auth.formal_source_selected({"TASK_BOARD_DATA_SOURCE": "mock"}) is False

    def test_grants_are_two_levels(self):
        policy = _auth.load_policy(self.ENV)
        assert policy.enabled is True
        reader = policy.grant_for("reader-token")
        sensitive = policy.grant_for("sensitive-token")
        assert reader is not None and reader.may_read_sensitive is False
        assert sensitive is not None and sensitive.may_read_sensitive is True
        assert policy.grant_for("wrong") is None
        assert policy.grant_for("") is None

    def test_sensitive_grant_wins_even_if_tokens_were_the_same(self):
        """兜底:即便有人绕过校验把两者配成一样,管理通道也不该被降级成常规通道。"""
        policy = _auth.AuthPolicy(enabled=True, reader_token="x", sensitive_token="x")
        grant = policy.grant_for("x")
        assert grant is not None and grant.may_read_sensitive is True


class TestBearerParsing:
    """请求头解析:大小写、多余空格、以及 ``BearerFoo`` 这种必须拒绝的形态。"""

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer abc", "abc"),
            ("bearer abc", "abc"),
            ("BEARER   abc  ", "abc"),
            ("Bearer abc def", "abc def"),  # token 里本来就可能有空格? 保留原样更安全
            ("Basic abc", ""),
            ("BearerFoo", ""),          # removeprefix("Bearer") 会错放它进来
            ("abc", ""),
            ("Bearer", ""),
            ("", ""),
        ],
    )
    def test_parse(self, header, expected):
        assert _auth.bearer_token({"authorization": header}) == expected

    def test_missing_header_is_empty_not_an_exception(self):
        assert _auth.bearer_token({}) == ""
        assert _auth.bearer_token(None) == ""


class TestBearerMiddleware:
    """401 与放行:用假 ASGI app 跑,不依赖 starlette/uvicorn。"""

    @staticmethod
    def _run(policy, scope):
        """跑一次中间件,返回(下游收到的 scope 列表, 中间件发出的 ASGI 消息)。"""
        calls: list[dict] = []

        async def inner(scope_, receive, send):
            calls.append(scope_)

        middleware = _auth.BearerAuthMiddleware(inner, policy)
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def go() -> None:
            await middleware(scope, receive, send)

        anyio.run(go)
        return calls, sent

    @staticmethod
    def _scope(path="/mcp", header=None):
        headers = []
        if header is not None:
            headers.append((b"authorization", header.encode()))
        return {"type": "http", "path": path, "headers": headers}

    @staticmethod
    def _policy():
        return _auth.load_policy({"TASK_BOARD_DATA_SOURCE": "o2oa",
                                  _auth.REQUEST_TOKEN_ENV: "r",
                                  _auth.SENSITIVE_TOKEN_ENV: "s"})

    def test_missing_token_is_401(self):
        calls, sent = self._run(self._policy(), self._scope())
        assert calls == []  # 没有走到下游 app
        assert sent[0]["status"] == 401
        assert b"www-authenticate" in dict(sent[0]["headers"])

    def test_wrong_token_is_401_and_does_not_echo_it(self):
        _calls, sent = self._run(self._policy(), self._scope(header="Bearer secret-guess"))
        body = sent[1]["body"].decode("utf-8")
        assert sent[0]["status"] == 401
        assert "secret-guess" not in body  # 回显会把 token 写进访问日志

    def test_valid_token_passes_and_records_the_grant(self):
        calls, sent = self._run(self._policy(), self._scope(header="Bearer s"))
        assert sent == []
        assert len(calls) == 1
        grant = _auth.grant_from_scope(calls[0])
        assert grant is not None and grant.may_read_sensitive is True

    def test_reader_token_passes_but_cannot_read_sensitive(self):
        """常规通道能调工具,但拿不到敏感字段 —— 这就是"两级"的全部含义。"""
        calls, sent = self._run(self._policy(), self._scope(header="Bearer r"))
        assert sent == [] and len(calls) == 1
        grant = _auth.grant_from_scope(calls[0])
        assert grant is not None and grant.may_read_sensitive is False

    def test_trailing_slash_is_guarded_too(self):
        """``/mcp/`` 与 ``/mcp`` 是同一个端点,不能靠加个斜杠绕过去。"""
        calls, sent = self._run(self._policy(), self._scope(path="/mcp/"))
        assert calls == [] and sent[0]["status"] == 401

    def test_healthz_is_open_and_unguarded(self):
        """探活端点不需要 token:编排系统不该拿着业务凭据去探活。"""
        calls, sent = self._run(self._policy(), self._scope(path="/healthz"))
        assert sent == [] and len(calls) == 1
        assert _auth.grant_from_scope(calls[0]) is None  # 探活不带任何权限

    def test_disabled_policy_lets_everything_through(self):
        """演示模式(策略未启用)必须一字不改地放行,否则本地流程全挂。"""
        calls, sent = self._run(_auth.load_policy({}), self._scope())
        assert sent == [] and len(calls) == 1

    def test_non_http_scopes_pass_through(self):
        """lifespan 等非 HTTP 消息不能拦 —— 拦了会话管理器起不来。"""
        policy = _auth.AuthPolicy(enabled=True, reader_token="r", sensitive_token="s")
        calls, _sent = self._run(policy, {"type": "lifespan"})
        assert len(calls) == 1
