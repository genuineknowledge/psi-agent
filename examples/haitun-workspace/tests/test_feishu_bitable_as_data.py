"""Parity: the ``feishu-bitable`` skill reaches Feishu the same way the tools did.

Fourth domain through the migration, after ``contact``, ``chat`` and ``message``. Same
standard of proof: build the request through the generic ``feishu_api`` path driven by
``skills/feishu-bitable/SKILL.md``, and compare it against what the deleted tool sent.

Bitable is the domain where the *kept* tools matter most, because its signature failure
is silent in both directions. A column name that does not match the table is dropped
with ``code: 0`` — Feishu reports success for a write that stored nothing — so
``feishu_bitable_create_records`` / ``_update_record(s)`` check the column list before
writing and compare Feishu's echo after, and none of that fits in a table. Likewise
``search_records`` validates a nested ``conditions`` array and refuses ``view_id``
together with ``filter``/``sort`` (Feishu silently ignores the view and searches the
whole table), and ``create_table`` / ``update_field`` reach inside a nested ``fields``
array. ``_present`` is flat-only, so those stay tools and this file pins the six
``hard: true`` rules that keep the generic path from reaching around them.

The URI overlap is the worst of any domain so far. ``POST .../tables`` (建表, hard) is a
prefix of ``batch_create``, ``batch_delete`` and everything under ``:table_id``;
``POST .../records`` (建一行) is a prefix of ``batch_create``/``batch_update``/
``batch_delete``/``search``; ``PUT .../apps/:app_token`` sits above two hard-refused
PUTs. If specificity ordering or the advice downgrade failed, creating a view would be
refused with "use feishu_bitable_create_table", and batch-writing rows would be built as
a single-row insert. Both directions are tested.
"""

from __future__ import annotations

import importlib
import json
import re
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
BITABLE_SKILL = SKILLS_DIR / "feishu-bitable" / "SKILL.md"

#: Every tabled bitable endpoint declared both TENANT and USER as candidates, exactly as
#: the deleted impls did. The token *strategy* (``prefer``) is a separate axis, carried by
#: the rules' ``token:`` field and checked in its own test below.
WAS_TOKENS = {"TENANT", "USER"}

# Marks a CALLS entry whose endpoint is confirmation-gated: ``_call`` clears the gate
# by fetching the code the user would have been sent, instead of a literal token (the
# gate no longer accepts one — see ``_feishu_confirm``).
_AUTO_CONFIRM = "<user-code>"


# The wire contract of the 17 tools this skill replaced, captured mechanically by running
# each impl against a stub ``_invoke`` at migration time and frozen here. The impls are
# gone — keeping them alive only to be a test reference would be keeping dead production
# code, which is what this migration exists to remove. These literals are what Feishu
# received before the change, so they still pin the contract.
WAS: dict[str, dict[str, Any]] = {
    "create_app": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps",
        "paths": {},
        "queries": [],
        "body": {"name": "N", "folder_token": "fldA", "time_zone": "Asia/Shanghai"},
    },
    "get_app": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token",
        "paths": {"app_token": "appA"},
        "queries": [],
        "body": None,
    },
    "update_app": {
        "method": HttpMethod.PUT,
        "uri": "/open-apis/bitable/v1/apps/:app_token",
        "paths": {"app_token": "appA"},
        "queries": [],
        "body": {"name": "N2"},
    },
    "copy_app": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/copy",
        "paths": {"app_token": "appA"},
        "queries": [],
        "body": {"name": "C", "folder_token": "fldB"},
    },
    "list_tables": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables",
        "paths": {"app_token": "appA"},
        "queries": [("page_size", "100")],
        "body": None,
    },
    "create_tables": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/batch_create",
        "paths": {"app_token": "appA"},
        "queries": [],
        "body": {"tables": [{"name": "t1"}, {"name": "t2"}]},
    },
    "delete_tables": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/batch_delete",
        "paths": {"app_token": "appA"},
        "queries": [],
        "body": {"table_ids": ["tblA", "tblB"]},
    },
    "list_fields": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "queries": [("page_size", "100")],
        "body": None,
    },
    "create_field": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "queries": [],
        "body": {"field_name": "F", "type": 1},
    },
    # 删列是逐个 DELETE, 不是批量端点 —— 删两列就是两次请求, 表里也这么写。
    "delete_field": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id",
        "paths": {"app_token": "appA", "table_id": "tblA", "field_id": "fldX"},
        "queries": [],
        "body": None,
    },
    "list_records": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "queries": [("page_size", "100")],
        "body": None,
    },
    "get_record": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id",
        "paths": {"app_token": "appA", "table_id": "tblA", "record_id": "recA"},
        "queries": [("with_shared_url", "true")],
        "body": None,
    },
    "create_record": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "queries": [],
        "body": {"fields": {"标题": "v"}},
    },
    # batch_delete 的 body 键是 records, 不是 record_ids —— 猜错飞书会回成功且什么都不删。
    "delete_records": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_delete",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "queries": [],
        "body": {"records": ["recA", "recB"]},
    },
    "create_role": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/roles",
        "paths": {"app_token": "appA"},
        "queries": [],
        "body": {"role_name": "R", "table_roles": [{"table_id": "tblA", "table_perm": 1}]},
    },
    "list_roles": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/roles",
        "paths": {"app_token": "appA"},
        "queries": [("page_size", "100")],
        "body": None,
    },
    "add_role_member": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/roles/:role_id/members",
        "paths": {"app_token": "appA", "role_id": "rolA"},
        "queries": [("member_id_type", "open_id")],
        "body": {"member_id": "ou_a"},
    },
}

