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
        assert "p.progress_date >= %s" in sql and "p.progress_date <= %s" in sql
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
