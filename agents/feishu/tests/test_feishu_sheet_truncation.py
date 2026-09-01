"""Truncated sheet reads must not read as complete ones.

``feishu_sheet_read`` stops at a character budget and drops the remaining rows
**wholesale** — it breaks at a row boundary rather than cutting a cell in half. That
design is right (half a cell would be worse), but it creates a specific trap: the rows
that were cut are *absent*, and absent looks exactly like empty. A caller that answers
"who filled their TODO" from a truncated grid reports people as having filled nothing
when their row was simply never fetched — a silent miscall, in the direction of blaming
somebody.

Observed in a live session: eight ``feishu_sheet_read`` calls, seven of them truncated,
before the model switched to the paging reader. The tool descriptions are what steer that
choice, so the two tests at the bottom pin the *description* contract alongside the
payload one: the paging reader used to expose a 73-character summary while the truncating
one exposed 477 and recommended itself, which is a hint pointing the wrong way.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# Imported dynamically, like the other tool tests: ``tools/`` lands on ``sys.path`` at
# runtime, so a static checker cannot resolve these names as plain imports.
_impl: Any = importlib.import_module("_feishu_impl")
_sheet: Any = sys.modules["_feishu.sheet"]
feishu_sheet: Any = importlib.import_module("feishu_sheet")
feishu_sheet_read_grid: Any = importlib.import_module("feishu_sheet_read_grid")


def _rows(count: int, width: int = 40) -> list[list[str]]:
    """``count`` rows whose cells are wide enough to blow a small budget."""
    return [[f"r{index}-{'x' * width}"] for index in range(count)]


@pytest.fixture
def served_rows(monkeypatch: pytest.MonkeyPatch):
    """Serve a fixed grid for ``read_sheet_range_impl`` without touching the network."""

    def install(rows: list[list[str]]) -> None:
        async def fake_invoke(_req: Any, **_kw: Any) -> dict[str, Any]:
            return {"ok": True, "data": {"valueRange": {"range": "S!A1:C99", "values": rows}}}

        monkeypatch.setattr(_impl, "_invoke", fake_invoke)

    return install


def _read(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("token", "tok")
    kwargs.setdefault("range_", "S!A1:C99")
    return anyio.run(lambda: _sheet.read_sheet_range_impl(**kwargs))


class TestTruncationIsLoud:
    def test_truncated_result_carries_a_warning_and_the_cut_point(self, served_rows: Any) -> None:
        """A bare ``truncated: true`` sits quietly beside a plausible-looking grid."""
        served_rows(_rows(9))
        result = _read(max_chars=100)

        assert result["truncated"] is True
        assert result["row_count"] < 9, "the budget must actually have cut something"
        assert result["rows_dropped_after_row"] == result["row_count"]
        assert result["warning"], "truncation must be stated, not left to a boolean"

    def test_warning_says_dropped_not_empty_and_names_the_way_out(self, served_rows: Any) -> None:
        """The two facts a caller needs: rows are missing, and what to do instead."""
        served_rows(_rows(9))
        warning = _read(max_chars=100)["warning"]

        assert "NOT read as empty" in warning, "absent-vs-empty is the whole trap"
        assert "feishu_sheet_read_grid" in warning, "the paging reader must be named"

    def test_rows_break_at_a_boundary_rather_than_mid_cell(self, served_rows: Any) -> None:
        """Every returned cell is whole — the budget drops rows, it does not clip text."""
        served_rows(_rows(9))
        result = _read(max_chars=100)

        for row_index, row in enumerate(result["rows"]):
            assert row == _rows(9)[row_index], "a returned row must match the source exactly"

    def test_untruncated_read_stays_quiet(self, served_rows: Any) -> None:
        """No warning noise on the normal path, or it stops meaning anything."""
        served_rows(_rows(3))
        result = _read(max_chars=20000)

        assert result["truncated"] is False
        assert "warning" not in result
        assert "rows_dropped_after_row" not in result

    def test_zero_budget_disables_the_limit(self, served_rows: Any) -> None:
        served_rows(_rows(9))
        result = _read(max_chars=0)

        assert result["truncated"] is False
        assert result["row_count"] == 9
        assert "warning" not in result


class TestToolDescriptionsSteerTheChoice:
    """What the model sees when picking between the two readers.

    ``ToolRegistry.from_function`` builds the description from
    ``inspect.getdoc(func)`` — the **function** docstring, everything before ``Args:``.
    Guidance parked in the module docstring is invisible to the model, which is how the
    paging reader ended up advertising one line while the truncating one advertised nine.
    """

    @staticmethod
    def _described(func: Any) -> str:
        return (inspect.getdoc(func) or "").split("Args:")[0].strip()

    def test_paging_reader_is_not_out_advertised_by_the_truncating_one(self) -> None:
        paging = self._described(feishu_sheet_read_grid.feishu_sheet_read_grid)
        truncating = self._described(feishu_sheet.feishu_sheet_read)

        # Not an arbitrary ratio: the point is that the safe reader must not look like an
        # afterthought next to the lossy one.
        assert len(paging) > len(truncating) / 2, "the safe reader needs a comparable pitch"

    def test_each_reader_points_at_the_other(self) -> None:
        """Whichever one the model reads first, it learns the other exists."""
        assert "feishu_sheet_read" in self._described(feishu_sheet_read_grid.feishu_sheet_read_grid)
        assert "feishu_sheet_read_grid" in self._described(feishu_sheet.feishu_sheet_read)

    def test_truncating_reader_warns_in_its_own_description(self) -> None:
        described = self._described(feishu_sheet.feishu_sheet_read)
        assert "truncated" in described.lower()
        assert "whole board" in described.lower(), "the wrong first move must be named"

    def test_paging_reader_states_the_read_until_done_rule(self) -> None:
        described = self._described(feishu_sheet_read_grid.feishu_sheet_read_grid)
        assert "has_more" in described
        assert "locate" in described.lower(), "locate-then-fetch is the recipe that avoids truncation"


class TestBothReadersAreDeclared:
    """The two tools #723 added must appear in the docs the agent is given.

    #723 shipped ``feishu_sheet_read_grid`` and ``feishu_sheet_find_columns`` and updated
    ``skills/feishu-sheet/SKILL.md``, but neither tool was mentioned in ``TOOLS.md`` or
    ``AGENTS.md`` — zero occurrences in both. A tool nobody is told about is a tool that
    does not get used: the paging reader sat unused while eight of the truncating reader's
    calls ran, seven of them truncated. Declaration is part of shipping a tool, so it is
    asserted rather than trusted to review.
    """

    WORKSPACE = Path(__file__).resolve().parents[1]

    @pytest.mark.parametrize("doc_name", ["TOOLS.md", "AGENTS.md"])
    @pytest.mark.parametrize("tool", ["feishu_sheet_read_grid", "feishu_sheet_find_columns"])
    def test_tool_is_named(self, doc_name: str, tool: str) -> None:
        text = (self.WORKSPACE / doc_name).read_text(encoding="utf-8")
        assert tool in text, f"{tool} is exposed to the model but never declared in {doc_name}"
