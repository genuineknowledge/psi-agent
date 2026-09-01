"""The permission / task / eLearning domains as data, not as tools.

Nine tools became five table rows plus one carried-along eLearning read, so what has to
be proved is that the generic path still puts the same bytes on the wire. Every ``WAS``
shape below was captured *from the builders* before they were deleted, by monkeypatching
``_invoke`` and recording the ``BaseRequest`` — not written by hand from the new rules,
which would only prove the rules agree with themselves.

Two facts here are the reason this domain was worth migrating at all:

* Feishu names three different things ``type`` on the permission endpoints — the file's
  type (query), the member id's form (``member_type``), and the member's kind (body
  ``type``) — and gives no field-level error when they disagree. The deleted tools hid
  that behind three separate Python parameters; the rules carry it as ``choices`` plus
  prose instead.
* ``PATCH /task/v2/tasks/:task_guid`` reads ``update_fields`` to decide what to change.
  An empty one changes nothing and still answers ``code: 0``. ``min`` could not express
  that — it coerces with ``float()`` and gives up on a list — so this round added
  ``min_items`` to the vocabulary. ``test_empty_update_fields_is_refused`` is the test
  that would have caught the silent no-op.

One deliberate behavior change is recorded here rather than hidden: the deleted
``complete_task_impl`` stamped ``completed_at`` with ``time.time()`` itself. A table row
cannot read the clock, so the caller now supplies the timestamp. See
``test_completion_timestamp_moved_to_the_caller``.
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
PERMISSION_SKILL = SKILLS_DIR / "feishu-permission" / "SKILL.md"
TASK_SKILL = SKILLS_DIR / "feishu-task" / "SKILL.md"

#: Both token types were declared on every endpoint here. The eLearning builder narrowed
#: itself to TENANT, which is a strategy, not a candidate list — see
#: ``test_elearning_sends_as_tenant_with_a_user_fallback``.
WAS_TOKENS = {"TENANT", "USER"}
ELEARNING_TOKENS = {"TENANT"}

#: The wire shapes the deleted builders produced, frozen mechanically before deletion.
WAS: dict[str, dict[str, Any]] = {
    "permission_add": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/permissions/:token/members",
        "paths": {"token": "doccnTOKEN"},
        "queries": [("type", "docx"), ("need_notification", "false")],
        "body": {"member_type": "openid", "member_id": "ou_abc", "perm": "edit", "type": "user"},
    },
    "permission_add_department_notify": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/permissions/:token/members",
        "paths": {"token": "shtcnTOKEN"},
        "queries": [("type", "sheet"), ("need_notification", "true")],
        "body": {
            "member_type": "opendepartmentid",
            "member_id": "od_dept",
            "perm": "view",
            "type": "department",
        },
    },
    "permission_list": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/permissions/:token/members",
        "paths": {"token": "doccnTOKEN"},
        "queries": [("type", "docx")],
        "body": None,
    },
    "permission_remove": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/drive/v1/permissions/:token/members/:member_id",
        "paths": {"token": "doccnTOKEN", "member_id": "ou_abc"},
        "queries": [("type", "docx"), ("member_type", "openid")],
        "body": {"type": "user"},
    },
    "task_create": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/task/v2/tasks",
        "paths": {},
        "queries": [("user_id_type", "open_id")],
        "body": {
            "summary": "写周报",
            "description": "本周进展",
            "due": {"timestamp": "1786323600000", "is_all_day": False},
            "members": [
                {"id": "ou_a", "type": "user", "id_type": "open_id", "role": "assignee"},
                {"id": "ou_b", "type": "user", "id_type": "open_id", "role": "assignee"},
                {"id": "ou_c", "type": "user", "id_type": "open_id", "role": "follower"},
            ],
        },
    },
    "task_create_bare": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/task/v2/tasks",
        "paths": {},
        "queries": [("user_id_type", "open_id")],
        "body": {"summary": "只有标题"},
    },
    "task_list": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/task/v2/tasks",
        "paths": {},
        "queries": [("page_size", "50"), ("type", "my_tasks"), ("user_id_type", "open_id")],
        "body": None,
    },
    "task_list_filtered": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/task/v2/tasks",
        "paths": {},
        "queries": [
            ("page_size", "20"),
            ("type", "my_tasks"),
            ("user_id_type", "open_id"),
            ("completed", "false"),
            ("page_token", "PAGE2"),
        ],
        "body": None,
    },
    "task_get": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/task/v2/tasks/:task_guid",
        "paths": {"task_guid": "guid-1"},
        "queries": [("user_id_type", "open_id")],
        "body": None,
    },
    "task_update": {
        "method": HttpMethod.PATCH,
        "uri": "/open-apis/task/v2/tasks/:task_guid",
        "paths": {"task_guid": "guid-1"},
        "queries": [("user_id_type", "open_id")],
        "body": {
            "task": {"summary": "新标题", "due": {"timestamp": "1786377600000", "is_all_day": False}},
            "update_fields": ["summary", "due"],
        },
    },
    "task_reopen": {
        "method": HttpMethod.PATCH,
        "uri": "/open-apis/task/v2/tasks/:task_guid",
        "paths": {"task_guid": "guid-1"},
        "queries": [("user_id_type", "open_id")],
        "body": {"task": {"completed_at": "0"}, "update_fields": ["completed_at"]},
    },
    "elearning_list": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/elearning/v2/course_registrations",
        "paths": {},
        "queries": [
            ("user_id_type", "open_id"),
            ("page_size", "100"),
            ("user_ids", "ou_a"),
            ("user_ids", "ou_b"),
        ],
        "body": None,
    },
    "elearning_list_all": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/elearning/v2/course_registrations",
        "paths": {},
        "queries": [("user_id_type", "open_id"), ("page_size", "50"), ("page_token", "PAGE2")],
        "body": None,
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    "permission_add": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/permissions/:token/members",
        "paths": {"token": "doccnTOKEN"},
        "query": {"type": "docx", "need_notification": "false"},
        "body": {"member_type": "openid", "member_id": "ou_abc", "perm": "edit", "type": "user"},
    },
    "permission_add_department_notify": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/permissions/:token/members",
        "paths": {"token": "shtcnTOKEN"},
        "query": {"type": "sheet", "need_notification": "true"},
        "body": {
            "member_type": "opendepartmentid",
            "member_id": "od_dept",
            "perm": "view",
            "type": "department",
        },
    },
    "permission_list": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/permissions/:token/members",
        "paths": {"token": "doccnTOKEN"},
        "query": {"type": "docx"},
    },
    "permission_remove": {
        "method": "DELETE",
        "uri": "/open-apis/drive/v1/permissions/:token/members/:member_id",
        "paths": {"token": "doccnTOKEN", "member_id": "ou_abc"},
        "query": {"type": "docx", "member_type": "openid"},
        "body": {"type": "user"},
    },
    "task_create": {
        "method": "POST",
        "uri": "/open-apis/task/v2/tasks",
        "query": {"user_id_type": "open_id"},
        "body": {
            "summary": "写周报",
            "description": "本周进展",
            "due": {"timestamp": "1786323600000", "is_all_day": False},
            "members": [
                {"id": "ou_a", "type": "user", "id_type": "open_id", "role": "assignee"},
                {"id": "ou_b", "type": "user", "id_type": "open_id", "role": "assignee"},
                {"id": "ou_c", "type": "user", "id_type": "open_id", "role": "follower"},
            ],
        },
    },
    "task_create_bare": {
        "method": "POST",
        "uri": "/open-apis/task/v2/tasks",
        "query": {"user_id_type": "open_id"},
        "body": {"summary": "只有标题"},
    },
    "task_list": {
        "method": "GET",
        "uri": "/open-apis/task/v2/tasks",
        "query": {"page_size": 50, "type": "my_tasks", "user_id_type": "open_id"},
    },
    "task_list_filtered": {
        "method": "GET",
        "uri": "/open-apis/task/v2/tasks",
        "query": {
            "page_size": 20,
            "type": "my_tasks",
            "user_id_type": "open_id",
            "completed": "false",
            "page_token": "PAGE2",
        },
    },
    "task_get": {
        "method": "GET",
        "uri": "/open-apis/task/v2/tasks/:task_guid",
        "paths": {"task_guid": "guid-1"},
        "query": {"user_id_type": "open_id"},
    },
    "task_update": {
        "method": "PATCH",
        "uri": "/open-apis/task/v2/tasks/:task_guid",
        "paths": {"task_guid": "guid-1"},
        "query": {"user_id_type": "open_id"},
        "body": {
            "task": {"summary": "新标题", "due": {"timestamp": "1786377600000", "is_all_day": False}},
            "update_fields": ["summary", "due"],
        },
    },
    "task_reopen": {
        "method": "PATCH",
        "uri": "/open-apis/task/v2/tasks/:task_guid",
        "paths": {"task_guid": "guid-1"},
        "query": {"user_id_type": "open_id"},
        "body": {"task": {"completed_at": "0"}, "update_fields": ["completed_at"]},
    },
    "elearning_list": {
        "method": "GET",
        "uri": "/open-apis/elearning/v2/course_registrations",
        "query": {"user_id_type": "open_id", "page_size": 100, "user_ids": ["ou_a", "ou_b"]},
    },
    "elearning_list_all": {
        "method": "GET",
        "uri": "/open-apis/elearning/v2/course_registrations",
        "query": {"user_id_type": "open_id", "page_size": 50, "page_token": "PAGE2"},
    },
}

#: Endpoints the skills' tables must name, with the tool each replaced.
MIGRATED = [
    ("POST", "/open-apis/drive/v1/permissions/:token/members", "feishu_permission_add_member"),
    ("GET", "/open-apis/drive/v1/permissions/:token/members", "feishu_permission_list_members"),
    (
        "DELETE",
        "/open-apis/drive/v1/permissions/:token/members/:member_id",
        "feishu_permission_remove_member",
    ),
    ("POST", "/open-apis/task/v2/tasks", "feishu_task_create"),
    ("GET", "/open-apis/task/v2/tasks", "feishu_task_list"),
    ("GET", "/open-apis/task/v2/tasks/:task_guid", "feishu_task_get"),
    ("PATCH", "/open-apis/task/v2/tasks/:task_guid", "feishu_task_update"),
    ("PATCH", "/open-apis/task/v2/tasks/:task_guid", "feishu_task_complete"),
    (
        "GET",
        "/open-apis/elearning/v2/course_registrations",
        "feishu_elearning_list_registrations",
    ),
]

#: Which reads page, and under which response key. Both happen to be ``items`` here,
#: which is exactly why the key is declared rather than assumed.
PAGED = [
    ("/open-apis/task/v2/tasks", "items"),
    ("/open-apis/elearning/v2/course_registrations", "items"),
]

#: Facts that live only in prose. If a rewrite drops one, the model loses it silently.
PERMISSION_FACTS = [
    "member_type",
    "opendepartmentid",
    "full_access",
    "obj_token",
    "openid",
]
TASK_FACTS = [
    "update_fields",
    "1470400",
    "my_tasks",
    "assignee_related",
    "is_all_day",
    "completed_at",
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
    call = CALLS[label]
    for uri, key in PAGED:
        if call["uri"] == uri and call["method"] == "GET":
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
        query_json=json.dumps(spec.get("query", {}), ensure_ascii=False),
        confirm=spec.get("confirm", ""),
    )


def _rules() -> list[Any]:
    """Both skills' rules together — the endpoints were split across two files."""
    out: list[Any] = []
    for skill in (PERMISSION_SKILL, TASK_SKILL):
        out.extend(_spec.parse_rules(skill.read_text(encoding="utf-8")))
    return out


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skills parse


