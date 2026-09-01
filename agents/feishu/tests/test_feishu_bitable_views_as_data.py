"""Views, dashboards and automation: the bitable endpoints that never had a tool.

The rest of ``feishu-bitable`` was migrated *from* Python, so those tests freeze what a
deleted builder used to send. These nine had no builder — views, dashboards and workflows
were simply missing — so the shapes below are the requests the official documentation
describes, field for field, and the tests prove the generic ``feishu_api`` path produces
exactly those. ``test_feishu_bitable_as_data.py`` owns the migrated set and lists these in
its ``NEVER_A_TOOL`` so neither file's closed-set assertion goes soft.

Three facts here are worth a test rather than a sentence, because each one fails *silently*
or fails in a way a caller would misread:

``view_type`` is a closed set of five. Feishu answers 1254019 for anything else, and the
plausible-looking guesses (``table``, ``calendar``, ``board``) are all wrong — a model that
reasons from the UI's Chinese labels will produce them. The refusal is local, so it costs no
HTTP call and says which five are real.

``filter_info.conditions`` are keyed by ``field_id``, never by column name. Every other
bitable write in this repo takes 列名 — ``create_records``, ``update_record``,
``search_records`` all do — so the one endpoint that breaks the pattern is exactly where a
caller will pass a name and get 1254009. That asymmetry is pinned as pitfall text.

``status`` on a workflow is ``Enable``/``Disable``, capitalized. Neither ``enable`` nor
``true`` nor ``open`` works, and this endpoint is reached most often in a
"disable → bulk import → re-enable" sequence, where a rejected re-enable leaves the base's
automations off. So the choices are enforced and the "it won't turn itself back on" warning
is asserted to still be there.

The last-view guard (1254023) and the absence of any create-a-dashboard or create-a-workflow
API are recorded as pitfalls too: those are things Feishu does not let you do, and a caller
who doesn't know will keep retrying a call that cannot succeed.
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
BITABLE_SKILL = SKILLS_DIR / "feishu-bitable" / "SKILL.md"

#: Every one of these works under either identity; ``prefer`` picks the send path.
WAS_TOKENS = {"TENANT", "USER"}

APP = "bascnmBA0lQ1"
TABLE = "tblsRc9GRRXKqhvW"
VIEW = "vewTpR1urY"
BLOCK = "blk_6c6a3b6d5e9f7c8d"
WORKFLOW = "wfl_7a1b2c3d4e5f6a7b"

#: The wire shape each tabled endpoint must produce, from the official docs.
WAS: dict[str, dict[str, Any]] = {
    # The two paged rows carry the ``page_size`` their rule injects: a declared
    # ``paginate`` is part of the wire shape, not a detail of the send loop.
    "list_views": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views",
        "paths": {"app_token": APP, "table_id": TABLE},
        "queries": [("page_size", "100")],
        "body": None,
    },
    "get_view": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id",
        "paths": {"app_token": APP, "table_id": TABLE, "view_id": VIEW},
        "queries": [],
        "body": None,
    },
    "create_view": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views",
        "paths": {"app_token": APP, "table_id": TABLE},
        "queries": [],
        "body": {"view_name": "进行中", "view_type": "kanban"},
    },
    "patch_view": {
        "method": HttpMethod.PATCH,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id",
        "paths": {"app_token": APP, "table_id": TABLE, "view_id": VIEW},
        "queries": [],
        "body": {
            "view_name": "进行中(本周)",
            "property": {
                "filter_info": {
                    "conjunction": "and",
                    "conditions": [{"field_id": "fldPFAOoRO", "operator": "is", "value": ["进行中"]}],
                },
                "hidden_fields": ["fldbMHOSbA"],
            },
        },
    },
    "delete_view": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id",
        "paths": {"app_token": APP, "table_id": TABLE, "view_id": VIEW},
        "queries": [],
        "body": None,
    },
    "list_dashboards": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/dashboards",
        "paths": {"app_token": APP},
        "queries": [("page_size", "100")],
        "body": None,
    },
    "copy_dashboard": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/bitable/v1/apps/:app_token/dashboards/:block_id/copy",
        "paths": {"app_token": APP, "block_id": BLOCK},
        "queries": [],
        "body": {"name": "2026-08 月度看板"},
    },
    "list_workflows": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/bitable/v1/apps/:app_token/workflows",
        "paths": {"app_token": APP},
        "queries": [],
        "body": None,
    },
    "toggle_workflow": {
        "method": HttpMethod.PUT,
        "uri": "/open-apis/bitable/v1/apps/:app_token/workflows/:workflow_id",
        "paths": {"app_token": APP, "workflow_id": WORKFLOW},
        "queries": [],
        "body": {"status": "Disable"},
    },
}

#: Endpoints whose rule declares ``paginate``, so one call drains pages.
PAGED = {"list_views", "list_dashboards"}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``. The
#: caller never passes ``page_size`` — the rule's ``paginate`` supplies it — so it is
#: dropped here while staying in the expected shape above.
CALLS: dict[str, dict[str, Any]] = {
    label: {
        "method": WAS[label]["method"].name,
        "uri": WAS[label]["uri"],
        "paths": WAS[label]["paths"],
        "body": WAS[label]["body"] or {},
        "query": {k: v for k, v in WAS[label]["queries"] if not (label in PAGED and k == "page_size")},
    }
    for label in WAS
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
    want["queries"] = sorted(want["queries"])
    return want


class _CapturedInvoke:
    """Stands in for ``_invoke`` and keeps the request instead of sending it.

    ``pages`` lets a paged endpoint terminate: the send loop follows ``page_token`` until
    ``has_more`` is false, so a bare ``{}`` would be read as a first page with no items and
    the loop would stop for the wrong reason.
    """

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[BaseRequest] = []
        self.kwargs: list[dict[str, Any]] = []
        self._pages = list(pages or [])

    async def __call__(self, request: BaseRequest, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        if self._pages:
            return self._pages.pop(0)
        return {"ok": True, "data": {}}

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


#: The key each paged endpoint's rule declares for its rows. ``dashboards`` is the odd one:
#: getting it wrong is not a crash but an empty result for a base full of dashboards.
PAGE_ITEMS_KEY = {"list_dashboards": "dashboards"}


def _pages_for(label: str) -> list[dict[str, Any]] | None:
    """A single terminal page, under whichever key this endpoint's rule declares."""
    if label not in PAGED:
        return None
    return [{"ok": True, "data": {PAGE_ITEMS_KEY.get(label, "items"): [], "has_more": False}}]


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
    )


