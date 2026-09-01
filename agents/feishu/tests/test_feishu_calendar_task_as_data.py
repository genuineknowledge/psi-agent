"""Parity tests for the calendar and task domains moving into endpoint tables.

The calendar half is a *replacement*: three tools (`feishu_calendar_create_event`,
`_list_events`, `_create_per_person`) are deleted and their endpoints become rows in
``skills/feishu-calendar/SKILL.md``. The wire shapes in :data:`WAS` were captured by
running those builders before deleting them, so what is compared here is the generic
path against what actually went out — not a fresh guess at what it used to do.

The task half is an *addition*: nothing is deleted, so there is no parity to check.
What is tested instead is that each new row is reachable, carries the constraint that
makes it safe, and does not collide with a neighbouring endpoint — Feishu hangs
``/tasks/search`` and ``/tasks/:task_guid`` under the same collection, and a
``required: [summary]`` leaking onto the search endpoint would make it unreachable.

Two behaviours deliberately change, and both are repairs:

* **The per-person loop is gone.** ``create_events_per_person_impl`` looped over
  open_ids issuing two calls each. A table row cannot loop, and this one does not need
  to: the loop was never the hard part, and it hid partial failure behind an aggregate
  ``ok``. The caller now issues the calls and reports per-person outcomes, which is
  what the skill's prose specifies.
* **`timezone` stops lying.** The deleted builder parsed ``'YYYY-MM-DD HH:MM'`` with
  ``datetime.strptime(...).timestamp()`` — a *naive* datetime, resolved against the
  host clock — then attached the caller's ``timezone`` string to the result. So
  ``timezone="America/New_York"`` produced exactly the same epoch as
  ``Asia/Shanghai``: a meeting booked for a New York colleague landed 12 hours off,
  and the call returned success. :func:`test_deleted_builder_ignored_its_timezone_arg`
  pins that this was real, and the skill now tells the caller to compute the epoch for
  the target zone itself.
"""

from __future__ import annotations

import datetime
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from lark_channel.core.enum import HttpMethod
from lark_channel.core.model import BaseRequest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_spec: Any = importlib.import_module("_feishu_spec")
_api: Any = importlib.import_module("_feishu_api_impl")
_impl: Any = importlib.import_module("_feishu_impl")

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
CALENDAR_SKILL = SKILLS_DIR / "feishu-calendar" / "SKILL.md"
TASK_SKILL = SKILLS_DIR / "feishu-task" / "SKILL.md"

#: Both token types are declared on every endpoint; ``prefer`` picks the send path.
WAS_TOKENS = {"TENANT", "USER"}

