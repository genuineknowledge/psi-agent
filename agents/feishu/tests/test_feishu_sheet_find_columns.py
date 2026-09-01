"""``feishu_sheet_find_columns`` returns coordinates, not opinions about meaning.

The tool used to classify every header off a hardcoded substring table —
``负责人``/``姓名``/``名字``/``owner`` became ``kind: "names"``, anything containing
``mentor`` became ``kind: "mentor"``, everything else ``other``. Both halves of that
misfired in a live session where "谁的 mentor 是谁" came back with the two roles
swapped:

* a Chinese mentor column (导师 / 带教 / 师父) matched no marker and came back
  ``other``. That is not a neutral "don't know" — it reads as *there is no mentor
  column on this sheet*, and sends the model back to counting header cells, the one
  mistake the tool was added to prevent;
* a header naming two roles (``带教负责人(mentor)``) was resolved by the table's
  **write order**: ``mentor`` sat first, so a 负责人 column was labelled the mentor
  column.

So the classification is gone and the arithmetic stays. What the tool still owes the
caller is exact coordinates: 26-base column letters, offset by where the returned
range actually starts, and dates normalized to ISO. Those are pinned here, together
with the two defects above as regressions — a header comes back **verbatim** and
carries no ``kind`` unless it is a date.
"""

from __future__ import annotations

import datetime
import importlib
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_sheet: Any = sys.modules["_feishu.sheet"]


@pytest.fixture
def header_row(monkeypatch: pytest.MonkeyPatch):
    """Serve one header row, echoing back the range Feishu would report.

    ``start_at`` is the column the served range starts at. It defaults to ``A``, but a
    real sheet answers a request with the range it actually filled, and the offset is
    the reason column letters are computed in code at all.
    """

    def install(cells: list[str], start_at: str = "A") -> None:
        async def fake_invoke(req: Any, **_kw: Any) -> dict[str, Any]:
            asked = req.paths.get("range", "")
            sheet_id = asked.split("!", 1)[0]
            row = asked.split("!", 1)[1].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").split(":")[0] or "1"
            served = f"{sheet_id}!{start_at}{row}:ZZ{row}"
            return {"ok": True, "data": {"valueRange": {"range": served, "values": [cells]}}}

        monkeypatch.setattr(_impl, "_invoke", fake_invoke)

    return install


