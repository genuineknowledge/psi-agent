"""Parity: the last two pure forwards reach Feishu the same way their tools did.

Not a domain this time — two leftovers. After ten domains moved, an audit of the four
remaining ones (``calendar`` / ``sheet`` / ``doc`` / ``wiki``) found only 2 of their 25
tools were pure forwards: ``feishu_sheet_tabs`` and ``feishu_wiki_get_node``. Everything
else transforms (flattening mention cells, parsing timestamps), orchestrates (resolve the
primary calendar, then create), or bypasses ``_invoke`` outright. So these two rows land
in ``skills/feishu-api/SKILL.md`` — beside the table rows they belong to — rather than in
a new per-domain skill, and their sibling tools stay.

``sheets/query`` has since moved on: when the spreadsheet domain got its own endpoint
table (``skills/feishu-sheet``), its rule went with it, because a rule belongs beside the
table a reader would look it up in — and because two rules for one endpoint at equal
specificity are resolved by filename order rather than by anyone's decision. The wire
parity asserted below is unaffected: both endpoints still go out through the same generic
path. ``RULE_HOME`` records which file each one lives in.

Both endpoints already appear in their skill's endpoint tables. What the ``rules`` block
adds is the part a table cannot execute, and for these two it is the same shape of
mistake in both: **a successful response that means nothing**.

- ``sheets/query`` answers with ``grid_properties.row_count``, which is the sheet's
  *extent*, not how many rows hold data. Reading that range back gives a wall of blanks.
- ``spaces/get_node`` answers ``code: 0`` with an **empty** ``data.node`` when the bot is
  not a member of the space, rather than a permission error.

The second one is why this file also pins a behavior change rather than pure parity.
``get_wiki_node_impl`` routed through ``_invoke_wiki_read``, which watched for that empty
success and silently retried as the user. ``token: tenant_then_user`` retries on denial
only — the vocabulary has no way to say "retry when the answer is empty". Rather than
grow the vocabulary for one endpoint, the retry moves into prose the model reads: the
rule's own ``pitfalls`` say to re-ask with ``prefer="user"``. ``_invoke_wiki_read``
itself stays, because the two wiki *list* reads still use it and still keep their tools.
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
API_SKILL = SKILLS_DIR / "feishu-api" / "SKILL.md"

#: Both endpoints declare TENANT and USER as candidates; ``prefer`` picks the send path.
WAS_TOKENS = {"TENANT", "USER"}

# The wire contract of the 2 tools this replaced, captured by running each builder at
# migration time and frozen here. These literals are what Feishu received before.
WAS: dict[str, dict[str, Any]] = {
    "sheet_tabs": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "paths": {"spreadsheet_token": "shtcnABC"},
        "queries": [],
        "body": None,
    },
    "wiki_get_node": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/wiki/v2/spaces/get_node",
        # No path placeholder at all — the node token travels in the query string.
        "paths": {},
        "queries": [("token", "NFOnwDvr")],
        "body": None,
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    "sheet_tabs": {
        "method": "GET",
        "uri": "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "paths": {"spreadsheet_token": "shtcnABC"},
    },
    "wiki_get_node": {
        "method": "GET",
        "uri": "/open-apis/wiki/v2/spaces/get_node",
        "query": {"token": "NFOnwDvr"},
    },
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
    """One frozen wire shape, normalized the way ``_sent`` normalizes a live one."""
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


def _call(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    pages: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    """Invoke one ``CALLS`` entry through the generic path."""
    spec = {**CALLS[label], **overrides}
    return _generic(
        monkeypatch,
        pages=pages,
        method=spec["method"],
        uri=spec["uri"],
        paths_json=json.dumps(spec.get("paths", {})),
        body_json=json.dumps(spec.get("body", {}), ensure_ascii=False),
        query_json=json.dumps(spec.get("query", {})),
        user_key=spec.get("user_key", ""),
        prefer=spec.get("prefer", "tenant"),
    )


#: Which skill file each of the two rules lives in now. ``sheets/query`` started here and
#: moved to ``feishu-sheet`` when that domain got its own endpoint table — an endpoint's
#: rule belongs beside the table a reader would find it in, and a rule declared twice at
#: equal specificity would be resolved by filename order rather than on purpose. The wire
#: parity below is unchanged by the move: it goes through the same generic path either way.
RULE_HOME = {
    "sheet_tabs": SKILLS_DIR / "feishu-sheet" / "SKILL.md",
    "wiki_get_node": API_SKILL,
}


def _rules(skill: Path = API_SKILL) -> list[Any]:
    return _spec.parse_rules(skill.read_text(encoding="utf-8"))


def _rule(method: str, uri: str, skill: Path = API_SKILL) -> Any:
    match = [r for r in _rules(skill) if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule in {skill.parent.name}, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


def test_api_skill_keeps_only_the_wiki_rule() -> None:
    """``feishu-api``'s ``rules`` block holds one rule now, not two.

    The skill's job is the endpoint *tables*; a rule lives here only when its endpoint has
    no domain skill to belong to. ``sheets/query`` acquired one (``feishu-sheet``) and its
    rule went with it — see ``RULE_HOME``. If a second rule shows up here, it belongs to a
    domain skill unless it earns the same argument this one does.
    """
    assert {(r.method, r.uri) for r in _rules()} == {("GET", "/open-apis/wiki/v2/spaces/get_node")}


@pytest.mark.parametrize("label", sorted(WAS))
def test_every_endpoint_is_also_a_table_row(label: str) -> None:
    """Both views of the fact exist: the human table row and the executable rule.

    They may live in different skill files, but each rule has to sit beside a table row in
    *its own* file — a rule the reader of that document never sees documented is a rule
    they will not know to use.
    """
    skill = RULE_HOME[label]
    text = skill.read_text(encoding="utf-8")
    uri = WAS[label]["uri"]
    # Once in the table, once in the rules block — drift becomes a visible diff.
    assert text.count(uri) >= 2, f"{uri} should appear in both the table and the rules of {skill.parent.name}"


def test_exactly_one_skill_declares_each_of_the_two() -> None:
    """Neither endpoint may be declared twice across the whole skills tree.

    Two rules for one endpoint at equal specificity are resolved by filename order, which
    is not a decision anyone made. This is the check that would have caught the sheets
    ``PATCH`` being declared in both ``feishu-drive`` and ``feishu-sheet``.
    """
    everything = _spec.load_rules(SKILLS_DIR)
    for label, home in RULE_HOME.items():
        method, uri = WAS[label]["method"].name, WAS[label]["uri"]
        owners = [r.source for r in everything if r.method == method and r.uri == uri]
        assert owners == [home.parent.name], f"{method} {uri} is declared by {owners}"


# ------------------------------------------------------------------ wire-shape parity


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_rebuilds_the_frozen_request(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Byte-for-byte the same request the deleted tool sent."""
    cap, out = _call(monkeypatch, label)
    assert out["ok"] is True
    assert _sent(cap.request) == _want(label)