#: The wire shapes the deleted calendar builders produced, frozen mechanically by
#: running them before deletion. Epochs are the ones those builders computed on a
#: UTC+8 host for the literal times below, so they double as a record of the naive-
#: timestamp behaviour documented in the module docstring.
WAS: dict[str, dict[str, Any]] = {
    "primary_calendar": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/calendar/v4/calendars/primary",
        "paths": {},
        "queries": [("user_id_type", "open_id")],
        "body": None,
    },
    "create_timed": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events",
        "paths": {"calendar_id": "cal_primary"},
        "queries": [],
        "body": {
            "summary": "周会",
            "start_time": {"timestamp": "1786341600", "timezone": "Asia/Shanghai"},
            "end_time": {"timestamp": "1786345200", "timezone": "Asia/Shanghai"},
            "description": "议题x",
        },
    },
    "create_allday": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events",
        "paths": {"calendar_id": "cal_primary"},
        "queries": [],
        "body": {
            "summary": "全天",
            "start_time": {"date": "2026-08-10", "timezone": "Asia/Shanghai"},
            "end_time": {"date": "2026-08-11", "timezone": "Asia/Shanghai"},
        },
    },
    "add_attendees": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees",
        "paths": {"calendar_id": "cal_primary", "event_id": "ev_1"},
        "queries": [("user_id_type", "open_id")],
        "body": {"attendees": [{"type": "user", "user_id": "ou_a"}, {"type": "user", "user_id": "ou_b"}]},
    },
    "list_events": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events",
        "paths": {"calendar_id": "cal_x"},
        "queries": [
            ("end_time", "1786356000"),
            ("page_size", "50"),
            ("start_time", "1786323600"),
            ("user_id_type", "open_id"),
        ],
        "body": None,
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    "primary_calendar": {
        "method": "POST",
        "uri": "/open-apis/calendar/v4/calendars/primary",
    },
    "create_timed": {
        "method": "POST",
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events",
        "paths": {"calendar_id": "cal_primary"},
        "body": {
            "summary": "周会",
            "start_time": {"timestamp": "1786341600", "timezone": "Asia/Shanghai"},
            "end_time": {"timestamp": "1786345200", "timezone": "Asia/Shanghai"},
            "description": "议题x",
        },
    },
    "create_allday": {
        "method": "POST",
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events",
        "paths": {"calendar_id": "cal_primary"},
        "body": {
            "summary": "全天",
            "start_time": {"date": "2026-08-10", "timezone": "Asia/Shanghai"},
            "end_time": {"date": "2026-08-11", "timezone": "Asia/Shanghai"},
        },
    },
    "add_attendees": {
        "method": "POST",
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees",
        "paths": {"calendar_id": "cal_primary", "event_id": "ev_1"},
        "body": {"attendees": [{"type": "user", "user_id": "ou_a"}, {"type": "user", "user_id": "ou_b"}]},
    },
    "list_events": {
        "method": "GET",
        "uri": "/open-apis/calendar/v4/calendars/:calendar_id/events",
        "paths": {"calendar_id": "cal_x"},
        "query": {"start_time": "1786323600", "end_time": "1786356000", "page_size": 50},
    },
}

#: Endpoints the calendar table must declare, with the tool each replaced ("" = new).
CALENDAR_MIGRATED = [
    ("POST", "/open-apis/calendar/v4/calendars/primary", "feishu_calendar_create_event"),
    ("POST", "/open-apis/calendar/v4/calendars/:calendar_id/events", "feishu_calendar_create_event"),
    (
        "POST",
        "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees",
        "feishu_calendar_create_event",
    ),
    ("GET", "/open-apis/calendar/v4/calendars/:calendar_id/events", "feishu_calendar_list_events"),
]

#: The xlsx gaps this change closes, as (skill, method, uri). Every row must exist.
GAP_ENDPOINTS = [
    # 日历 49 更新/删除日程 — "现建了删不掉"
    (CALENDAR_SKILL, "PATCH", "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id"),
    (CALENDAR_SKILL, "DELETE", "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id"),
    # 日历 50 日程详情
    (CALENDAR_SKILL, "GET", "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id"),
    # 日历 51 日历列表/新建/订阅
    (CALENDAR_SKILL, "GET", "/open-apis/calendar/v4/calendars"),
    (CALENDAR_SKILL, "POST", "/open-apis/calendar/v4/calendars"),
    (CALENDAR_SKILL, "POST", "/open-apis/calendar/v4/calendars/:calendar_id/subscribe"),
    # 日历 52 忙闲查询
    (CALENDAR_SKILL, "POST", "/open-apis/calendar/v4/freebusy/list"),
    # 日历 53 重复规则/提醒/参会人
    (CALENDAR_SKILL, "GET", "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/instances"),
    (CALENDAR_SKILL, "GET", "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees"),
    (
        CALENDAR_SKILL,
        "POST",
        "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees/batch_delete",
    ),
    # 任务 54 删除任务
    (TASK_SKILL, "DELETE", "/open-apis/task/v2/tasks/:task_guid"),
    # 任务 55 子任务/评论
    (TASK_SKILL, "POST", "/open-apis/task/v2/tasks/:task_guid/subtasks"),
    (TASK_SKILL, "GET", "/open-apis/task/v2/tasks/:task_guid/subtasks"),
    (TASK_SKILL, "POST", "/open-apis/task/v2/comments"),
    (TASK_SKILL, "GET", "/open-apis/task/v2/comments"),
    # 任务 56 成员/关注人管理 — "建时指定后改不了"
    (TASK_SKILL, "POST", "/open-apis/task/v2/tasks/:task_guid/add_members"),
    (TASK_SKILL, "POST", "/open-apis/task/v2/tasks/:task_guid/remove_members"),
    # 任务 57 清单管理
    (TASK_SKILL, "POST", "/open-apis/task/v2/tasklists"),
    (TASK_SKILL, "GET", "/open-apis/task/v2/tasklists"),
    (TASK_SKILL, "PATCH", "/open-apis/task/v2/tasklists/:tasklist_guid"),
    (TASK_SKILL, "DELETE", "/open-apis/task/v2/tasklists/:tasklist_guid"),
    (TASK_SKILL, "GET", "/open-apis/task/v2/tasklists/:tasklist_guid/tasks"),
    (TASK_SKILL, "POST", "/open-apis/task/v2/tasks/:task_guid/add_tasklist"),
    # 任务 58 查任意用户任务 — "现仅查机器人自己的"
    (TASK_SKILL, "POST", "/open-apis/task/v2/tasks/search"),
]

