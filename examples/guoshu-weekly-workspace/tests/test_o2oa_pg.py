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

    def test_ts_normalization(self):
        expr = adm.normalize_ts_sql("t.published_at", "pg")
        assert expr.startswith("to_char(") and "YYYY-MM-DD" in expr


class TestO2oaTemplates:
    def test_published_task_list(self):
        sql, params = o2.published_task_list("tech", keyword="规划")
        assert "workflow_status = %s" in sql
        assert "b.code = %s" in sql
        assert params[:2] == ("published", "tech")
        assert "%规划%" in params  # ILIKE pattern
        assert "task_category" in sql

    def test_board_code_required(self):
        with pytest.raises(ValueError):
            o2.published_task_list("all")

    def test_latest_formal_progress(self):
        sql, params = o2.latest_formal_progress("group")
        assert "is_published = 1" in sql
        assert "MAX(p2.version_no)" in sql
        assert params == ("published", "group")

    def test_task_detail_needs_explicit_year(self):
        with pytest.raises(ValueError):
            o2.task_detail_with_goals(1, None)

    def test_task_detail_admission(self):
        sql, params = o2.task_detail_with_goals(101, 2026)
        assert "t.workflow_status = %s" in sql
        assert "is_deleted = 0" in sql
        assert params == (2026, 101, "published")