#: What the caller now writes to reach each of those endpoints generically. Kept beside
#: ``WAS`` so a row and its expected wire shape are read together.
CALLS: dict[str, dict[str, Any]] = {
    "create_app": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps",
        "body": {"name": "N", "folder_token": "fldA", "time_zone": "Asia/Shanghai"},
    },
    "get_app": {
        "method": "GET",
        "uri": "/open-apis/bitable/v1/apps/:app_token",
        "paths": {"app_token": "appA"},
    },
    "update_app": {
        "method": "PUT",
        "uri": "/open-apis/bitable/v1/apps/:app_token",
        "paths": {"app_token": "appA"},
        "body": {"name": "N2"},
    },
    "copy_app": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/copy",
        "paths": {"app_token": "appA"},
        "body": {"name": "C", "folder_token": "fldB"},
    },
    "list_tables": {
        "method": "GET",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables",
        "paths": {"app_token": "appA"},
    },
    "create_tables": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/batch_create",
        "paths": {"app_token": "appA"},
        "body": {"tables": [{"name": "t1"}, {"name": "t2"}]},
    },
    "delete_tables": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/batch_delete",
        "paths": {"app_token": "appA"},
        "body": {"table_ids": ["tblA", "tblB"]},
        "confirm": _AUTO_CONFIRM,
    },
    "list_fields": {
        "method": "GET",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields",
        "paths": {"app_token": "appA", "table_id": "tblA"},
    },
    "create_field": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "body": {"field_name": "F", "type": 1},
    },
    "delete_field": {
        "method": "DELETE",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id",
        "paths": {"app_token": "appA", "table_id": "tblA", "field_id": "fldX"},
    },
    "list_records": {
        "method": "GET",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records",
        "paths": {"app_token": "appA", "table_id": "tblA"},
    },
    "get_record": {
        "method": "GET",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id",
        "paths": {"app_token": "appA", "table_id": "tblA", "record_id": "recA"},
    },
    "create_record": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "body": {"fields": {"标题": "v"}},
    },
    "delete_records": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_delete",
        "paths": {"app_token": "appA", "table_id": "tblA"},
        "body": {"records": ["recA", "recB"]},
    },
    "create_role": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/roles",
        "paths": {"app_token": "appA"},
        "body": {"role_name": "R", "table_roles": [{"table_id": "tblA", "table_perm": 1}]},
    },
    "list_roles": {
        "method": "GET",
        "uri": "/open-apis/bitable/v1/apps/:app_token/roles",
        "paths": {"app_token": "appA"},
    },
    "add_role_member": {
        "method": "POST",
        "uri": "/open-apis/bitable/v1/apps/:app_token/roles/:role_id/members",
        "paths": {"app_token": "appA", "role_id": "rolA"},
        "body": {"member_id": "ou_a"},
    },
}