#: Which reads page, and under which response key. The calendar *list* is the outlier.
PAGED = [
    ("GET", "/open-apis/calendar/v4/calendars", "calendar_list"),
    ("GET", "/open-apis/calendar/v4/calendars/:calendar_id/events", "items"),
    ("GET", "/open-apis/task/v2/tasklists", "items"),
    ("GET", "/open-apis/task/v2/comments", "items"),
]

#: Facts that live only in prose. If a rewrite drops one, the model loses it silently.
CALENDAR_PITFALL_FACTS = [
    "calendar_list",  # the list endpoint's items key is not "items"
    "free_busy_reader",  # the role that explains a read that returns nothing
    "194004",  # attendee id key mismatch
    "190009",  # sync_token mixed with a range query
    "193002",  # not the organizer
    "191004",  # wrong calendar type
    "attendee_id",  # deleting attendees needs it, not open_id
    "freebusy_list",
    "90 天",  # freebusy range cap
    "RFC 3339",  # freebusy time format, unlike everything else in the domain
    "idempotency_key",
]

TASK_PITFALL_FACTS = [
    "1470400",
    "1470403",
    "1470422",
    "1470612",
    "assignee_related",
    "origin_owner_to_role",
    "add_members",
    "resource_id",
    "subtask_count",
]


#: Queries the table adds that the deleted builder did not send, and why.
#:
#: Feishu's own default for ``user_id_type`` is documented as ``open_id`` on these
#: endpoints, so pinning it changes no behaviour — but ``feishu-api``'s table warns that
#: an *unsent* ``user_id_type`` "默认可能不是 open_id", and every other rule in the repo
#: declares it for that reason. The deleted create-event builder omitted it on the create
#: call while sending it on the attendee call two lines later, which is the inconsistency
#: being removed rather than preserved.
DELIBERATE_EXTRA_QUERY: dict[str, list[tuple[str, str]]] = {
    "create_timed": [("user_id_type", "open_id")],
    "create_allday": [("user_id_type", "open_id")],
}


def _sent(req: BaseRequest) -> dict[str, Any]:
    """Normalize a live request so it can be compared with a frozen shape."""
    return {
        "method": req.http_method,
        "uri": req.uri,
        "paths": dict(req.paths or {}),
        "queries": sorted((k, str(v)) for k, v in (req.queries or [])),
        "body": req.body,
    }


def _want(label: str) -> dict[str, Any]:
    """One frozen wire shape, normalized the same way ``_sent`` normalizes a live one."""
    want = dict(WAS[label])
    want["queries"] = sorted([*want["queries"], *DELIBERATE_EXTRA_QUERY.get(label, [])])
    return want


