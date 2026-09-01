"""``feishu_leave_query`` — the rules that must stay in code, not in a model's head.

This tool exists because date-interval overlap is pure logic: left to the model it is a
calendar computation redone every cycle, and one slip marks somebody on approved leave as
having skipped their TODO. The failure direction is "harsher assessment", and it does not
surface on its own — the person who was wronged has to come back and appeal. So the tests
below pin the decisions rather than the plumbing:

* both the query window and each leave interval are **closed** (both endpoints count);
* **only approved** applications count — pending ones must not silently become leave;
* an application whose dates cannot be read lands in ``needs_fix`` and is **not** dropped,
  because dropping it turns "did file leave" into "did not";
* a blank end date is one day of leave, not an open-ended one;
* no-year date forms (``8.19``) resolve against the current year, and 2.29 must not blow
  up on the way through (``strptime`` defaults to 1900, a non-leap year).

``leave.py`` reaches the shared client layer through ``_core.<name>`` attribute access
rather than importing ``_invoke`` by value, which is what lets these tests patch
``_feishu_impl._invoke`` and have it take effect inside the submodule.
"""

from __future__ import annotations

import datetime
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_leave: Any = importlib.import_module("_feishu.leave")

APPROVAL_CODE = "99EEC396-536A-4C7A-8B2D-412584E35CE3"


def _instance(applicant: str, start: str, end: str, *, status: Any = "APPROVED") -> dict[str, Any]:
    """A minimal approval-instance detail, shaped the way the real endpoint returns one."""
    return {
        "ok": True,
        "status": status,
        "applicant": applicant,
        "form": json.dumps(
            [
                {"name": "开始日期", "value": start},
                {"name": "结束日期", "value": end},
                {"name": "假别", "value": "年假"},
            ],
            ensure_ascii=False,
        ),
    }


@pytest.fixture
def fake_feishu(monkeypatch: pytest.MonkeyPatch):
    """Serve a fixed instance list plus per-code details, without touching the network.

    ``names`` is the contact book: applicant **user_id** -> display name. It is stubbed for
    every test because the tool resolves applicant names unconditionally, so leaving it out
    would let the tests reach the network. Pass ``names_result`` to make the lookup fail.

    The stub rejects a lookup that asks for the wrong ``user_id_type``, the way Feishu does
    (99992351 "not a valid open_id"). That matters: an echo-anything stub passes whichever
    id type the code sends, which is exactly how the ``open_id`` version of this lookup got
    green tests while resolving nothing at all against the live tenant.
    """

    def install(
        details: dict[str, dict[str, Any]],
        names: dict[str, str] | None = None,
        names_result: dict[str, Any] | None = None,
    ) -> None:
        async def fake_invoke(_req: Any, **_kw: Any) -> dict[str, Any]:
            return {"ok": True, "data": {"instance_code_list": list(details), "has_more": False}}

        async def fake_detail(instance_code: str, *_a: Any, **_kw: Any) -> dict[str, Any]:
            return details[instance_code]

        async def fake_batch(user_ids: str, user_id_type: str = "open_id", **_kw: Any) -> dict[str, Any]:
            if names_result is not None:
                return names_result
            book = names or {}
            asked = [uid.strip() for uid in user_ids.split(",") if uid.strip()]
            if user_id_type != "user_id":
                return {
                    "ok": False,
                    "error": f"Feishu API error 99992351: not a valid {{open_id}}. Invalid ids: {asked}",
                }
            return {
                "ok": True,
                "users": [
                    {"user_id": uid, "open_id": f"ou_for_{uid}", "name": book[uid]} for uid in asked if uid in book
                ],
            }

        monkeypatch.setattr(_impl, "_invoke", fake_invoke)
        monkeypatch.setattr(_impl, "get_approval_instance_impl", fake_detail)
        monkeypatch.setattr(_impl, "get_users_batch_impl", fake_batch)

    return install


def _query(**kwargs: Any) -> dict[str, Any]:
    return anyio.run(lambda: _leave.query_leave_impl(approval_code=APPROVAL_CODE, **kwargs))