def _find(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("token", "tok")
    kwargs.setdefault("range_", "S")
    return anyio.run(lambda: _sheet.find_sheet_columns_impl(**kwargs))


class TestHeadersComeBackVerbatim:
    def test_a_chinese_mentor_header_is_not_labelled_other(self, header_row: Any) -> None:
        """``other`` on 导师 reads as "no mentor column here" — that is what misled the model."""
        header_row(["tag", "负责人", "导师", "7.24"])
        columns = _find()["columns"]

        mentor = next(col for col in columns if col["header"] == "导师")
        assert mentor["col"] == "C"
        assert "kind" not in mentor, "a non-date header must not be classified at all"

    def test_a_header_naming_two_roles_is_not_decided_for_the_caller(self, header_row: Any) -> None:
        """``带教负责人(mentor)`` used to come back ``mentor`` purely because that rule was written first."""
        header_row(["带教负责人(mentor)"])
        (column,) = _find()["columns"]

        assert column["header"] == "带教负责人(mentor)"
        assert "kind" not in column

    @pytest.mark.parametrize("header", ["负责人", "姓名", "名字", "owner", "mentor", "导师", "带教", "师父", "备注"])
    def test_no_header_word_earns_a_kind(self, header_row: Any, header: str) -> None:
        header_row([header])
        (column,) = _find()["columns"]

        assert column == {"col": "A", "header": header}

    def test_empty_cells_are_skipped_without_shifting_the_letters(self, header_row: Any) -> None:
        """A blank header must not push its neighbours one column left."""
        header_row(["tag", "", "导师"])
        columns = _find()["columns"]

        assert [col["col"] for col in columns] == ["A", "C"]
        assert [col["header"] for col in columns] == ["tag", "导师"]


class TestDatesAreNormalizedNotInterpreted:
    THIS_YEAR = datetime.datetime.now().year

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("7.24", f"{THIS_YEAR}-07-24"),
            ("8.10日", f"{THIS_YEAR}-08-10"),
            ("2026-07-24", "2026-07-24"),
            ("2026/7/24", "2026-07-24"),
            # 分隔符是 / 而不是 . 的无年份写法。原先按「模式 → 格式串」配对解析,这两个
            # 配到的格式串是 %m-%d,分隔符对不上就抛 ValueError,兜底成 1900-01-01,再被
            # 补成「今年 1 月 1 日」—— 日期列全都指向 1.1,而且看不出错。
            ("7/24", f"{THIS_YEAR}-07-24"),
            ("8-14", f"{THIS_YEAR}-08-14"),
        ],
    )
    def test_cycle_columns_carry_an_iso_date(self, header_row: Any, header: str, expected: str) -> None:
        header_row([header])
        (column,) = _find()["columns"]

        assert column["kind"] == "date"
        assert column["date"] == expected

    def test_a_year_less_date_uses_the_current_year_not_1900(self, header_row: Any) -> None:
        """1900 sorts before everything and silently loses every date comparison."""
        header_row(["8.14"])
        (column,) = _find()["columns"]

        assert column["date"] == f"{self.THIS_YEAR}-08-14"

    def test_feb_29_is_resolved_against_the_current_year(self, header_row: Any) -> None:
        """``strptime`` defaults to 1900, which is not a leap year, so 2.29 hit the fallback.

        Whether 2.29 exists depends on the year, so the assertion does too: in a leap
        year it must resolve, otherwise it must come back unclassified rather than
        silently sliding to March 1st.
        """
        header_row(["2.29"])
        (column,) = _find()["columns"]

        leap = self.THIS_YEAR % 4 == 0 and (self.THIS_YEAR % 100 != 0 or self.THIS_YEAR % 400 == 0)
        if leap:
            assert column["date"] == f"{self.THIS_YEAR}-02-29"
        else:
            assert "date" not in column

    @pytest.mark.parametrize("header", ["2026", "13.32", "0.0", "8.10.14"])
    def test_a_number_that_is_not_a_date_stays_unclassified(self, header_row: Any, header: str) -> None:
        """Better no ``date`` at all than a plausible-looking wrong one."""
        header_row([header])
        (column,) = _find()["columns"]

        assert "kind" not in column
        assert "date" not in column


class TestColumnLettersAreArithmeticTheCallerCannotDo:
    def test_the_ranges_start_column_offsets_every_letter(self, header_row: Any) -> None:
        """A range served from B must not report its first cell as A — that is a one-column shift."""
        header_row(["负责人", "导师"], start_at="B")
        columns = _find()["columns"]

        assert [col["col"] for col in columns] == ["B", "C"]

    def test_the_letters_are_26_base_past_z(self, header_row: Any) -> None:
        header_row(["h"] * 28)
        columns = _find()["columns"]

        assert [col["col"] for col in columns[25:]] == ["Z", "AA", "AB"]

    def test_a_two_letter_start_column_is_parsed_as_a_whole(self, header_row: Any) -> None:
        header_row(["h", "h"], start_at="AA")
        columns = _find()["columns"]

        assert [col["col"] for col in columns] == ["AA", "AB"]


class TestTheSemanticRuleLivesInTheSkill:
    """Deleting the classification only helps if the rule it got wrong is written down.

    The tool no longer says which column is the mentor, so the thing that keeps 负责人
    and mentor from being swapped is the skill text. It is asserted rather than trusted
    to review: dropping code and dropping the guidance with it would leave the defect
    with nothing standing against it.
    """

    SKILL = Path(__file__).resolve().parents[1] / "skills" / "feishu-sheet" / "SKILL.md"

    def test_the_two_roles_are_distinguished(self) -> None:
        text = self.SKILL.read_text(encoding="utf-8")

        assert "负责人" in text and "mentor" in text
        assert "两个人" in text, "the skill must say 负责人 and mentor are different people"

    @pytest.mark.parametrize("synonym", ["导师", "带教", "师父"])
    def test_chinese_mentor_synonyms_are_listed(self, synonym: str) -> None:
        """These are exactly the headers the deleted table could not match."""
        assert synonym in self.SKILL.read_text(encoding="utf-8")