class _CapturedInvoke:
    """Stands in for ``_invoke`` and keeps the request instead of sending it."""

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[BaseRequest] = []
        self.kwargs: list[dict[str, Any]] = []
        self._pages = pages or [{"ok": True, "data": {}}]

    async def __call__(self, request: BaseRequest, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        return self._pages[min(len(self.requests) - 1, len(self._pages) - 1)]


@pytest.fixture(autouse=True)
def _real_skills(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Drive the generic path from the shipped skill files, not a synthetic fixture."""
    _spec.reset_cache()
    monkeypatch.setattr(_api, "_skills_dir", lambda: str(SKILLS_DIR))
    yield
    _spec.reset_cache()


async def _generic(
    monkeypatch: pytest.MonkeyPatch,
    pages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    cap = _CapturedInvoke(pages)
    monkeypatch.setattr(_impl, "_invoke", cap)
    return cap, await _api.call_api_impl(**kwargs)


def _pages_for(uri: str, method: str) -> list[dict[str, Any]] | None:
    """A single terminal page, under whichever key this endpoint's rule declares."""
    for pg_method, pg_uri, key in PAGED:
        if pg_uri == uri and pg_method == method:
            return [{"ok": True, "data": {key: [], "has_more": False}}]
    return None


async def _call(
    monkeypatch: pytest.MonkeyPatch, label: str, **overrides: Any
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    """Invoke one ``CALLS`` entry through the generic path."""
    spec = {**CALLS[label], **overrides}
    return await _generic(
        monkeypatch,
        pages=_pages_for(spec["uri"], spec["method"]),
        method=spec["method"],
        uri=spec["uri"],
        paths_json=json.dumps(spec.get("paths", {})),
        body_json=json.dumps(spec.get("body", {}), ensure_ascii=False),
        query_json=json.dumps(spec.get("query", {})),
        confirm=spec.get("confirm", ""),
    )


def _rules(skill: Path) -> list[Any]:
    return _spec.parse_rules(skill.read_text(encoding="utf-8"))


def _rule(skill: Path, method: str, uri: str) -> Any:
    match = [r for r in _rules(skill) if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skills parse


def test_calendar_skill_declares_every_migrated_endpoint() -> None:
    """A deleted tool whose endpoint never made it into the table is a lost capability."""
    declared = {(r.method, r.uri) for r in _rules(CALENDAR_SKILL)}
    for method, uri, tool in CALENDAR_MIGRATED:
        assert (method, uri) in declared, f"{tool} was deleted but {method} {uri} is not in the table"


@pytest.mark.parametrize(("skill", "method", "uri"), GAP_ENDPOINTS)
def test_every_gap_from_the_worklist_has_a_row(skill: Path, method: str, uri: str) -> None:
    """Each 缺失工具清单 row this change claims to close must be an executable rule."""
    declared = {(r.method, r.uri) for r in _rules(skill)}
    assert (method, uri) in declared, f"{method} {uri} is claimed as closed but has no rule"


@pytest.mark.parametrize("skill", [CALENDAR_SKILL, TASK_SKILL])
def test_every_rule_has_a_documented_row(skill: Path) -> None:
    """The Markdown table and the rules block are two views of one fact — drift is a bug."""
    body = skill.read_text(encoding="utf-8").split("```rules", 1)[0]
    for rule in _rules(skill):
        assert rule.uri in body, f"{rule.method} {rule.uri} is executable but undocumented"


@pytest.mark.parametrize("fact", CALENDAR_PITFALL_FACTS)
def test_calendar_pitfall_text_survives(fact: str) -> None:
    """These facts exist only as prose; a rewrite that drops one loses it silently."""
    assert fact in CALENDAR_SKILL.read_text(encoding="utf-8"), f"{fact} no longer documented"


@pytest.mark.parametrize("fact", TASK_PITFALL_FACTS)
def test_task_pitfall_text_survives(fact: str) -> None:
    assert fact in TASK_SKILL.read_text(encoding="utf-8"), f"{fact} no longer documented"


# ------------------------------------------------------- parity with the old builders


@pytest.mark.parametrize("label", sorted(WAS))
async def test_generic_path_matches_the_deleted_builder(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Field for field, the generic tool sends what the deleted builder sent."""
    cap, out = await _call(monkeypatch, label)
    assert out.get("ok") is True, out
    assert _sent(cap.requests[0]) == _want(label)


@pytest.mark.parametrize("label", sorted(WAS))
async def test_token_candidates_are_declared_for_every_endpoint(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """The SDK picks by which token is present, so both candidates must be declared."""
    cap, _ = await _call(monkeypatch, label)
    names = {str(t).rsplit(".", 1)[-1] for t in (cap.requests[0].token_types or set())}
    assert names == WAS_TOKENS, f"{label} declares {names}, builder declared {WAS_TOKENS}"


def test_the_deleted_tools_are_really_gone() -> None:
    """Adding a skill without deleting the tool it replaces raises resident context, not lowers it."""
    assert not (TOOLS_DIR / "feishu_calendar.py").exists(), "the calendar tool file is back"
    assert not (TOOLS_DIR / "_feishu" / "calendar.py").exists(), "the calendar impl module is back"
    for gone in ("create_event_impl", "list_events_impl", "create_events_per_person_impl"):
        assert not hasattr(_impl, gone), f"_feishu_impl still re-exports {gone}"


def test_deleted_builder_ignored_its_timezone_arg() -> None:
    """The bug the skill now warns about was real, so the warning must not be dropped.

    ``_time_to_info`` built a *naive* datetime and called ``.timestamp()``, which
    resolves against the host clock — the ``timezone`` argument only ever decorated the
    result. So the epoch did not move when the zone did, while a zone-aware computation
    for the same wall-clock time differs by the offset between the zones. Both halves are
    computed here rather than against the deleted code, so this pins the arithmetic the
    skill's warning rests on instead of depending on what was removed.

    Deliberately host-independent: the old behaviour is reproduced as "one epoch for any
    zone" rather than as a specific number, since on a UTC+8 host it silently matched
    Shanghai — which is exactly why the bug went unnoticed in this repo's own tests.
    """
    wall = "2026-08-10 14:00"
    naive = datetime.datetime.strptime(wall, "%Y-%m-%d %H:%M")

    # What the builder produced: the zone string never entered the arithmetic.
    was_shanghai = int(naive.timestamp())
    was_new_york = int(naive.timestamp())
    assert was_shanghai == was_new_york, "the premise of the warning no longer holds"

    # What the caller meant: 2pm local in each zone is 12 hours apart.
    aware_shanghai = int(naive.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
    aware_new_york = int(naive.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=-4))).timestamp())
    assert aware_new_york - aware_shanghai == 12 * 3600, "the 12-hour claim in the skill must stay true"

    prose = CALENDAR_SKILL.read_text(encoding="utf-8")
    assert "timezone" in prose and "换算" in prose, "the timezone caveat must survive in prose"


# ------------------------------------------------------------ the guardrails execute


async def test_event_without_times_is_refused_before_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """``start_time``/``end_time`` are the only truly required event fields."""
    cap, out = await _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/calendar/v4/calendars/:calendar_id/events",
        paths_json=json.dumps({"calendar_id": "cal_x"}),
        body_json=json.dumps({"summary": "无时间"}, ensure_ascii=False),
    )
    assert out.get("ok") is False, out
    assert cap.requests == [], "a refused call must not reach the wire"


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("visibility", "secret"),
        ("attendee_ability", "can_do_anything"),
        ("free_busy_status", "maybe"),
    ],
)
async def test_event_enums_are_refused(monkeypatch: pytest.MonkeyPatch, field: str, bad: str) -> None:
    """Feishu rejects these itself, but a refusal here costs no HTTP round trip."""
    cap, out = await _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/calendar/v4/calendars/:calendar_id/events",
        paths_json=json.dumps({"calendar_id": "cal_x"}),
        body_json=json.dumps(
            {
                "start_time": {"timestamp": "1786341600"},
                "end_time": {"timestamp": "1786345200"},
                field: bad,
            }
        ),
    )
    assert out.get("ok") is False, out
    assert cap.requests == []


async def test_calendar_list_page_size_floor_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """This endpoint's minimum is 50, not 1 — a plausible ``page_size=10`` is an error."""
    cap, out = await _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/calendar/v4/calendars",
        query_json=json.dumps({"page_size": 10}),
    )
    assert out.get("ok") is False, out
    assert cap.requests == []


async def test_calendar_list_pages_under_its_own_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The declarative loop must read ``calendar_list``; ``items`` would collect nothing."""
    cap, out = await _generic(
        monkeypatch,
        pages=[
            {"ok": True, "data": {"calendar_list": [{"calendar_id": "c1"}], "has_more": True, "page_token": "p2"}},
            {"ok": True, "data": {"calendar_list": [{"calendar_id": "c2"}], "has_more": False}},
        ],
        method="GET",
        uri="/open-apis/calendar/v4/calendars",
    )
    assert out.get("ok") is True, out
    assert [c["calendar_id"] for c in out["calendar_list"]] == ["c1", "c2"]
    assert len(cap.requests) == 2, "the second page must be fetched"


async def test_reading_one_calendar_does_not_inherit_the_list_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A detail read nested under a paging collection must not borrow its loop.

    ``GET /calendars`` pages under ``calendar_list``; ``GET /calendars/:calendar_id``
    returns a single ``calendar`` and has no rule of its own, so it is matched by prefix.
    If the inherited rule kept its ``paginate``, this read would follow a ``page_token``
    that is not there and answer with an empty ``calendar_list`` — a wrong answer wearing
    ``ok: true``. That is what ``Rule.as_advice`` dropping ``paginate`` prevents.
    """
    cap, out = await _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"calendar": {"calendar_id": "cal_x", "summary": "共享日历"}}}],
        method="GET",
        uri="/open-apis/calendar/v4/calendars/:calendar_id",
        paths_json=json.dumps({"calendar_id": "cal_x"}),
    )
    assert out.get("ok") is True, out
    assert out["data"]["calendar"]["calendar_id"] == "cal_x"
    assert "calendar_list" not in out, "the detail read inherited the collection's paging loop"
    assert len(cap.requests) == 1