#: Endpoints whose response is paged, so the stub has to answer with a closed page.
PAGED = {"list_tables", "list_fields", "list_records", "list_roles"}


def _shape(req: BaseRequest) -> dict[str, Any]:
    """The part of a request that determines what Feishu receives."""
    return {
        "method": req.http_method,
        "uri": req.uri,
        "paths": dict(req.paths or {}),
        "queries": sorted((k, str(v)) for k, v in (req.queries or [])),
        "body": req.body or None,
        "tokens": set(req.token_types or set()),
    }


def _sent(req: BaseRequest) -> dict[str, Any]:
    """What the generic path is about to send, comparable to ``_was``."""
    out = _shape(req)
    out.pop("tokens")
    return out


def _was(label: str) -> dict[str, Any]:
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
    *,
    pages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    cap = _CapturedInvoke(pages)
    monkeypatch.setattr(_impl, "_invoke", cap)
    out: dict[str, Any] = anyio.run(lambda: _api.call_api_impl(**kwargs))
    return cap, out


def _call(monkeypatch: pytest.MonkeyPatch, label: str, **overrides: Any) -> tuple[_CapturedInvoke, dict[str, Any]]:
    """Invoke one ``CALLS`` entry through the generic path.

    Guarded endpoints need a code the *user* was sent (see ``_feishu_confirm``), so for
    those the confirmation is cleared first — these tests are about the request that
    goes on the wire, and the gate itself is tested separately below.
    """
    spec = {**CALLS[label], **overrides}
    pages = [{"ok": True, "data": {"items": [], "has_more": False}}] if label in PAGED else None
    kwargs = {
        "method": spec["method"],
        "uri": spec["uri"],
        "paths_json": json.dumps(spec.get("paths", {})),
        "body_json": json.dumps(spec.get("body", {}), ensure_ascii=False),
        "query_json": json.dumps(spec.get("query", {})),
        "confirm": spec.get("confirm", ""),
    }
    if spec.get("confirm") == _AUTO_CONFIRM:
        kwargs["user_key"] = "ou_boss"
        kwargs["confirm"] = _obtain_code(monkeypatch, kwargs)
    return _generic(monkeypatch, pages=pages, **kwargs)


