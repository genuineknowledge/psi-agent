"""Parity tests for the attendance domain's move from tools into the endpoint table.

These were written and run green against the *old* code first, so what they compare is
the generic path against the builders that were deleted — not against a fresh guess at
what those builders did.

The seam in this domain is **result vs config**. Reading the admin console (考勤组 /
班次) is four plain GETs and moves into the table; querying clock results stays a tool
because what it returns is a *transformation* of the response, not the response.

One behaviour deliberately changes, and it is a repair rather than a regression: the
deleted list builders **clamped** ``page_size`` silently (999 became 50, 0 became 1).
The rules block declares ``max: 50`` / ``min: 1``, which *refuses* instead. A caller who
asked for 999 was getting 50 and no way to know; now they are told.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import anyio
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
ATTENDANCE_SKILL = SKILLS_DIR / "feishu-attendance" / "SKILL.md"

#: Both token types are declared on every attendance endpoint; ``prefer`` picks the
#: send path. Frozen off the old builders.
WAS_TOKENS = {"TENANT", "USER"}

#: The wire shapes the deleted builders produced, frozen mechanically before deletion.
WAS: dict[str, dict[str, Any]] = {
    "list_groups": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/groups",
        "paths": {},
        "queries": [("page_size", "50")],
        "body": None,
    },
    "list_groups_paged": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/groups",
        "paths": {},
        "queries": [("page_size", "50"), ("page_token", "pt1")],
        "body": None,
    },
    "get_group": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/groups/:group_id",
        "paths": {"group_id": "grpA"},
        "queries": [("employee_type", "employee_id"), ("dept_type", "open_id")],
        "body": None,
    },
    "get_group_ids": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/groups/:group_id",
        "paths": {"group_id": "grpA"},
        "queries": [("employee_type", "open_id"), ("dept_type", "department_id")],
        "body": None,
    },
    "list_shifts": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/shifts",
        "paths": {},
        "queries": [("page_size", "50")],
        "body": None,
    },
    "list_shifts_paged": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/shifts",
        "paths": {},
        "queries": [("page_size", "50"), ("page_token", "pt1")],
        "body": None,
    },
    "get_shift": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/attendance/v1/shifts/:shift_id",
        "paths": {"shift_id": "shfA"},
        "queries": [],
        "body": None,
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    "list_groups": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/groups",
        "query": {"page_size": 50},
    },
    "list_groups_paged": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/groups",
        "query": {"page_size": 50, "page_token": "pt1"},
    },
    "get_group": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/groups/:group_id",
        "paths": {"group_id": "grpA"},
        "query": {"employee_type": "employee_id", "dept_type": "open_id"},
    },
    "get_group_ids": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/groups/:group_id",
        "paths": {"group_id": "grpA"},
        "query": {"employee_type": "open_id", "dept_type": "department_id"},
    },
    "list_shifts": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/shifts",
        "query": {"page_size": 50},
    },
    "list_shifts_paged": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/shifts",
        "query": {"page_size": 50, "page_token": "pt1"},
    },
    "get_shift": {
        "method": "GET",
        "uri": "/open-apis/attendance/v1/shifts/:shift_id",
        "paths": {"shift_id": "shfA"},
    },
}

#: Endpoints the skill's table must name, with the tool each replaced.
MIGRATED = [
    ("GET", "/open-apis/attendance/v1/groups", "feishu_attendance_groups"),
    ("GET", "/open-apis/attendance/v1/groups/:group_id", "feishu_attendance_group_config"),
    ("GET", "/open-apis/attendance/v1/shifts", "feishu_attendance_shifts"),
    ("GET", "/open-apis/attendance/v1/shifts/:shift_id", "feishu_attendance_shift_config"),
]

#: The one endpoint that stays a tool, and the tool that must be named by its rule.
KEPT_TOOLS = [("POST", "/open-apis/attendance/v1/user_tasks/query", "feishu_attendance_query")]

#: Which reads page, and under which response key. Neither is called ``items``.
PAGED = [
    ("/open-apis/attendance/v1/groups", "group_list"),
    ("/open-apis/attendance/v1/shifts", "shift_list"),
]

#: Facts that live only in prose. If a rewrite drops one, the model loses it silently.
PITFALL_FACTS = [
    "punch_type",
    "group_type",
    "free_punch_cfg",
    "need_punch_special_days",
    "punch_time_rule",
    "is_flexible",
    "punch_day_shift_ids",
    "1220004",
]


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
    want["queries"] = sorted(want["queries"])
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

    @property
    def request(self) -> BaseRequest:
        assert len(self.requests) == 1, f"expected 1 request, got {len(self.requests)}"
        return self.requests[0]


@pytest.fixture(autouse=True)
def _real_skills(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Drive the generic path from the shipped skill files, not a synthetic fixture."""
    _spec.reset_cache()
    monkeypatch.setattr(_api, "_skills_dir", lambda: str(SKILLS_DIR))
    yield
    _spec.reset_cache()