def test_detail_reads_still_inherit_token_strategy() -> None:
    """Dropping ``paginate`` from inherited advice must not drop the token strategy too."""
    advice = _spec.rules_for(SKILLS_DIR, "GET", "/open-apis/calendar/v4/calendars/cal_x")
    assert advice is not None, "the subtree rule should still match by prefix"
    assert advice.paginate is None, "paging must not be inherited"
    assert advice.token == "tenant_then_user", "token strategy describes the subtree and must survive"


async def test_deleting_a_shared_calendar_needs_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Irreversible and undocumented in its blast radius, so it must not go out unconfirmed."""
    cap, out = await _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/calendar/v4/calendars/:calendar_id",
        paths_json=json.dumps({"calendar_id": "cal_x"}),
    )
    assert out.get("ok") is False, out
    assert out.get("code") == "need_confirmation", out
    assert cap.requests == [], "an unconfirmed irreversible call must not reach the wire"


@pytest.mark.parametrize(
    "uri",
    ["/open-apis/task/v2/tasks/:task_guid", "/open-apis/task/v2/tasklists/:tasklist_guid"],
)
async def test_task_deletions_need_confirmation(monkeypatch: pytest.MonkeyPatch, uri: str) -> None:
    """Feishu has no task recycle bin, and does not document whether children go too."""
    key = "task_guid" if uri.endswith("task_guid") else "tasklist_guid"
    cap, out = await _generic(
        monkeypatch,
        method="DELETE",
        uri=uri,
        paths_json=json.dumps({key: "x1"}),
    )
    assert out.get("ok") is False, out
    assert out.get("code") == "need_confirmation", out
    assert cap.requests == []


async def test_empty_update_fields_is_refused_for_tasklist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same silent no-op as the task PATCH: Feishu answers 0 and changes nothing."""
    cap, out = await _generic(
        monkeypatch,
        method="PATCH",
        uri="/open-apis/task/v2/tasklists/:tasklist_guid",
        paths_json=json.dumps({"tasklist_guid": "tl_1"}),
        body_json=json.dumps({"tasklist": {"name": "新名字"}, "update_fields": []}, ensure_ascii=False),
    )
    assert out.get("ok") is False, out
    assert cap.requests == []