class TestOverlapIsClosed:
    """Both endpoints of both intervals count as leave."""

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            # Leave touching only the first day of the window still counts.
            ("2026-08-15", "2026-08-19", ["2026-08-19"]),
            # ... and only the last.
            ("2026-08-26", "2026-09-02", ["2026-08-26"]),
            # Fully inside.
            ("2026-08-20", "2026-08-21", ["2026-08-20", "2026-08-21"]),
        ],
    )
    def test_endpoints_count(self, start: str, end: str, expected: list[str]) -> None:
        got = _leave._overlap_days(
            datetime.date.fromisoformat(start),
            datetime.date.fromisoformat(end),
            datetime.date(2026, 8, 19),
            datetime.date(2026, 8, 26),
        )
        assert got == expected

    def test_no_overlap_is_empty_not_an_error(self) -> None:
        assert (
            _leave._overlap_days(
                datetime.date(2026, 7, 1),
                datetime.date(2026, 7, 5),
                datetime.date(2026, 8, 19),
                datetime.date(2026, 8, 26),
            )
            == []
        )


class TestDateForms:
    """The approval form is human-configured, so a tolerant set of forms is parsed."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-08-19", datetime.date(2026, 8, 19)),
            ("2026/08/19", datetime.date(2026, 8, 19)),
            ("2026.08.19", datetime.date(2026, 8, 19)),
            # Datetime forms keep only the date half.
            ("2026-08-19 10:00", datetime.date(2026, 8, 19)),
            ("2026-08-19T10:00:00+08:00", datetime.date(2026, 8, 19)),
        ],
    )
    def test_parsed(self, raw: str, expected: datetime.date) -> None:
        assert _leave._parse_date(raw) == expected

    def test_no_year_form_uses_current_year(self) -> None:
        """``8.19`` in a board header must land in this year, not 1900."""
        assert _leave._parse_date("8.19") == datetime.date(datetime.date.today().year, 8, 19)

    def test_leap_day_survives_the_no_year_path(self) -> None:
        """2.29 parses only because the year is prepended *before* strptime, not after.

        Resolving it as 1900-02-29 and then replacing the year would raise, since 1900 is
        not a leap year — the bug this asserts against.
        """
        year = datetime.date.today().year
        expected = datetime.date(year, 2, 29) if year % 4 == 0 else None
        assert _leave._parse_date("2.29") == expected

    def test_unreadable_returns_none_rather_than_guessing(self) -> None:
        assert _leave._parse_date("下周一") is None
        assert _leave._parse_date("") is None


class TestLeaveGroupWidget:
    """The real 假勤 template puts the whole request in ONE widget, named 「说明」.

    Probed against the live tenant: the form is a single ``leaveGroupV2`` widget whose
    ``value`` carries ``start`` / ``end`` / ``name`` / ``reason``. Matching Chinese widget
    labels for 「开始日期」/「结束日期」 finds nothing there, so every application would land
    in ``needs_fix`` — "leave was checked and nobody was on leave", which is the
    harsher-assessment direction and looks like a working feature. Dates therefore have to
    be recognised from the widget **type**, not from naming convention.
    """

    LEAVE_GROUP_FORM = json.dumps(
        [
            {
                "id": "w1",
                "name": "说明",  # the label really is this — it must not win over the type
                "type": "leaveGroupV2",
                "ext": None,
                "value": {
                    "start": "2026-08-19T00:00:00+08:00",
                    "end": "2026-08-21T00:00:00+08:00",
                    "interval": 3,
                    "name": "年假",
                    "reason": "回家",
                    "unit": "DAY",
                    "timezoneOffset": -480,
                },
            }
        ],
        ensure_ascii=False,
    )

    def test_dates_come_from_the_widget_type_not_its_label(self) -> None:
        start, end, kind, reason = _leave._leave_span(self.LEAVE_GROUP_FORM)
        assert _leave._parse_date(start) == datetime.date(2026, 8, 19)
        assert _leave._parse_date(end) == datetime.date(2026, 8, 21)
        # 假别/事由 also live inside the same widget rather than in separate ones.
        assert kind == "年假"
        assert reason == "回家"

    def test_end_to_end_over_the_real_form_shape(self, fake_feishu: Any) -> None:
        fake_feishu({"i1": {"ok": True, "status": "APPROVED", "applicant": "ou_a", "form": self.LEAVE_GROUP_FORM}})
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert result["needs_fix"] == [], "the live form shape must not be unreadable"
        assert result["on_leave"][0]["hit_dates"] == ["2026-08-19", "2026-08-20", "2026-08-21"]

    def test_named_widgets_still_work(self) -> None:
        """The older shape must keep parsing — the type path is additive, not a replacement."""
        form = json.dumps(
            [{"name": "开始日期", "value": "2026-08-19"}, {"name": "结束日期", "value": "2026-08-21"}],
            ensure_ascii=False,
        )
        start, end, _kind, _reason = _leave._leave_span(form)
        assert (start, end) == ("2026-08-19", "2026-08-21")


class TestOnlyApprovedCounts:
    def test_approved_string_and_numeric_both_count(self, fake_feishu: Any) -> None:
        """Detail status is a string enum; listings carry a numeric 2. Both mean approved."""
        fake_feishu(
            {
                "i1": _instance("ou_a", "2026-08-19", "2026-08-19"),
                "i2": _instance("ou_b", "2026-08-20", "2026-08-20", status=2),
            }
        )
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert result["ok"] is True
        assert result["on_leave_applicants"] == ["ou_a", "ou_b"]

    def test_pending_is_not_leave_and_is_reported(self, fake_feishu: Any) -> None:
        """A pending application must not read as leave — that person is still at work.

        It also must not vanish: a pile of pending requests has to be visible, otherwise it
        looks like nobody asked for leave at all.
        """
        fake_feishu({"i1": _instance("ou_a", "2026-08-19", "2026-08-21", status="PENDING")})
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert result["on_leave_applicants"] == []
        assert result["skipped_not_approved"], "pending applications must be surfaced"

    @pytest.mark.parametrize("status", ["REJECTED", "CANCELED", "DELETED"])
    def test_rejected_and_revoked_never_count(self, fake_feishu: Any, status: str) -> None:
        fake_feishu({"i1": _instance("ou_a", "2026-08-19", "2026-08-21", status=status)})
        assert _query(date_from="2026-08-19", date_to="2026-08-26")["on_leave_applicants"] == []


class TestUnreadableDatesAreNotDropped:
    def test_unparseable_dates_land_in_needs_fix(self, fake_feishu: Any) -> None:
        """Dropping these would turn "did file leave" into "did not" — the wrong direction."""
        fake_feishu({"i1": _instance("ou_a", "下周一", "说不好")})
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert result["on_leave_applicants"] == []
        assert result["needs_fix"], "an application a human must look at must be reported"

    def test_blank_end_date_is_one_day(self, fake_feishu: Any) -> None:
        fake_feishu({"i1": _instance("ou_a", "2026-08-19", "")})
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert result["on_leave"][0]["hit_dates"] == ["2026-08-19"]


class TestWindowAndShape:
    def test_full_period_applicants_are_flagged(self, fake_feishu: Any) -> None:
        """People away for the whole window get skipped wholesale when dispatching."""
        fake_feishu({"i1": _instance("ou_a", "2026-08-01", "2026-09-01")})
        result = _query(date_from="2026-08-19", date_to="2026-08-21")
        assert result["full_period_applicants"] == ["ou_a"]
        assert result["on_leave"][0]["hit_days"] == 3

    def test_blank_date_to_means_a_single_day(self, fake_feishu: Any) -> None:
        fake_feishu({"i1": _instance("ou_a", "2026-08-19", "2026-08-25")})
        result = _query(date_from="2026-08-19")
        assert result["date_from"] == result["date_to"] == "2026-08-19"
        assert result["span_days"] == 1

    def test_reversed_window_is_rejected(self, fake_feishu: Any) -> None:
        fake_feishu({})
        assert _query(date_from="2026-08-26", date_to="2026-08-19")["ok"] is False

    def test_names_filter_keeps_only_the_asked_for_ids(self, fake_feishu: Any) -> None:
        fake_feishu(
            {
                "i1": _instance("ou_a", "2026-08-19", "2026-08-19"),
                "i2": _instance("ou_b", "2026-08-20", "2026-08-20"),
            }
        )
        result = _query(date_from="2026-08-19", date_to="2026-08-26", names_json='["ou_b"]')
        assert result["on_leave_applicants"] == ["ou_b"]

    def test_names_filter_accepts_display_names(self, fake_feishu: Any) -> None:
        """A filter written as Chinese names must work — the parameter is called ``names``.

        The regression this pins: the filter used to be a bare set-membership test against
        the applicant ids, so a list of names matched nobody and the empty result was
        indistinguishable from "nobody was on leave". Live, that judged three people on
        approved leave as having skipped their TODO.

        The applicant ids here are the shape the live tenant really returns (8-char
        ``user_id``, from ``data.user_id`` in the instance detail) — not open_ids.
        """
        fake_feishu(
            {
                "i1": _instance("dg429f6d", "2026-08-03", "2026-09-01"),
                "i2": _instance("9be7b262", "2026-08-11", "2026-08-28"),
                "i3": _instance("g7e36a55", "2026-08-20", "2026-08-20"),
            },
            names={"dg429f6d": "潘逸轩", "9be7b262": "董修奇", "g7e36a55": "马晨柯"},
        )
        result = _query(date_from="2026-08-26", date_to="2026-08-26", names_json='["潘逸轩","董修奇"]')
        assert result["on_leave_applicants"] == ["9be7b262", "dg429f6d"]
        assert result["on_leave_names"] == ["董修奇", "潘逸轩"]
        # Both cover the single-day window end to end, so dispatch skips them entirely.
        assert result["full_period_applicants"] == ["9be7b262", "dg429f6d"]
        assert result["unmatched_filter"] == []

    def test_names_are_looked_up_by_user_id_not_open_id(self, fake_feishu: Any) -> None:
        """The applicant is a ``user_id``; asking the contact book for an open_id resolves nothing.

        Probed against the live tenant: ``users/batch`` with ``user_id_type=open_id`` and an
        applicant like ``dg429f6d`` returns error 99992351 "not a valid {open_id}", so every
        name stays unresolved and a name filter matches nobody — the harsher-assessment
        direction, and it looks exactly like a working feature.
        """
        asked: list[str] = []
        fake_feishu({"i1": _instance("dg429f6d", "2026-08-19", "2026-08-19")}, names={"dg429f6d": "潘逸轩"})
        inner = _impl.get_users_batch_impl

        async def spy(user_ids: str, user_id_type: str = "open_id", **kw: Any) -> dict[str, Any]:
            asked.append(user_id_type)
            return await inner(user_ids, user_id_type=user_id_type, **kw)

        _impl.get_users_batch_impl = spy
        try:
            result = _query(date_from="2026-08-19", date_to="2026-08-26", names_json='["潘逸轩"]')
        finally:
            _impl.get_users_batch_impl = inner
        assert asked == ["user_id"], "the applicant id is a user_id, so the lookup must say so"
        assert result["on_leave_applicants"] == ["dg429f6d"]
        assert result["name_lookup_error"] == ""

    def test_names_and_ids_can_be_mixed(self, fake_feishu: Any) -> None:
        fake_feishu(
            {
                "i1": _instance("ou_a", "2026-08-19", "2026-08-19"),
                "i2": _instance("ou_b", "2026-08-20", "2026-08-20"),
            },
            names={"ou_a": "潘逸轩", "ou_b": "董修奇"},
        )
        result = _query(date_from="2026-08-19", date_to="2026-08-26", names_json='["ou_a","董修奇"]')
        assert result["on_leave_applicants"] == ["ou_a", "ou_b"]

    def test_a_filter_entry_that_matches_nobody_is_reported(self, fake_feishu: Any) -> None:
        """``unmatched_filter`` is what separates "was at work" from "never matched".

        An empty ``on_leave`` on its own cannot: both a person who really was at work and a
        spelling that failed to resolve produce exactly the same empty list, and reading it
        as the former books overdue todos against people who were away.
        """
        fake_feishu(
            {"i1": _instance("ou_a", "2026-08-19", "2026-08-19")},
            names={"ou_a": "潘逸轩"},
        )
        result = _query(date_from="2026-08-19", date_to="2026-08-26", names_json='["潘逸轩","高博"]')
        assert result["on_leave_applicants"] == ["ou_a"]
        assert result["unmatched_filter"] == ["高博"]

    def test_name_lookup_failure_is_surfaced_not_swallowed(self, fake_feishu: Any) -> None:
        """If the contact book is unreachable, every name filter matches nothing.

        That failure must be visible, otherwise it looks like a confident "nobody was on
        leave" — the harsher-assessment direction again.
        """
        fake_feishu(
            {"i1": _instance("ou_a", "2026-08-19", "2026-08-19")},
            names_result={"ok": False, "error": "no contact scope"},
        )
        result = _query(date_from="2026-08-19", date_to="2026-08-26", names_json='["潘逸轩"]')
        assert result["on_leave_applicants"] == []
        assert result["unmatched_filter"] == ["潘逸轩"]
        assert "no contact scope" in result["name_lookup_error"]

    def test_ids_still_filter_when_the_name_lookup_fails(self, fake_feishu: Any) -> None:
        """Name resolution is additive: an id filter must not start depending on it."""
        fake_feishu(
            {
                "i1": _instance("ou_a", "2026-08-19", "2026-08-19"),
                "i2": _instance("ou_b", "2026-08-20", "2026-08-20"),
            },
            names_result={"ok": False, "error": "no contact scope"},
        )
        result = _query(date_from="2026-08-19", date_to="2026-08-26", names_json='["ou_b"]')
        assert result["on_leave_applicants"] == ["ou_b"]

    def test_names_resolve_even_without_a_filter(self, fake_feishu: Any) -> None:
        """Otherwise the caller either re-queries or reads ids out loud as if they were names."""
        fake_feishu({"i1": _instance("ou_a", "2026-08-19", "2026-08-19")}, names={"ou_a": "潘逸轩"})
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert result["on_leave"][0]["name"] == "潘逸轩"
        assert result["unmatched_filter"] == []

    def test_name_lookup_is_batched_at_fifty(self, fake_feishu: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Feishu rejects more than 50 ids per call, so a big cycle must not be one request."""
        details = {f"i{n}": _instance(f"ou_{n:03d}", "2026-08-19", "2026-08-19") for n in range(120)}
        fake_feishu(details, names={f"ou_{n:03d}": f"人{n}" for n in range(120)})
        batches: list[int] = []
        inner = _impl.get_users_batch_impl

        async def counting(user_ids: str, **kw: Any) -> dict[str, Any]:
            batches.append(len([u for u in user_ids.split(",") if u.strip()]))
            return await inner(user_ids, **kw)

        monkeypatch.setattr(_impl, "get_users_batch_impl", counting)
        result = _query(date_from="2026-08-19", date_to="2026-08-26")
        assert max(batches) <= 50
        assert len(result["on_leave_names"]) == 120

    def test_listing_window_is_widened_beyond_the_asked_for_dates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The endpoint filters by **submission** time, not by the leave dates themselves.

        Leave filed late last month for this week would fall outside a listing window equal
        to the query window, and the person would come back as not-on-leave — a silent
        under-count in the "harsher assessment" direction. So the listing is widened and the
        real decision is the interval intersection further down. Pinning this keeps the
        margin from being "simplified" away later.
        """
        seen: dict[str, int] = {}

        async def capture(req: Any, **_kw: Any) -> dict[str, Any]:
            for key, value in req.queries:
                if key in {"start_time", "end_time"}:
                    seen[key] = int(value)
            return {"ok": True, "data": {"instance_code_list": [], "has_more": False}}

        monkeypatch.setattr(_impl, "_invoke", capture)
        _query(date_from="2026-08-19", date_to="2026-08-26")

        window_start = datetime.datetime.fromtimestamp(seen["start_time"] / 1000).date()
        window_end = datetime.datetime.fromtimestamp(seen["end_time"] / 1000).date()
        assert window_start < datetime.date(2026, 8, 19), "listing must reach back before the window"
        assert window_end > datetime.date(2026, 8, 26), "listing must reach past the window"

    def test_multiple_leaves_for_one_person_all_land(self, fake_feishu: Any) -> None:
        """A person can have several intervals; reading only the first understates leave."""
        fake_feishu(
            {
                "i1": _instance("ou_a", "2026-08-19", "2026-08-19"),
                "i2": _instance("ou_a", "2026-08-24", "2026-08-25"),
            }
        )
        person = _query(date_from="2026-08-19", date_to="2026-08-26")["on_leave"][0]
        assert len(person["leaves"]) == 2
        assert person["hit_dates"] == ["2026-08-19", "2026-08-24", "2026-08-25"]
