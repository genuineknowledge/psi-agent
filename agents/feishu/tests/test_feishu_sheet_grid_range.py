"""``feishu_sheet_read_grid`` must honor pinned columns and stay valid JSON when capped.

Two defects, observed together in a live session on a 32-person TODO board and both
reproduced below.

**The column range was a dead argument.** ``read_sheet_grid_impl`` took only the
``sheetId`` out of ``range_`` and always fetched ``A{start}:ZZ{end}``. The model,
having been told a wide read is the wrong first move, dutifully narrowed to the name
column (``!B1:B80``) and to one date column (``!O1:O80``) — and got the full-width
board back all three times, 322,338 characters of it. The advice was sound and the
tool silently ignored it.

**Blowing the cap destroyed the paging contract.** This tool has no budget of its own
and promises ``has_more`` / ``next_start_row`` instead. But the session layer caps a
tool result at ``MAX_TOOL_RESULT_CHARS`` and cuts mid-string, so the JSON came back
unparseable — ``json.loads`` failed on all four ``read_grid`` results from that
session, while every smaller ``feishu_sheet_read`` result parsed fine. With the
metadata gone the model could not page, and fell back to the reader its own
description forbids for fact questions.

Row numbers in ``range_`` stay ignored on purpose: pinning them is what caused the
earlier 钉死 A1:S20 → 第 31 行漏读 incident, so column narrowing must not smuggle a
row limit back in.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.session.history_display import truncate_tool_result

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_sheet: Any = sys.modules["_feishu.sheet"]
feishu_sheet_read_grid: Any = importlib.import_module("feishu_sheet_read_grid")


def _requested_rows(range_str: str) -> tuple[int, int]:
    """The 1-based row span Feishu is being asked for, from ``S!B7:O56``."""
    cells = range_str.split("!", 1)[1] if "!" in range_str else ""
    start, _, end = cells.partition(":")
    first = int("".join(c for c in start if c.isdigit()) or 1)
    last = int("".join(c for c in end if c.isdigit()) or first)
    return first, last


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch):
    """Capture the range actually requested, and serve that slice of a canned sheet.

    Slicing by the requested rows (rather than replaying the same block forever) is
    what makes a paging loop testable: a real sheet runs out of rows, and the loop
    must notice. A fake that ignores ``start_row`` can never distinguish "paged to
    the end" from "spun on block one".
    """
    seen: dict[str, Any] = {}

    def install(rows: list[list[str]]) -> dict[str, Any]:
        async def fake_invoke(req: Any, **_kw: Any) -> dict[str, Any]:
            seen["range"] = req.paths.get("range", "")
            first, last = _requested_rows(seen["range"])
            return {
                "ok": True,
                "data": {"valueRange": {"range": seen["range"], "values": rows[first - 1 : last]}},
            }

        monkeypatch.setattr(_impl, "_invoke", fake_invoke)
        return seen

    return install


def _grid(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("token", "tok")
    return anyio.run(lambda: _sheet.read_sheet_grid_impl(**kwargs))


class TestPinnedColumnsAreHonored:
    def test_single_column_range_fetches_only_that_column(self, spy: Any) -> None:
        """``!B1:B80`` used to fetch A..ZZ — the narrowing the caller asked for was dropped."""
        seen = spy([["name"]])
        _grid(range_="S!B1:B80", max_rows=80)

        assert seen["range"] == "S!B1:B80"

    def test_column_span_is_kept_end_to_end(self, spy: Any) -> None:
        seen = spy([["b", "c"]])
        _grid(range_="S!B2:O40", max_rows=39, start_row=2)

        assert seen["range"] == "S!B2:O40"

    def test_bare_sheet_id_still_reads_full_width(self, spy: Any) -> None:
        """No columns pinned means no narrowing — the old default must survive."""
        seen = spy([["a"]])
        _grid(range_="S", max_rows=60)

        assert seen["range"] == "S!A1:ZZ60"

    def test_empty_range_resolves_first_sheet_at_full_width(self, spy: Any, monkeypatch: Any) -> None:
        async def fake_first(_token: str, _user_key: str) -> tuple[str, str]:
            return "FIRST", "Sheet1"

        monkeypatch.setattr(_sheet, "_first_sheet_id", fake_first)
        seen = spy([["a"]])
        _grid(max_rows=10)

        assert seen["range"] == "FIRST!A1:ZZ10"

    def test_row_numbers_in_range_are_ignored(self, spy: Any) -> None:
        """Paging stays driven by start_row/max_rows: pinning rows is how 第 31 行 got lost."""
        seen = spy([["a"]])
        _grid(range_="S!B1:O20", max_rows=50, start_row=7)

        assert seen["range"] == "S!B7:O56", "rows come from start_row/max_rows, not from range"

    def test_reversed_column_span_is_normalized(self, spy: Any) -> None:
        """``O1:B80`` would make Feishu reject the range; the caller only sees an error string."""
        seen = spy([["a"]])
        _grid(range_="S!O1:B80", max_rows=80)

        assert seen["range"] == "S!B1:O80"

    def test_open_ended_column_span_repeats_the_single_column(self, spy: Any) -> None:
        seen = spy([["a"]])
        _grid(range_="S!C5", max_rows=30, start_row=5)

        assert seen["range"] == "S!C5:C34"


class TestCappedBlocksStayParseable:
    @staticmethod
    def _wide_rows(count: int, cell_chars: int) -> list[list[str]]:
        return [[f"r{index}" + "x" * cell_chars] for index in range(count)]

    def test_oversized_block_is_cut_at_a_row_boundary(self, spy: Any) -> None:
        spy(self._wide_rows(60, 2000))
        result = _grid(range_="S", max_rows=60)

        assert result["row_count"] < 60, "a 120k-char block must not be handed back whole"
        for row in result["rows"]:
            assert row[0].endswith("x" * 2000), "rows are dropped whole, never clipped mid-cell"

    def test_cut_block_reports_more_and_where_to_resume(self, spy: Any) -> None:
        """The whole point: the cut is folded into the paging contract, not hidden."""
        spy(self._wide_rows(60, 2000))
        result = _grid(range_="S", max_rows=60, start_row=1)

        assert result["has_more"] is True
        assert result["next_start_row"] == 1 + result["row_count"]

    def test_tool_output_survives_the_wire_cap_as_valid_json(self, spy: Any) -> None:
        """Four live ``read_grid`` results failed ``json.loads`` — the metadata went with them."""
        spy(self._wide_rows(60, 2000))
        payload = anyio.run(lambda: feishu_sheet_read_grid.feishu_sheet_read_grid(token="tok", range="S", max_rows=60))
        on_the_wire = truncate_tool_result(payload)

        assert on_the_wire == payload, "a result that needs wire-truncation has already lost its JSON"
        decoded = json.loads(on_the_wire)
        assert decoded["has_more"] is True
        assert decoded["next_start_row"] == 1 + decoded["row_count"]

    def test_paging_a_wide_board_terminates_and_covers_every_row(self, spy: Any) -> None:
        """Fewer rows per block is fine; silently spinning or skipping rows is not."""
        spy(self._wide_rows(9, 3000))
        seen_rows: list[int] = []
        start = 1
        for _ in range(20):
            block = _grid(range_="S", max_rows=9, start_row=start)
            assert block["row_count"] >= 1, "a block must always advance, or paging never ends"
            seen_rows.append(block["row_count"])
            if not block["has_more"]:
                break
            start = block["next_start_row"]
        else:
            pytest.fail("paging did not terminate")

        assert sum(seen_rows) >= 9, "every row of the fixture must be reachable by paging"

    def test_single_huge_row_is_still_returned(self, spy: Any) -> None:
        """Returning zero rows would pin next_start_row to start_row and loop forever."""
        spy(self._wide_rows(1, 40_000))
        result = _grid(range_="S", max_rows=50)

        assert result["row_count"] == 1

    def test_small_block_is_untouched(self, spy: Any) -> None:
        spy([["a"], ["b"], ["c"]])
        result = _grid(range_="S", max_rows=50)

        assert result["row_count"] == 3
        assert result["has_more"] is False
        assert result["next_start_row"] is None


class TestReadRangeBudgetIsHonest:
    """``feishu_sheet_read``'s ``max_chars`` cannot exceed the per-result cap.

    In the live session the model reacted to a truncated read by raising ``max_chars``
    to 80,000. The read then returned ~80k characters and the wire cap cut it to 20,053
    — so "give me more" reliably produced a broken result instead of more data.
    """

    @staticmethod
    def _rows(count: int, cell_chars: int) -> list[list[str]]:
        return [[f"r{index}" + "y" * cell_chars] for index in range(count)]

    @pytest.fixture
    def served(self, monkeypatch: pytest.MonkeyPatch):
        def install(rows: list[list[str]]) -> None:
            async def fake_invoke(_req: Any, **_kw: Any) -> dict[str, Any]:
                return {"ok": True, "data": {"valueRange": {"range": "S!A1:C99", "values": rows}}}

            monkeypatch.setattr(_impl, "_invoke", fake_invoke)

        return install

    def _read(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("token", "tok")
        kwargs.setdefault("range_", "S!A1:C99")
        return anyio.run(lambda: _sheet.read_sheet_range_impl(**kwargs))

    def test_budget_above_the_cap_is_clamped(self, served: Any) -> None:
        served(self._rows(60, 2000))
        result = self._read(max_chars=80_000)

        assert result["truncated"] is True
        assert result["max_chars_effective"] < 80_000

    def test_warning_quotes_the_effective_budget_not_the_request(self, served: Any) -> None:
        """Echoing 80000 back would advertise a bigger number as the way out. It isn't."""
        served(self._rows(60, 2000))
        result = self._read(max_chars=80_000)

        assert "80000" not in result["warning"]
        assert str(result["max_chars_effective"]) in result["warning"]
        assert "will NOT help" in result["warning"]

    def test_zero_means_the_cap_rather_than_unlimited(self, served: Any) -> None:
        served(self._rows(60, 2000))
        result = self._read(max_chars=0)

        assert result["truncated"] is True, "0 used to disable the limit and blow the wire cap"

    def test_clamped_result_survives_the_wire_cap(self, served: Any) -> None:
        served(self._rows(60, 2000))
        payload = _impl.dumps_result(_sheet._label_grid(self._read(max_chars=0)))

        assert truncate_tool_result(payload) == payload
        assert json.loads(payload)["truncated"] is True

    def test_a_budget_under_the_cap_is_respected_as_given(self, served: Any) -> None:
        served(self._rows(60, 2000))
        result = self._read(max_chars=5000)

        assert result["max_chars_effective"] == 5000

    def test_untruncated_read_reports_no_effective_budget(self, served: Any) -> None:
        served(self._rows(2, 10))
        result = self._read(max_chars=0)

        assert result["truncated"] is False
        assert "max_chars_effective" not in result