async def test_listing_comments_without_resource_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``resource_id`` is a required *query* param — omitting it is an error, not "all"."""
    cap, out = await _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/task/v2/comments",
        query_json=json.dumps({"page_size": 50}),
    )
    assert out.get("ok") is False, out
    assert cap.requests == []


async def test_task_search_page_size_cap_is_thirty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every other paged endpoint here allows 100; this one stops at 30."""
    cap, out = await _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/task/v2/tasks/search",
        query_json=json.dumps({"page_size": 100}),
        body_json=json.dumps({"query": "周报"}, ensure_ascii=False),
    )
    assert out.get("ok") is False, out
    assert cap.requests == []


async def test_task_search_sends_as_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint only accepts a user token, so the rule must route it that way."""
    cap, out = await _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="POST",
        uri="/open-apis/task/v2/tasks/search",
        body_json=json.dumps({"query": "周报"}, ensure_ascii=False),
        user_key="ou_caller",
    )
    assert out.get("ok") is True, out
    assert cap.kwargs[0]["prefer"] == "user", cap.kwargs[0]


def test_search_does_not_inherit_the_create_contract() -> None:
    """``POST /tasks`` requires ``summary``; ``POST /tasks/search`` must not.

    Both live under the same collection, so an over-broad rule would make search
    unreachable — refused for a field it does not take.
    """
    create = _rule(TASK_SKILL, "POST", "/open-apis/task/v2/tasks")
    search = _rule(TASK_SKILL, "POST", "/open-apis/task/v2/tasks/search")
    assert create.required == ["summary"]
    assert "summary" not in search.required
    resolved = _spec.rules_for(SKILLS_DIR, "POST", "/open-apis/task/v2/tasks/search")
    assert resolved.endpoint == search.endpoint, "the more specific rule must win"


def test_task_and_tasklist_member_roles_are_kept_apart() -> None:
    """Two ``role`` vocabularies in one domain — conflating them is the likely mistake."""
    prose = TASK_SKILL.read_text(encoding="utf-8")
    assert "editor" in prose and "viewer" in prose
    assert "assignee" in prose and "follower" in prose
    add_task = _rule(TASK_SKILL, "POST", "/open-apis/task/v2/tasks/:task_guid/add_members")
    add_list = _rule(TASK_SKILL, "POST", "/open-apis/task/v2/tasklists/:tasklist_guid/add_members")
    assert any("assignee" in p for p in add_task.pitfalls), "task members must name their own roles"
    assert any("editor" in p for p in add_list.pitfalls), "tasklist members must name their own roles"


def test_the_millisecond_versus_second_split_is_documented_on_both_sides() -> None:
    """The two domains disagree on time units, so each must say so where it is read."""
    assert "毫秒" in TASK_SKILL.read_text(encoding="utf-8")
    calendar = CALENDAR_SKILL.read_text(encoding="utf-8")
    assert "秒" in calendar
    assert "毫秒" in calendar, "the calendar skill must warn that tasks use milliseconds"


def test_the_generic_skill_points_at_the_calendar_table() -> None:
    """Two tables for one domain is how a wrong endpoint survives.

    ``feishu-api`` carried its own calendar rows and told the reader to use the tools that
    no longer exist. It must defer to this skill instead.
    """
    generic = (SKILLS_DIR / "feishu-api" / "SKILL.md").read_text(encoding="utf-8")
    assert "feishu-calendar" in generic, "the generic skill must name the domain skill"
    assert "feishu_calendar_create_event" not in generic, "a deleted tool is still advertised"
    assert "feishu_calendar_create_per_person" not in generic, "a deleted tool is still advertised"