def test_skills_declare_every_migrated_endpoint() -> None:
    """A deleted tool whose endpoint never made it into a table is a lost capability."""
    declared = {(r.method, r.uri) for r in _rules()}
    for method, uri, tool in MIGRATED:
        assert (method, uri) in declared, f"{tool} was deleted but {method} {uri} is not in the table"


def test_every_rule_has_a_documented_row() -> None:
    """The Markdown table and the rules block are two views of one fact — drift is a bug."""
    for skill in (PERMISSION_SKILL, TASK_SKILL):
        prose = skill.read_text(encoding="utf-8")
        body = prose.split("```rules", 1)[0]
        for rule in _spec.parse_rules(prose):
            assert rule.uri in body, f"{rule.method} {rule.uri} is executable but undocumented in {skill.name}"


def test_complete_and_update_share_one_endpoint() -> None:
    """Two deleted tools, one PATCH. The table must not grow a second rule for the same URI.

    ``feishu_task_complete`` and ``feishu_task_update`` were separate tools, but on the wire
    completion *is* an update — ``task.completed_at`` plus ``update_fields``. A duplicate rule
    would make which one applies depend on file order.
    """
    patches = [r for r in _rules() if r.method == "PATCH" and r.uri == "/open-apis/task/v2/tasks/:task_guid"]
    assert len(patches) == 1, f"completion and update must share one rule, found {len(patches)}"