@pytest.mark.parametrize("label", sorted(WAS))
def test_token_candidates_are_declared_for_every_endpoint(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Both endpoints stay tenant-or-user, as the builders declared them."""
    cap, _ = _call(monkeypatch, label)
    got = {str(t).rsplit(".", 1)[-1] for t in (cap.request.token_types or [])}
    assert got == WAS_TOKENS


@pytest.mark.parametrize("label", sorted(WAS))
def test_prefer_tenant_is_the_default_send_path(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Reads try the bot first and fall back to the caller only when denied."""
    cap, _ = _call(monkeypatch, label, user_key="ou_zhang")
    assert cap.kwargs[0].get("prefer") == "tenant"
    assert cap.kwargs[0].get("user_key") == "ou_zhang"


def test_wiki_node_token_is_not_a_uri_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """``spaces/get_node`` takes no path parameter — the token is a query value.

    Worth pinning because every neighbouring wiki endpoint *does* have a placeholder
    (``/spaces/:space_id/nodes``), so the natural guess is ``/spaces/:token/get_node``,
    which is a 404 rather than a validation error.
    """
    cap, _ = _call(monkeypatch, "wiki_get_node")
    assert cap.request.paths == {} or cap.request.paths is None
    assert ("token", "NFOnwDvr") in [(k, str(v)) for k, v in (cap.request.queries or [])]


def test_sheet_token_is_a_uri_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror image: ``sheets/query`` carries its token in the path, not the query."""
    cap, _ = _call(monkeypatch, "sheet_tabs")
    assert cap.request.paths == {"spreadsheet_token": "shtcnABC"}
    assert list(cap.request.queries or []) == []


def test_wiki_node_token_is_required() -> None:
    """Omitting it is caught before the request, not answered with an empty node."""
    rule = _rule("GET", "/open-apis/wiki/v2/spaces/get_node")
    assert "query.token" in rule.required


def test_missing_wiki_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "wiki_get_node", query={})
    assert out["ok"] is False
    assert cap.requests == [], "nothing should reach Feishu when the token is missing"


# ------------------------------------------------- the two silent-success facts, pinned


def test_empty_node_is_not_retried_automatically(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented behavior change, pinned so it cannot drift back unnoticed.

    ``get_wiki_node_impl`` used ``_invoke_wiki_read``, which turned one call into two when
    tenant answered ``code: 0`` with an empty ``node``. The generic path does not: it
    sends once and returns the empty success. That is a real difference, and it is
    deliberate — ``token: tenant_then_user`` retries on *denial*, and teaching the
    vocabulary to retry on emptiness for one endpoint would put a second, invisible
    request behind every rule. Instead the rule's ``pitfalls`` tell the caller to re-ask.
    """
    cap, out = _call(monkeypatch, "wiki_get_node", pages=[{"ok": True, "data": {}}], user_key="ou_zhang")
    assert out["ok"] is True
    assert len(cap.requests) == 1, "the generic path must not silently send a second request"


def test_empty_node_pitfall_names_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Since the retry is now the caller's move, the rule has to say so."""
    rule = _rule("GET", "/open-apis/wiki/v2/spaces/get_node")
    joined = " ".join(rule.pitfalls)
    assert "prefer=user" in joined, "the rule must name the identity to retry as"
    assert "user_key" in joined
    # And it has to explain *why*, or the model reads an empty node as "does not exist".
    assert "空" in joined


def test_wiki_read_helper_survives_for_the_list_reads() -> None:
    """``_invoke_wiki_read`` is not orphaned by this change — two callers remain.

    Both wiki *list* reads keep their tools (they project fields the table cannot), and
    both still need the empty-success retry. Deleting the helper alongside the node read
    would quietly break them.
    """
    assert callable(getattr(_impl, "_invoke_wiki_read", None))


def test_sheet_extent_pitfall_is_stated() -> None:
    """``row_count`` is the sheet's extent, not its data.

    This is the one that costs a whole conversation rather than an error: feed
    ``row_count`` into a range read and you get thousands of blank rows back. The tool
    could not have prevented it either — it returned the same number — but the rule can
    say what the number means.
    """
    rule = _rule("GET", "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query", RULE_HOME["sheet_tabs"])
    joined = " ".join(rule.pitfalls)
    assert "row_count" in joined
    assert "grid_properties" in joined, "the field's real location has to be stated"
    assert "sheet_id" in joined, "and that the range prefix is not in the sheet URL"


def test_wiki_hosted_sheet_needs_obj_token_first() -> None:
    """The two rules point at each other, because that is the real call order.

    Now that they live in different skill files, this cross-reference is the only thing
    connecting them — a reader who opens ``feishu-sheet`` for a wiki-hosted spreadsheet has
    no other way to learn they need the wiki lookup first.
    """
    rule = _rule("GET", "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query", RULE_HOME["sheet_tabs"])
    assert any("get_node" in p for p in rule.pitfalls)


# ------------------------------------------------------ the sibling tools are still here


@pytest.mark.parametrize(
    "name",
    ["read_sheet_range_impl", "write_sheet_impl", "append_sheet_impl", "format_sheet_impl"],
)
def test_sheet_transforming_tools_are_kept(name: str) -> None:
    """Only the tab listing moved. The four that transform or validate stay in Python."""
    assert callable(getattr(_impl, name, None))


@pytest.mark.parametrize(
    "name",
    ["list_wiki_spaces_impl", "list_wiki_nodes_impl", "create_wiki_node_impl", "create_wiki_space_impl"],
)
def test_wiki_projecting_tools_are_kept(name: str) -> None:
    """Same for wiki: the node resolve moved, the listings and the creates did not."""
    assert callable(getattr(_impl, name, None))


def test_shared_sheet_meta_builder_is_kept() -> None:
    """``_build_sheet_meta_request`` has a second caller (the range read resolves the
    sheet's extent through it), so it outlives the tool that made it worth naming."""
    assert callable(getattr(_impl, "_build_sheet_meta_request", None))