def _generic(
    monkeypatch: pytest.MonkeyPatch,
    pages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    cap = _CapturedInvoke(pages)
    monkeypatch.setattr(_impl, "_invoke", cap)
    out: dict[str, Any] = anyio.run(lambda: _api.call_api_impl(**kwargs))
    return cap, out


def _pages_for(label: str) -> list[dict[str, Any]] | None:
    """A single terminal page, under whichever key this endpoint's rule declares."""
    for uri, key in PAGED:
        if CALLS[label]["uri"] == uri:
            return [{"ok": True, "data": {key: [], "has_more": False}}]
    return None


def _call(monkeypatch: pytest.MonkeyPatch, label: str, **overrides: Any) -> tuple[_CapturedInvoke, dict[str, Any]]:
    """Invoke one ``CALLS`` entry through the generic path."""
    spec = {**CALLS[label], **overrides}
    return _generic(
        monkeypatch,
        pages=_pages_for(label),
        method=spec["method"],
        uri=spec["uri"],
        paths_json=json.dumps(spec.get("paths", {})),
        body_json=json.dumps(spec.get("body", {}), ensure_ascii=False),
        query_json=json.dumps(spec.get("query", {})),
        confirm=spec.get("confirm", ""),
    )


def _rules() -> list[Any]:
    return _spec.parse_rules(ATTENDANCE_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


def test_skill_declares_every_migrated_endpoint() -> None:
    """A deleted tool whose endpoint never made it into the table is a lost capability."""
    declared = {(r.method, r.uri) for r in _rules()}
    for method, uri, tool in MIGRATED:
        assert (method, uri) in declared, f"{tool} was deleted but {method} {uri} is not in the table"


def test_every_rule_has_a_documented_row() -> None:
    """The Markdown table and the rules block are two views of one fact — drift is a bug."""
    prose = ATTENDANCE_SKILL.read_text(encoding="utf-8")
    body = prose.split("```rules", 1)[0]
    for rule in _rules():
        assert rule.uri in body, f"{rule.method} {rule.uri} is executable but undocumented"


def test_the_result_config_split_is_explained() -> None:
    """Why four endpoints moved and one did not has to survive in prose."""
    prose = ATTENDANCE_SKILL.read_text(encoding="utf-8")
    assert "考勤组" in prose
    assert "班次" in prose
    assert "punch_day_shift_ids" in prose, "the group→shift indirection is the domain's main trap"


@pytest.mark.parametrize("fact", PITFALL_FACTS)
def test_pitfall_text_survives(fact: str) -> None:
    """These facts exist only as prose; a rewrite that drops one loses it silently."""
    assert fact in ATTENDANCE_SKILL.read_text(encoding="utf-8"), f"{fact} no longer documented"


# ------------------------------------------------------- parity with the old builders


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_matches_the_deleted_builder(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Field for field, the generic tool sends what the deleted builder sent."""
    cap, out = _call(monkeypatch, label)
    assert out.get("ok") is True, out
    assert _sent(cap.requests[0]) == _want(label)


@pytest.mark.parametrize(("method", "uri", "_tool"), MIGRATED)
def test_token_candidates_are_declared_for_every_endpoint(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, _tool: str
) -> None:
    """The SDK picks by which token is present, so both candidates must be declared."""
    label = next(k for k, v in CALLS.items() if v["uri"] == uri and v["method"] == method)
    cap, _ = _call(monkeypatch, label)
    names = {str(t).rsplit(".", 1)[-1] for t in (cap.requests[0].token_types or set())}
    assert names == WAS_TOKENS, f"{method} {uri} declares {names}, builder declared {WAS_TOKENS}"


# ------------------------------------------------------- the kept tool stays reachable


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_endpoint_refuses_and_names_the_tool(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, tool: str
) -> None:
    """A hard rule must refuse *silently* — the request may not go out at all."""
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        body_json=json.dumps({"user_ids": ["e1"], "check_date_from": 20260701, "check_date_to": 20260731}),
    )
    assert out.get("ok") is False, out
    assert out.get("code") == "use_dedicated_tool", out
    assert tool in json.dumps(out, ensure_ascii=False), f"the refusal must name {tool}"
    assert cap.requests == [], "a refused call must not reach the wire"


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_rule_says_why(method: str, uri: str, tool: str) -> None:
    """Without a reason, a future reader cannot tell this apart from an oversight."""
    rule = _rule(method, uri)
    assert rule.prefer_tool == tool
    assert rule.prefer_hard is True
    assert rule.why and len(rule.why) > 40, f"{tool}'s rule needs a real reason, got {rule.why!r}"


@pytest.mark.parametrize(("_method", "_uri", "tool"), KEPT_TOOLS)
def test_kept_tools_still_exist(_method: str, _uri: str, tool: str) -> None:
    """A hard rule pointing at a deleted tool would be a dead end with no way forward."""
    tools: Any = importlib.import_module("feishu_attendance")
    assert hasattr(tools, tool), f"{tool} is named by a hard rule but no longer exists"


def test_the_kept_tool_still_sends_the_frozen_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The surviving tool is untouched by this migration — prove it, don't assume it."""
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    out = anyio.run(lambda: _impl.query_attendance_impl("e1,e2", "20260701", "20260731", "employee_id", False))
    assert out["ok"] is True, out
    assert _sent(cap.request) == {
        "method": HttpMethod.POST,
        "uri": "/open-apis/attendance/v1/user_tasks/query",
        "paths": {},
        "queries": sorted([("employee_type", "employee_id"), ("ignore_invalid_users", "true")]),
        "body": {
            "user_ids": ["e1", "e2"],
            "check_date_from": 20260701,
            "check_date_to": 20260731,
            "need_overtime_result": False,
        },
    }


# --------------------------------------------------------- paging and the page_size cap


@pytest.mark.parametrize(("uri", "items_key"), PAGED)
def test_paging_drains_pages_under_the_declared_key(monkeypatch: pytest.MonkeyPatch, uri: str, items_key: str) -> None:
    """Neither list endpoint returns ``items`` — the declared key is what makes paging work."""
    pages = [
        {"ok": True, "data": {items_key: [{"id": "a"}], "has_more": True, "page_token": "pt2"}},
        {"ok": True, "data": {items_key: [{"id": "b"}], "has_more": False}},
    ]
    cap, out = _generic(
        monkeypatch,
        pages=pages,
        method="GET",
        uri=uri,
        query_json=json.dumps({"page_size": 50}),
    )
    assert out.get("ok") is True, out
    assert len(cap.requests) == 2, "paging stopped early or never started"
    assert out.get(items_key) == [{"id": "a"}, {"id": "b"}], out
    assert out.get("count") == 2, out


@pytest.mark.parametrize(("uri", "items_key"), PAGED)
def test_paging_carries_the_token_forward(monkeypatch: pytest.MonkeyPatch, uri: str, items_key: str) -> None:
    """A second page fetched without the token would silently re-read page one forever."""
    pages = [
        {"ok": True, "data": {items_key: [{"id": "a"}], "has_more": True, "page_token": "pt2"}},
        {"ok": True, "data": {items_key: [], "has_more": False}},
    ]
    cap, _ = _generic(monkeypatch, pages=pages, method="GET", uri=uri, query_json=json.dumps({"page_size": 50}))
    second = dict(cap.requests[1].queries or [])
    assert second.get("page_token") == "pt2", f"second page went out with {second}"


@pytest.mark.parametrize("uri", [uri for uri, _ in PAGED])
def test_page_size_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch, uri: str) -> None:
    """The deleted builders clamped 999 to 50 silently; refusing is the repair."""
    cap, out = _generic(monkeypatch, method="GET", uri=uri, query_json=json.dumps({"page_size": 999}))
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == [], "a refused call must not reach the wire"


@pytest.mark.parametrize("uri", [uri for uri, _ in PAGED])
def test_page_size_under_the_floor_is_refused(monkeypatch: pytest.MonkeyPatch, uri: str) -> None:
    """Same repair at the other end: 0 used to become 1 with no way to notice."""
    cap, out = _generic(monkeypatch, method="GET", uri=uri, query_json=json.dumps({"page_size": 0}))
    assert out.get("ok") is False, out
    assert cap.requests == [], "a refused call must not reach the wire"


# ------------------------------------------------------------ rules do not shadow each other


def test_group_detail_is_not_swallowed_by_the_list_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/groups`` is a segment-wise prefix of ``/groups/:group_id`` — the detail rule must win."""
    cap, out = _call(monkeypatch, "get_group")
    assert out.get("ok") is True, out
    assert cap.requests[0].uri == "/open-apis/attendance/v1/groups/:group_id"
    # The list rule declares paginate; if it had matched, this would have paged.
    assert len(cap.requests) == 1, "the detail call was treated as a paged list"


def test_shift_detail_is_not_swallowed_by_the_list_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same shadowing risk on the shift side, and the shift detail takes no query at all."""
    cap, out = _call(monkeypatch, "get_shift")
    assert out.get("ok") is True, out
    assert cap.requests[0].uri == "/open-apis/attendance/v1/shifts/:shift_id"
    assert len(cap.requests) == 1, "the detail call was treated as a paged list"
    assert list(cap.requests[0].queries or []) == [], "the shift detail endpoint takes no query"


def test_config_reads_are_not_swallowed_by_the_hard_query_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one hard rule must not stop the four reads that share the ``attendance`` prefix."""
    for label in ("list_groups", "get_group", "list_shifts", "get_shift"):
        cap, out = _call(monkeypatch, label)
        assert out.get("ok") is True, f"{label} was refused: {out}"
        assert cap.requests, f"{label} sent nothing"


def test_defaults_do_not_become_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """The group detail's two id-type params have defaults; omitting them must still work."""
    cap, out = _call(monkeypatch, "get_group", query={})
    assert out.get("ok") is True, out
    sent = dict(cap.requests[0].queries or [])
    assert sent.get("employee_type") == "employee_id", sent
    assert sent.get("dept_type") == "open_id", sent


@pytest.mark.parametrize(
    ("uri", "field", "bad"),
    [
        ("/open-apis/attendance/v1/groups/:group_id", "employee_type", "staff_id"),
        ("/open-apis/attendance/v1/groups/:group_id", "dept_type", "dept_id"),
    ],
)
def test_unknown_id_type_is_refused(monkeypatch: pytest.MonkeyPatch, uri: str, field: str, bad: str) -> None:
    """An id type Feishu doesn't know makes the response's user fields unusable."""
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri=uri,
        paths_json=json.dumps({"group_id": "grpA"}),
        query_json=json.dumps({field: bad}),
    )
    assert out.get("ok") is False, out
    assert cap.requests == [], "a refused call must not reach the wire"


def test_field_projection_is_a_documented_capability_gap() -> None:
    """The deleted config readers projected a field whitelist; rules have no such vocabulary.

    ``_GROUP_CONFIG_FIELDS`` / ``_SHIFT_CONFIG_FIELDS`` picked which keys to return. That is
    output *shaping*, and the rules vocabulary has checks only — so the generic path returns
    the whole response. Nothing is lost (the whitelists were strictly narrowing), but the
    fields worth reading have to be named in prose instead, or the model gets a wall of JSON
    with no idea which keys answer the question.
    """
    prose = ATTENDANCE_SKILL.read_text(encoding="utf-8")
    for field in ("punch_type", "group_type", "work_day_no_punch_as_lack", "punch_time_rule", "flexible_rule"):
        assert field in prose, f"{field} was in the deleted whitelist and is now undocumented"


def test_the_generic_skill_does_not_keep_a_stale_second_table() -> None:
    """Two tables for one domain is how a wrong endpoint survives.

    ``feishu-api`` used to carry its own attendance rows, and two of them were wrong:
    ``POST .../groups/list`` and ``POST .../shifts/list``, where the real endpoints the
    builders called are ``GET .../groups`` and ``GET .../shifts``. Nothing executed those
    rows, so nothing caught them. The generic skill must point at this one instead.
    """
    generic = (SKILLS_DIR / "feishu-api" / "SKILL.md").read_text(encoding="utf-8")
    assert "attendance/v1/groups/list" not in generic, "the wrong groups endpoint is back"
    assert "attendance/v1/shifts/list" not in generic, "the wrong shifts endpoint is back"
    assert "feishu-attendance" in generic, "the generic skill must name the domain skill"