def _obtain_code(monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]) -> str:
    """Run the refusal once and read the code out of the message sent to the user."""
    sent: list[str] = []

    async def _send(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
        sent.append(text)
        return {"ok": True, "message_id": "om_1"}

    with monkeypatch.context() as patch:
        patch.setattr(_impl, "send_message_impl", _send)
        patch.setattr(_impl, "_invoke", _CapturedInvoke())
        anyio.run(lambda: _api.call_api_impl(**{**kwargs, "confirm": ""}))
    match = re.search(r"确认码: (\d{6})", sent[0] if sent else "")
    assert match, f"the gate did not send a code: {sent!r}"
    return match.group(1)


def _rules() -> list[Any]:
    return _spec.parse_rules(BITABLE_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


#: Endpoints this skill declares that were never a tool, so there is no builder to compare
#: against and they are absent from ``WAS`` above. Their wire shapes are frozen from the
#: official docs in ``test_feishu_bitable_views_as_data.py`` instead. Listed here so this
#: file's closed-set assertion stays closed: a rule appearing in neither set is a rule
#: nobody documented a shape for.
NEVER_A_TOOL = {
    ("GET", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views"),
    ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views"),
    ("GET", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id"),
    ("PATCH", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id"),
    ("DELETE", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id"),
    ("GET", "/open-apis/bitable/v1/apps/:app_token/dashboards"),
    ("POST", "/open-apis/bitable/v1/apps/:app_token/dashboards/:block_id/copy"),
    ("GET", "/open-apis/bitable/v1/apps/:app_token/workflows"),
    ("PUT", "/open-apis/bitable/v1/apps/:app_token/workflows/:workflow_id"),
}


def test_skill_declares_every_migrated_endpoint() -> None:
    """The 17 tabled endpoints, the 6 that point back at a tool, and the 9 with no tool."""
    got = {(r.method, r.uri) for r in _rules()}
    tabled = {(WAS[k]["method"].name, WAS[k]["uri"]) for k in WAS}
    assert tabled <= got, f"tabled endpoint missing a rule: {tabled - got}"
    assert got - tabled - NEVER_A_TOOL == {
        ("POST", "/open-apis/bitable/v1/apps/:app_token/tables"),
        ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_create"),
        ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_update"),
        ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/search"),
        ("PUT", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id"),
        ("PUT", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"),
    }
    assert got >= NEVER_A_TOOL, f"declared-but-unruled: {NEVER_A_TOOL - got}"


# ------------------------------------------------------- wire parity vs the builders


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_matches_the_deleted_tool(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Every tabled endpoint builds byte-for-byte what its tool built."""
    cap, out = _call(monkeypatch, label)
    assert cap.requests, f"{label}: no request built — {out}"
    assert _sent(cap.request) == _was(label)


@pytest.mark.parametrize("label", sorted(WAS))
def test_every_tabled_endpoint_keeps_both_candidate_tokens(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """No token-strategy regression: all 17 impls declared TENANT+USER, as does this path."""
    cap, _ = _call(monkeypatch, label)
    assert {t.name for t in _shape(cap.request)["tokens"]} == WAS_TOKENS


# ------------------------------------------------------------- the six kept tools

# Endpoints whose payload a table cannot express, each pointing back at the tool that
# survived. ``_present`` looks a field up by top-level key across (body, query, paths) —
# it cannot reach ``tables[i].name`` or ``records[i].fields``, so the checks that matter
# for these six live in Python and the rule's job is to say so before a request goes out.
KEPT_TOOLS = [
    ("POST", "/open-apis/bitable/v1/apps/:app_token/tables", "feishu_bitable_create_table"),
    (
        "POST",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_create",
        "feishu_bitable_create_records",
    ),
    (
        "POST",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_update",
        "feishu_bitable_update_records",
    ),
    ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/search", "feishu_bitable_search_records"),
    ("PUT", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id", "feishu_bitable_update_field"),
    (
        "PUT",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id",
        "feishu_bitable_update_record",
    ),
]


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_endpoint_refuses_and_names_the_tool(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, tool: str
) -> None:
    """The refusal must arrive before any request, and must name the tool to use instead."""
    concrete = uri.replace(":app_token", "appA").replace(":table_id", "tblA")
    concrete = concrete.replace(":field_id", "fldX").replace(":record_id", "recA")
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps(
            {
                k: v
                for k, v in {
                    "app_token": "appA",
                    "table_id": "tblA",
                    "field_id": "fldX",
                    "record_id": "recA",
                }.items()
                if f":{k}" in uri
            }
        ),
        body_json="{}",
    )
    assert out["ok"] is False, f"{method} {concrete} should refuse, got {out}"
    assert out["tool"] == tool
    assert cap.requests == [], "a refusal must not send anything"


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_rule_says_why(method: str, uri: str, tool: str) -> None:
    """``why`` is what the model reads to know the tool is not a detour but the only route."""
    rule = _rule(method, uri)
    assert rule.prefer_tool == tool
    assert rule.prefer_hard is True
    assert rule.why.strip(), f"{method} {uri}: a hard refusal without a reason reads as arbitrary"


def test_kept_tools_still_exist() -> None:
    """A hard rule pointing at a deleted tool would be a dead end with no way forward."""
    tools: Any = importlib.import_module("feishu_bitable")

    for _, _, tool in KEPT_TOOLS:
        assert hasattr(tools, tool), f"{tool} is named by a hard rule but no longer exists"


# --------------------------------------------------------- refusals stay in their lane

# Bitable has the worst URI overlap of any domain: ``POST .../tables`` is a hard refusal and
# also the prefix of ``.../tables/batch_create``, ``.../tables/batch_delete`` and
# ``.../tables/:table_id/views``. ``Rule.matches`` is segment-wise *prefix* matching, so a
# blocking rule used to strand every endpoint nested under it — the exact bug that shipped
# four unsendable message endpoints. ``rules_for`` now downgrades a prefix hit to advice.
#
# Every entry here is an endpoint the table does *not* declare. Declared ones nested under a
# blocking rule (``.../tables/batch_create`` and ``.../tables/batch_delete``) are covered by
# the parity and confirmation tests instead — those are enforced on purpose.
SHOULD_NOT_BE_SWALLOWED = [
    # nested under the hard `POST .../tables`. This used to be `.../tables/:table_id/views`,
    # which stopped qualifying once the views endpoints got rules of their own — a declared
    # endpoint is enforced on purpose, so proving it *isn't* enforced would assert the
    # opposite of the intent. `field_groups` is a real Feishu endpoint the table still does
    # not declare, so it keeps testing what this list is for.
    (
        "POST",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/field_groups",
        {"app_token": "appA", "table_id": "tblA"},
    ),
    # nested under the hard `POST .../records/batch_create` and friends
    (
        "POST",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_create/x",
        {"app_token": "appA", "table_id": "tblA"},
    ),
    # nested under the hard `PUT .../records/:record_id`
    (
        "PUT",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id/anything",
        {"app_token": "appA", "table_id": "tblA", "record_id": "recA"},
    ),
    # nested under the hard `PUT .../fields/:field_id`
    (
        "PUT",
        "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id/anything",
        {"app_token": "appA", "table_id": "tblA", "field_id": "fldX"},
    ),
]


@pytest.mark.parametrize(("method", "uri", "paths"), SHOULD_NOT_BE_SWALLOWED)
def test_nested_endpoint_is_not_refused_by_its_parent(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, paths: dict[str, str]
) -> None:
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps(paths),
        body_json='{"x": 1}',
    )
    assert out["ok"] is True, f"{method} {uri} was stranded by a parent rule: {out}"
    assert len(cap.requests) == 1


@pytest.mark.parametrize(("method", "uri", "paths"), SHOULD_NOT_BE_SWALLOWED)
def test_nested_endpoint_inherits_no_defaults(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, paths: dict[str, str]
) -> None:
    """An inherited ``default`` would silently add a field the child never declared."""
    cap, _ = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps(paths),
        body_json='{"x": 1}',
    )
    assert cap.request.body == {"x": 1}, "a prefix-matched rule must not inject body fields"
    assert cap.request.queries in ([], None), "a prefix-matched rule must not inject queries"


def test_batch_delete_tables_still_needs_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping tables is unrecoverable, and it is nested under a hard rule — both must hold."""
    cap, out = _call(monkeypatch, "delete_tables", confirm="")
    assert out["ok"] is False
    assert out["code"] == "need_confirmation"
    assert cap.requests == []


def test_batch_delete_tables_confirmation_is_not_self_serviceable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule's ``confirm`` value is a label, not a password the model may echo."""
    cap, out = _call(monkeypatch, "delete_tables", confirm="DELETE_BITABLE_TABLES")
    assert out["ok"] is False
    assert out["code"] == "need_confirmation"
    assert cap.requests == []


def test_batch_delete_tables_proceeds_once_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "delete_tables")
    assert out["ok"] is True, out
    assert _sent(cap.request) == _was("delete_tables")


def test_declared_nested_endpoints_are_still_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prefix fix must not have loosened endpoints that declare their own rule.

    ``.../tables/batch_create`` sits under the hard ``POST .../tables`` yet has its own row,
    so it keeps full authority: a body with no ``tables`` is refused before the request.
    """
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/bitable/v1/apps/:app_token/tables/batch_create",
        paths_json=json.dumps({"app_token": "appA"}),
        body_json="{}",
    )
    assert out["ok"] is False
    assert out["code"] == "spec_violation"
    assert "tables" in " ".join(out["violations"])
    assert cap.requests == []


# ---------------------------------------------------------------------- constraints


def test_create_app_requires_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(monkeypatch, method="POST", uri="/open-apis/bitable/v1/apps", body_json="{}")
    assert out["ok"] is False
    assert out["code"] == "spec_violation"
    assert "name" in " ".join(out["violations"])
    assert cap.requests == []


def test_create_field_requires_name_and_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu rejects a field with no type, but with a confusing error about the whole body."""
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields",
        paths_json=json.dumps({"app_token": "appA", "table_id": "tblA"}),
        body_json=json.dumps({"field_name": "F"}),
    )
    assert out["ok"] is False
    assert "type" in " ".join(out["violations"])
    assert cap.requests == []


def test_unbuildable_field_type_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Type 19 (查找引用) cannot be created through the API. The ``choices`` list says so, and
    the message has to name the way out, not just report a bad value."""
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields",
        paths_json=json.dumps({"app_token": "appA", "table_id": "tblA"}),
        body_json=json.dumps({"field_name": "F", "type": 19}),
    )
    assert out["ok"] is False
    assert "19" in " ".join(out["violations"])
    assert cap.requests == []


def test_get_record_defaults_to_the_shareable_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deleted tool hardcoded ``with_shared_url=true``; a rule that only mentioned it in
    prose would have quietly dropped the direct link to a row."""
    cap, _ = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id",
        paths_json=json.dumps({"app_token": "appA", "table_id": "tblA", "record_id": "recA"}),
    )
    assert ("with_shared_url", "true") in [(k, str(v)) for k, v in cap.request.queries]


def test_add_role_member_defaults_the_id_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``member_id_type`` Feishu reads an ``ou_`` id as the wrong kind of id."""
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/bitable/v1/apps/:app_token/roles/:role_id/members",
        paths_json=json.dumps({"app_token": "appA", "role_id": "rolA"}),
        body_json=json.dumps({"member_id": "ou_a"}),
    )
    assert ("member_id_type", "open_id") in [(k, str(v)) for k, v in cap.request.queries]


# --------------------------------------------------------------------------- paging


@pytest.mark.parametrize("label", sorted(PAGED))
def test_listing_endpoints_follow_page_token(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """The hand-written loops concatenated pages; the ``paged`` row has to do the same."""
    spec = CALLS[label]
    cap, out = _generic(
        monkeypatch,
        pages=[
            {"ok": True, "data": {"items": [{"i": 1}], "has_more": True, "page_token": "p2"}},
            {"ok": True, "data": {"items": [{"i": 2}], "has_more": False}},
        ],
        method=spec["method"],
        uri=spec["uri"],
        paths_json=json.dumps(spec.get("paths", {})),
        query_json=json.dumps(spec.get("query", {})),
    )
    assert len(cap.requests) == 2, f"{label}: did not follow page_token"
    assert ("page_token", "p2") in [(k, str(v)) for k, v in cap.requests[1].queries]
    # The collected items are hoisted to the top level under the declared ``items`` key,
    # alongside ``count``/``pages`` — the caller never sees the page boundary.
    assert out["items"] == [{"i": 1}, {"i": 2}]
    assert out["count"] == 2
    assert out["pages"] == 2


@pytest.mark.parametrize("label", sorted(PAGED))
def test_listing_endpoints_declare_paging(label: str) -> None:
    """Without ``paginate`` the row returns page 1 and silently loses the rest of a big base."""
    spec = CALLS[label]
    rule = _rule(spec["method"], spec["uri"])
    assert rule.paginate, f"{label} is a listing endpoint but the row declares no paginate"
    assert rule.paginate["items"] == "items"
    assert rule.paginate["page_size"] == 100


# -------------------------------------------------------------------------- pitfalls

# Facts that cost a debugging session each and that no signature can carry: which body key a
# batch endpoint wants, which name characters Feishu rejects, which column names are dropped
# in silence. ``_present`` is flat-only — it cannot look inside ``tables[i]`` or ``fields`` —
# so these live as prose, and the test holds the prose in place.
PITFALL_FACTS = [
    ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_delete", "records"),
    ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/batch_create", "/"),
    ("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields", "property"),
    ("DELETE", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id", "1254046"),
]


@pytest.mark.parametrize(("method", "uri", "needle"), PITFALL_FACTS)
def test_pitfall_text_survives(method: str, uri: str, needle: str) -> None:
    rule = _rule(method, uri)
    blob = " ".join(rule.pitfalls)
    assert needle in blob, f"{method} {uri}: pitfall text lost the {needle!r} warning"


def test_table_name_charset_warning_is_documented() -> None:
    """The deleted tool checked ``tables[i].name`` in Python. ``_present`` cannot reach a field
    inside an array, so this became prose — acceptable only because Feishu *errors* on a bad
    name instead of accepting it silently, unlike a mismatched column."""
    rule = _rule("POST", "/open-apis/bitable/v1/apps/:app_token/tables/batch_create")
    blob = " ".join(rule.pitfalls)
    assert all(ch in blob for ch in ("/", "?", "*", "[")), "the rejected-character list got trimmed"