@pytest.mark.parametrize("fact", PERMISSION_FACTS)
def test_permission_pitfall_text_survives(fact: str) -> None:
    """These facts exist only as prose; a rewrite that drops one loses it silently."""
    assert fact in PERMISSION_SKILL.read_text(encoding="utf-8"), f"{fact} no longer documented"


@pytest.mark.parametrize("fact", TASK_FACTS)
def test_task_pitfall_text_survives(fact: str) -> None:
    """Same for the task side, including the error code a wrong ``type`` produces."""
    assert fact in TASK_SKILL.read_text(encoding="utf-8"), f"{fact} no longer documented"


def test_the_three_types_are_explained() -> None:
    """Three unrelated things named ``type`` on one endpoint is this domain's worst trap.

    Query ``type`` is the *file's* type, body ``member_type`` is the id's *form*, and body
    ``type`` is the member's *kind*. Feishu accepts a mismatched set and answers with a
    generic error, so the only defence is that the distinction stays written down.
    """
    prose = PERMISSION_SKILL.read_text(encoding="utf-8")
    assert "member_type" in prose
    assert "openid" in prose, "the id form is `openid`, not `open_id` — the table must say so"
    add = _rule("POST", "/open-apis/drive/v1/permissions/:token/members")
    kinds = add.fields.get("body.member_type", {}).get("choices") or []
    assert "open_id" not in kinds, "`open_id` is not a valid member_type; offering it would send a request that fails"
    assert "openid" in kinds
    # The two `type` fields must be constrained separately, or one silently goes unchecked.
    assert (add.fields.get("query.type", {}).get("choices") or []) != (
        add.fields.get("body.type", {}).get("choices") or []
    ), "query.type and body.type are different vocabularies and need separate rows"