def _rules() -> list[Any]:
    return _spec.parse_rules(BITABLE_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


@pytest.mark.parametrize("label", sorted(WAS))
def test_every_endpoint_has_a_rule_and_a_table_row(label: str) -> None:
    """Both views of the fact exist: the human table row and the executable rule.

    Counting the URI twice is what makes drift a visible diff — a rule with no row is a
    capability the reader never learns about, and a row with no rule is unenforced prose.
    """
    _rule(WAS[label]["method"].name, WAS[label]["uri"])
    text = BITABLE_SKILL.read_text(encoding="utf-8")
    assert text.count(WAS[label]["uri"]) >= 2, f"{WAS[label]['uri']} needs a table row and a rule"


@pytest.mark.parametrize("label", sorted(WAS))
def test_exactly_one_skill_declares_each_endpoint(label: str) -> None:
    """No other skill claims these URIs.

    Two rules for one endpoint at equal specificity are resolved by filename order, which is
    nobody's decision. ``feishu-wiki`` was added in the same change and both files talk about
    bitable tokens, so this is the check that catches an endpoint drifting into two homes.
    """
    everything = _spec.load_rules(SKILLS_DIR)
    method, uri = WAS[label]["method"].name, WAS[label]["uri"]
    owners = [r.source for r in everything if r.method == method and r.uri == uri]
    assert owners == ["feishu-bitable"], f"{method} {uri} is declared by {owners}"


# ------------------------------------------------------------------- wire-shape parity


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_builds_the_documented_request(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Each call goes out as the shape Feishu's docs describe."""
    cap, out = _call(monkeypatch, label)
    assert out["ok"] is True, f"{label} was refused: {out}"
    assert _sent(cap.request) == _want(label)


@pytest.mark.parametrize("label", sorted(WAS))
def test_both_candidate_tokens_stay_declared(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """The SDK declares TENANT+USER for all nine; ``prefer`` selects, it does not narrow."""
    cap, _ = _call(monkeypatch, label)
    assert {t.name for t in cap.request.token_types} == WAS_TOKENS


@pytest.mark.parametrize("label", sorted(PAGED))
def test_paged_endpoints_declare_their_items_key(label: str) -> None:
    """``dashboards`` is not ``items`` — a wrong key silently drains nothing.

    Feishu's paging protocol is uniform, but the key holding the page's rows is not:
    the dashboard list returns ``dashboards``. Declaring ``items`` there would loop while
    reading an absent list, so the key is part of the contract.
    """
    rule = _rule(WAS[label]["method"].name, WAS[label]["uri"])
    assert rule.paginate, f"{label} should declare paginate"
    assert rule.paginate["items"] == ("dashboards" if label == "list_dashboards" else "items")


def test_listing_workflows_does_not_paginate() -> None:
    """Feishu returns every workflow in one response; there is no page_token to follow."""
    assert _rule("GET", "/open-apis/bitable/v1/apps/:app_token/workflows").paginate is None


# --------------------------------------------------------------- the closed value sets

#: The five real view types, and the guesses a model reasoning from the UI would produce.
#: ``table`` for 表格视图, ``board`` / ``calendar`` for the Chinese labels of kanban and the
#: calendar view (which this endpoint does not offer at all).
BAD_VIEW_TYPES = ["table", "board", "calendar", "list", "Grid", "GRID", "timeline"]


def test_view_type_accepts_exactly_the_five_real_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    for good in ("grid", "kanban", "gallery", "gantt", "form"):
        cap, out = _call(monkeypatch, "create_view", body={"view_name": "V", "view_type": good})
        assert out["ok"] is True, f"{good} should be accepted: {out}"
        assert cap.request.body["view_type"] == good


@pytest.mark.parametrize("bad", BAD_VIEW_TYPES)
def test_wrong_view_type_is_refused_before_any_http(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """1254019 never happens: the refusal is local and names the five that work."""
    cap, out = _call(monkeypatch, "create_view", body={"view_name": "V", "view_type": bad})
    assert out["ok"] is False
    assert not cap.requests, f"{bad} reached the network"
    assert "grid" in str(out) and "kanban" in str(out)


def test_creating_a_view_requires_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "create_view", body={"view_type": "grid"})
    assert out["ok"] is False
    assert "view_name" in str(out)
    assert not cap.requests


@pytest.mark.parametrize("label", ["create_view", "patch_view"])
@pytest.mark.parametrize("bad_name", ["", "x" * 101, "预算[草稿]", "a]b"])
def test_view_name_length_and_brackets_are_enforced(monkeypatch: pytest.MonkeyPatch, label: str, bad_name: str) -> None:
    """A ``max:`` here would check nothing — it coerces with ``float()`` and passes strings.

    That silent no-op shipped three times in this repo before it was noticed, so both view
    endpoints spell the cap as a ``pattern`` and both are tested. Brackets are a separate
    ``forbid``: Feishu answers 1254022 for a name containing ``[`` or ``]``.
    """
    body = {**CALLS[label]["body"], "view_name": bad_name}
    cap, out = _call(monkeypatch, label, body=body)
    assert out["ok"] is False, f"{bad_name!r} should be refused"
    assert not cap.requests


def test_a_101_char_name_is_refused_but_100_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boundary itself, since an off-by-one in the pattern would pass every test above."""
    ok_cap, ok_out = _call(monkeypatch, "create_view", body={"view_name": "x" * 100, "view_type": "grid"})
    assert ok_out["ok"] is True and ok_cap.requests
    bad_cap, bad_out = _call(monkeypatch, "create_view", body={"view_name": "x" * 101, "view_type": "grid"})
    assert bad_out["ok"] is False and not bad_cap.requests


def test_workflow_status_is_capitalized_enable_or_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    for good in ("Enable", "Disable"):
        cap, out = _call(monkeypatch, "toggle_workflow", body={"status": good})
        assert out["ok"] is True, f"{good} should be accepted: {out}"
        assert cap.request.body["status"] == good


@pytest.mark.parametrize("bad", ["enable", "disable", "ENABLE", "true", "open", "closed", "on", "off"])
def test_wrong_workflow_status_is_refused(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """A rejected re-enable would leave the base's automations off, so this fails loudly."""
    cap, out = _call(monkeypatch, "toggle_workflow", body={"status": bad})
    assert out["ok"] is False
    assert not cap.requests
    assert "Enable" in str(out)


def test_toggling_a_workflow_requires_a_status(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "toggle_workflow", body={})
    assert out["ok"] is False
    assert "status" in str(out)
    assert not cap.requests


def test_copying_a_dashboard_requires_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "copy_dashboard", body={})
    assert out["ok"] is False
    assert "name" in str(out)
    assert not cap.requests


# ------------------------------------------------------- facts that only prose can carry


@pytest.mark.parametrize(
    "fact",
    [
        # The one endpoint in this domain keyed by field_id instead of 列名.
        "field_id",
        # Sending a partial `property` can clear the other half of it.
        "先 GET 读回当前 property 再改",
        # Feishu refuses to delete a table's only view.
        "1254023",
        # Neither a dashboard nor a workflow can be created through the API at all.
        "飞书没有新建仪表盘",
        "建流程、改流程内容都没有接口",
        # The disable → import → enable sequence does not undo itself.
        "停用状态不会自己恢复",
        # Attachment columns need an upload first, and the value is an array of objects.
        "bitable_file",
    ],
)
def test_pitfall_text_survives(fact: str) -> None:
    """These are the sentences a reader has to see; a rewrite that drops one fails here."""
    assert fact in BITABLE_SKILL.read_text(encoding="utf-8"), f"missing from the skill: {fact}"


def test_field_id_asymmetry_is_stated_in_the_rule_not_only_the_prose() -> None:
    """The rule carries it too, so it shows up at refusal time rather than only when read.

    Every other bitable write in this repo takes 列名; this one takes ``field_id``. A caller
    who generalizes gets 1254009, which reads like a bad token rather than "wrong key kind".
    """
    rule = _rule("PATCH", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views/:view_id")
    assert any("field_id" in p for p in rule.pitfalls)


def test_type_19_stays_documented_as_uncreatable() -> None:
    """The create-field enum has no 19, and the doc says so outright — record both.

    Row #36 of the gap list asked for a 查找引用 field creator. It cannot be built, so the
    honest outcome is this sentence rather than an endpoint that always fails.
    """
    text = BITABLE_SKILL.read_text(encoding="utf-8")
    assert "19（查找引用）建不出来" in text  # noqa: RUF001 — the skill's own wording, fullwidth parens included
    assert "不支持新增 19 查找引用字段类型" in text
    rule = _rule("POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields")
    assert 19 not in rule.fields["type"]["choices"]