# ------------------------------------------------------- parity with the old builders


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_matches_the_deleted_builder(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Field for field, the generic tool sends what the deleted builder sent."""
    cap, out = _call(monkeypatch, label)
    assert out.get("ok") is True, out
    assert _sent(cap.requests[0]) == _want(label)


@pytest.mark.parametrize(("method", "uri", "_tool"), [m for m in MIGRATED if "elearning" not in m[1]])
def test_token_candidates_are_declared_for_every_endpoint(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, _tool: str
) -> None:
    """The SDK picks by which token is present, so both candidates must be declared."""
    label = next(k for k, v in CALLS.items() if v["uri"] == uri and v["method"] == method)
    cap, _ = _call(monkeypatch, label)
    names = {str(t).rsplit(".", 1)[-1] for t in (cap.requests[0].token_types or set())}
    assert names == WAS_TOKENS, f"{method} {uri} declares {names}, builder declared {WAS_TOKENS}"


def test_elearning_sends_as_tenant_with_a_user_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Here the generic path is deliberately *wider* than the builder, and that is a repair.

    ``_build_list_course_registrations_request`` set ``token_types = {TENANT}``, so if the
    app's tenant token was denied there was no second path — the read just failed. The
    generic builder declares both candidates and lets ``prefer`` choose, which is what makes
    the tenant→user retry in ``_invoke_once`` reachable at all. What must not change is the
    *strategy*: course registrations are an org-wide read, so tenant goes first.
    """
    cap, _ = _call(monkeypatch, "elearning_list")
    assert _rule("GET", "/open-apis/elearning/v2/course_registrations").token == "tenant"
    assert cap.kwargs[0].get("prefer") == "tenant", cap.kwargs[0]
    names = {str(t).rsplit(".", 1)[-1] for t in (cap.requests[0].token_types or set())}
    assert names >= ELEARNING_TOKENS, f"tenant must stay a candidate, got {names}"


# -------------------------------------------------- the silent failures are hard-blocked


def test_empty_update_fields_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ``update_fields`` changes nothing and Feishu still answers ``code: 0``.

    This is the failure ``min_items`` was added for. ``min: 1`` looks like it says the same
    thing but coerces with ``float()`` and swallows the ``TypeError`` a list raises, so the
    check silently passed everything.
    """
    cap, out = _generic(
        monkeypatch,
        method="PATCH",
        uri="/open-apis/task/v2/tasks/:task_guid",
        paths_json=json.dumps({"task_guid": "guid-1"}),
        body_json=json.dumps({"task": {"summary": "新标题"}, "update_fields": []}, ensure_ascii=False),
    )
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == [], "a refused call must not reach the wire"


def test_missing_update_fields_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting it entirely is the same no-op, so ``required`` has to cover it too."""
    cap, out = _generic(
        monkeypatch,
        method="PATCH",
        uri="/open-apis/task/v2/tasks/:task_guid",
        paths_json=json.dumps({"task_guid": "guid-1"}),
        body_json=json.dumps({"task": {"summary": "新标题"}}, ensure_ascii=False),
    )
    assert out.get("ok") is False, out
    assert cap.requests == [], "a refused call must not reach the wire"


def test_one_update_field_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """The floor is a floor, not a bigger requirement — one field must still go out."""
    cap, out = _generic(
        monkeypatch,
        method="PATCH",
        uri="/open-apis/task/v2/tasks/:task_guid",
        paths_json=json.dumps({"task_guid": "guid-1"}),
        body_json=json.dumps({"task": {"summary": "新标题"}, "update_fields": ["summary"]}, ensure_ascii=False),
    )
    assert out.get("ok") is True, out
    assert cap.requests[0].body["update_fields"] == ["summary"]


def test_completion_timestamp_moved_to_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deliberate behaviour change, recorded rather than hidden.

    ``complete_task_impl`` called ``time.time()`` itself and built ``completed_at`` from it. A
    table row cannot read a clock, so completion is now an ordinary PATCH whose ``completed_at``
    the caller supplies. Reopening was always the literal ``"0"``, so only this direction moved.
    The skill has to say so, or the model will send an empty ``completed_at`` and get a task
    that stays open.
    """
    cap, out = _generic(
        monkeypatch,
        method="PATCH",
        uri="/open-apis/task/v2/tasks/:task_guid",
        paths_json=json.dumps({"task_guid": "guid-1"}),
        body_json=json.dumps({"task": {"completed_at": "1786323600000"}, "update_fields": ["completed_at"]}),
    )
    assert out.get("ok") is True, out
    assert cap.requests[0].body["task"]["completed_at"] == "1786323600000"
    prose = TASK_SKILL.read_text(encoding="utf-8")
    assert "completed_at" in prose
    assert '"0"' in prose, "reopening by writing 0 has to stay documented"


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("type", "document"),
        ("perm", "editor"),
        ("member_type", "open_id"),
        ("need_notification", "yes"),
    ],
)
def test_permission_choices_are_enforced(monkeypatch: pytest.MonkeyPatch, field: str, bad: str) -> None:
    """Every one of these is accepted by the JSON schema and rejected by Feishu."""
    body = {"member_type": "openid", "member_id": "ou_abc", "perm": "edit", "type": "user"}
    query = {"type": "docx", "need_notification": "false"}
    if field in query:
        query[field] = bad
    else:
        body[field] = bad
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/drive/v1/permissions/:token/members",
        paths_json=json.dumps({"token": "doccnTOKEN"}),
        query_json=json.dumps(query),
        body_json=json.dumps(body),
    )
    assert out.get("ok") is False, f"{field}={bad} was accepted: {out}"
    assert cap.requests == [], "a refused call must not reach the wire"


def test_permission_add_requires_the_whole_member_triple(monkeypatch: pytest.MonkeyPatch) -> None:
    """``member_type`` without ``member_id`` (or vice versa) is an unanswerable request."""
    for missing in ("member_type", "member_id", "perm", "type"):
        body = {"member_type": "openid", "member_id": "ou_abc", "perm": "edit", "type": "user"}
        body.pop(missing)
        cap, out = _generic(
            monkeypatch,
            method="POST",
            uri="/open-apis/drive/v1/permissions/:token/members",
            paths_json=json.dumps({"token": "doccnTOKEN"}),
            query_json=json.dumps({"type": "docx"}),
            body_json=json.dumps(body),
        )
        assert out.get("ok") is False, f"missing {missing} was accepted: {out}"
        assert cap.requests == [], f"missing {missing} still reached the wire"


def test_file_type_is_required_on_every_permission_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the query ``type`` Feishu cannot tell which store the token belongs to."""
    for method, uri, paths in (
        ("GET", "/open-apis/drive/v1/permissions/:token/members", {"token": "doccnTOKEN"}),
        (
            "DELETE",
            "/open-apis/drive/v1/permissions/:token/members/:member_id",
            {"token": "doccnTOKEN", "member_id": "ou_abc"},
        ),
    ):
        cap, out = _generic(
            monkeypatch,
            method=method,
            uri=uri,
            paths_json=json.dumps(paths),
            body_json=json.dumps({"type": "user"}),
            query_json=json.dumps({"member_type": "openid"}),
        )
        assert out.get("ok") is False, f"{method} {uri} went out with no file type: {out}"
        assert cap.requests == [], "a refused call must not reach the wire"


# --------------------------------------------------------- paging under the declared key


@pytest.mark.parametrize(("uri", "items_key"), PAGED)
def test_paging_drains_pages_under_the_declared_key(monkeypatch: pytest.MonkeyPatch, uri: str, items_key: str) -> None:
    """Both of these do use ``items``, which is exactly why the key is declared not assumed."""
    pages = [
        {"ok": True, "data": {items_key: [{"id": "a"}], "has_more": True, "page_token": "pt2"}},
        {"ok": True, "data": {items_key: [{"id": "b"}], "has_more": False}},
    ]
    cap, out = _generic(monkeypatch, pages=pages, method="GET", uri=uri, query_json=json.dumps({"page_size": 50}))
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
    """Both endpoints cap at 100; asking for more used to be silently trimmed upstream."""
    cap, out = _generic(monkeypatch, method="GET", uri=uri, query_json=json.dumps({"page_size": 500}))
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == [], "a refused call must not reach the wire"


def test_elearning_user_ids_repeat_as_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """``user_ids`` is a repeatable param, not a comma-joined string — joining returns nothing.

    A single ``user_ids=ou_a,ou_b`` is accepted and matches no user, so the answer is an empty
    page rather than an error. The generic path has to expand the list into one pair each.
    """
    cap, out = _call(monkeypatch, "elearning_list")
    assert out.get("ok") is True, out
    pairs = [(k, v) for k, v in (cap.requests[0].queries or []) if k == "user_ids"]
    assert pairs == [("user_ids", "ou_a"), ("user_ids", "ou_b")], pairs


# ------------------------------------------------------------ rules do not shadow each other


def test_permission_get_and_post_do_not_share_a_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """One URI, two methods, different required fields — matching on the URI alone would break both."""
    cap, out = _call(monkeypatch, "permission_list")
    assert out.get("ok") is True, out
    assert cap.requests[0].http_method == HttpMethod.GET
    # The POST rule requires a member triple; if it had matched, this bare read would be refused.
    assert cap.requests[0].body in (None, {}), f"the read went out with a body: {cap.requests[0].body}"


def test_member_delete_is_not_swallowed_by_the_collection_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """``.../members`` is a segment-wise prefix of ``.../members/:member_id``."""
    cap, out = _call(monkeypatch, "permission_remove")
    assert out.get("ok") is True, out
    assert cap.requests[0].uri == "/open-apis/drive/v1/permissions/:token/members/:member_id"
    assert dict(cap.requests[0].queries or []).get("member_type") == "openid", (
        "delete moves member_type into the query while still needing type in the body"
    )


def test_task_detail_is_not_swallowed_by_the_list_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/task/v2/tasks`` prefixes ``/task/v2/tasks/:task_guid``, and only the list pages."""
    cap, out = _call(monkeypatch, "task_get")
    assert out.get("ok") is True, out
    assert cap.requests[0].uri == "/open-apis/task/v2/tasks/:task_guid"
    assert len(cap.requests) == 1, "the detail call was treated as a paged list"


def test_task_list_defaults_do_not_become_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """``type``/``user_id_type`` have defaults; a bare list must still send them."""
    cap, out = _call(monkeypatch, "task_list", query={})
    assert out.get("ok") is True, out
    sent = dict(cap.requests[0].queries or [])
    assert sent.get("type") == "my_tasks", sent
    assert sent.get("user_id_type") == "open_id", sent


def test_task_create_needs_a_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task with no summary is created and shows up in the list as a blank row."""
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/task/v2/tasks",
        body_json=json.dumps({"description": "只有描述"}, ensure_ascii=False),
    )
    assert out.get("ok") is False, out
    assert cap.requests == [], "a refused call must not reach the wire"


def test_the_generic_skill_points_at_both_new_skills() -> None:
    """The router table is how the model finds these endpoints at all."""
    generic = (SKILLS_DIR / "feishu-api" / "SKILL.md").read_text(encoding="utf-8")
    assert "feishu-permission" in generic
    assert "feishu-task" in generic


@pytest.mark.parametrize(
    "tool",
    [
        "feishu_permission_add_member",
        "feishu_permission_list_members",
        "feishu_permission_remove_member",
        "feishu_task_create",
        "feishu_task_list",
        "feishu_task_get",
        "feishu_task_update",
        "feishu_task_complete",
        "feishu_elearning_list_registrations",
    ],
)
def test_replaced_tools_are_gone(tool: str) -> None:
    """Adding a skill without deleting the tool it replaces raises context instead of lowering it."""
    hits = sorted(p.name for p in TOOLS_DIR.glob("*.py") if f"def {tool}(" in p.read_text(encoding="utf-8"))
    assert hits == [], f"{tool} is covered by the endpoint table but still defined in {hits}"
