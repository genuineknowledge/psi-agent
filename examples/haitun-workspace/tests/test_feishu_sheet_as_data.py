"""The ``feishu-sheet`` skill's endpoint table builds the requests Feishu documents.

Unlike the earlier migrations, this domain's tabled endpoints never had a tool to compare
against — spreadsheet *structure* (create, rename, add/insert/delete rows and columns,
merge, find/replace) was simply missing. So the frozen shapes below are the requests the
official documentation describes, field for field, and the tests prove the generic
``feishu_api`` path produces exactly those.

What earns this domain its own file is one asymmetry that no amount of prose fixes:

    POST   insert_dimension_range   startIndex/endIndex is 0-based, half-open
    DELETE dimension_range          startIndex/endIndex is 1-based, fully closed

Same two field names, same ``dimension`` wrapper, same spreadsheet — opposite conventions.
``{startIndex: 3, endIndex: 7}`` inserts **four** rows and deletes **five**, and both
calls return success either way, so a caller who generalized from one to the other
silently loses a row of someone's data. Each convention is pinned by its own test, with
the pitfall text asserted, so a later "cleanup" that unifies them fails loudly here.

The four read/write endpoints keep their tools (``feishu_sheet_read`` / ``_write`` /
``_append`` / ``_format``): a range written as a bare ``"<sheet_id>!A1"`` — no end cell —
is answered with ``code: 0`` and an empty ``updatedRange``, having written nothing. Their
rules name the tool but are deliberately **not** ``hard``: the generic tool has warned
rather than refused across ``/open-apis/sheets/`` since before this skill existed, and
tightening that would strand valid hand-built calls. What this file does pin is their
*methods*, because writing values is a ``PUT`` while appending is a ``POST`` and the table
is the only place that difference is written down.
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
SHEET_SKILL = SKILLS_DIR / "feishu-sheet" / "SKILL.md"

#: Every sheets endpoint works under either identity; ``prefer`` picks the send path.
WAS_TOKENS = {"TENANT", "USER"}

SPREADSHEET = "shtcnmBA0lQ1"
SHEET_ID = "0b2aa1"

#: The wire shape each tabled endpoint must produce, from the official docs.
WAS: dict[str, dict[str, Any]] = {
    "create": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v3/spreadsheets",
        "paths": {},
        "queries": [],
        "body": {"title": "季度销售", "folder_token": "fldrABC"},
    },
    "rename": {
        "method": HttpMethod.PATCH,
        "uri": "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {"title": "季度销售(终版)"},
    },
    "list_sheets": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": None,
    },
    "add_sheet": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/sheets_batch_update",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {"requests": [{"addSheet": {"properties": {"title": "新工作表", "index": 1}}}]},
    },
    "append_rows": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {"dimension": {"sheetId": SHEET_ID, "majorDimension": "ROWS", "length": 10}},
    },
    "insert_rows": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/insert_dimension_range",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {
            "dimension": {"sheetId": SHEET_ID, "majorDimension": "ROWS", "startIndex": 3, "endIndex": 7},
            "inheritStyle": "BEFORE",
        },
    },
    "delete_rows": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {"dimension": {"sheetId": SHEET_ID, "majorDimension": "ROWS", "startIndex": 3, "endIndex": 7}},
    },
    "merge": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/merge_cells",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {"range": f"{SHEET_ID}!F11:G12", "mergeType": "MERGE_ALL"},
    },
    "unmerge": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/unmerge_cells",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [],
        "body": {"range": f"{SHEET_ID}!F11:G12"},
    },
    "find": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/:sheet_id/find",
        "paths": {"spreadsheet_token": SPREADSHEET, "sheet_id": SHEET_ID},
        "queries": [],
        "body": {"find_condition": {"range": f"{SHEET_ID}!A1:C5"}, "find": "待跟进"},
    },
    "replace": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/:sheet_id/replace",
        "paths": {"spreadsheet_token": SPREADSHEET, "sheet_id": SHEET_ID},
        "queries": [],
        "body": {"find_condition": {"range": f"{SHEET_ID}!A1:C5"}, "find": "待跟进", "replacement": "已完成"},
    },
    "protected": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/protected_range_batch_get",
        "paths": {"spreadsheet_token": SPREADSHEET},
        "queries": [("protectIds", "pr1,pr2")],
        "body": None,
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    label: {
        "method": WAS[label]["method"].name,
        "uri": WAS[label]["uri"],
        "paths": WAS[label]["paths"],
        "body": WAS[label]["body"] or {},
        "query": dict(WAS[label]["queries"]),
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
    """Stands in for ``_invoke`` and keeps the request instead of sending it."""

    def __init__(self) -> None:
        self.requests: list[BaseRequest] = []
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, request: BaseRequest, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        return {"ok": True, "data": {}}


@pytest.fixture(autouse=True)
def _real_skills(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Drive the generic path from the shipped skill files, not a synthetic fixture."""
    _spec.reset_cache()
    monkeypatch.setattr(_api, "_skills_dir", lambda: str(SKILLS_DIR))
    yield
    _spec.reset_cache()


def _generic(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> tuple[_CapturedInvoke, dict[str, Any]]:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    out: dict[str, Any] = anyio.run(lambda: _api.call_api_impl(**kwargs))
    return cap, out


def _call(monkeypatch: pytest.MonkeyPatch, label: str, **overrides: Any) -> tuple[_CapturedInvoke, dict[str, Any]]:
    """Invoke one ``CALLS`` entry through the generic path."""
    spec = {**CALLS[label], **overrides}
    return _generic(
        monkeypatch,
        method=spec["method"],
        uri=spec["uri"],
        paths_json=json.dumps(spec.get("paths", {})),
        body_json=json.dumps(spec.get("body", {}), ensure_ascii=False),
        query_json=json.dumps(spec.get("query", {})),
    )


def _rules() -> list[Any]:
    return _spec.parse_rules(SHEET_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


def test_skill_declares_every_tabled_endpoint() -> None:
    """Every tabled endpoint has a rule, and the only extras point back at a tool."""
    got = {(r.method, r.uri) for r in _rules()}
    tabled = {(WAS[k]["method"].name, WAS[k]["uri"]) for k in WAS}
    assert tabled <= got, f"tabled endpoint missing a rule: {tabled - got}"
    assert got - tabled == {
        ("GET", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range"),
        ("PUT", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values"),
        ("POST", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append"),
        ("PUT", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style"),
    }


def test_every_rule_has_a_documented_row() -> None:
    """A rule the Markdown never mentions is a rule the model will never pick."""
    text = SHEET_SKILL.read_text(encoding="utf-8")
    table = text.split("```rules")[0]
    for rule in _rules():
        assert rule.uri in table, f"{rule.endpoint} is enforced but undocumented"


# ------------------------------------------------------------- the documented wire shape


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_builds_the_documented_request(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Field for field, the request the skill builds is the one Feishu documents."""
    cap, out = _call(monkeypatch, label)
    assert out.get("ok") is not False, out
    assert cap.requests, f"{label}: no request was built"
    assert _sent(cap.requests[0]) == _want(label)


@pytest.mark.parametrize("label", sorted(WAS))
def test_token_candidates_are_declared_for_every_endpoint(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Both types are always declared; ``prefer`` selects the send path, not this set."""
    cap, _ = _call(monkeypatch, label)
    got = {str(t).split(".")[-1] for t in (cap.requests[0].token_types or set())}
    assert got == WAS_TOKENS


# ------------------------------------------- the two opposite row-index conventions
#
# This is the pair the file exists for. Both endpoints take the same two field names in
# the same wrapper and disagree about what they mean, and both answer success either way.


def test_insert_and_delete_rows_are_different_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inserting is a POST to insert_dimension_range; deleting is a DELETE to dimension_range.

    Sending one to the other's URI is not a 404 — ``POST dimension_range`` exists and
    *appends at the end*, so a mixed-up insert silently adds rows in the wrong place.
    """
    insert, _ = _call(monkeypatch, "insert_rows")
    delete, _ = _call(monkeypatch, "delete_rows")
    base = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token"
    assert insert.requests[0].http_method == HttpMethod.POST
    assert insert.requests[0].uri == f"{base}/insert_dimension_range"
    assert delete.requests[0].http_method == HttpMethod.DELETE
    # The whole URI, not a suffix: "/dimension_range" is also a suffix of
    # "/insert_dimension_range", so a suffix check would pass on the wrong endpoint.
    assert delete.requests[0].uri == f"{base}/dimension_range"


def test_appending_rows_needs_no_index_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST dimension_range`` takes ``length``, not a range — it only ever appends."""
    cap, out = _call(monkeypatch, "append_rows")
    assert out.get("ok") is not False, out
    dimension = cap.requests[0].body["dimension"]
    assert dimension["length"] == 10
    assert "startIndex" not in dimension and "endIndex" not in dimension


def test_both_index_conventions_are_written_down() -> None:
    """The opposite conventions must be stated on *both* rules, not just one.

    A caller reads the rule for the endpoint they are using. Documenting the asymmetry
    only on the insert rule leaves someone who starts from delete with no warning, and
    the failure — one row too many, gone, with a success response — is not one they will
    notice in the result.
    """
    insert = _rule("POST", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/insert_dimension_range")
    delete = _rule("DELETE", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range")
    insert_text = " ".join(insert.pitfalls)
    delete_text = " ".join(delete.pitfalls)
    assert "0-based" in insert_text, "the insert rule must state its own index convention"
    assert "1-based" in delete_text, "the delete rule must state its own index convention"
    # Each also has to name the *other* one, since that is the mistake being prevented.
    assert "1-based" in insert_text, "the insert rule must warn that delete is 1-based"
    assert "0-based" in delete_text, "the delete rule must warn that insert is 0-based"


def test_deleting_rows_warns_that_it_is_irreversible() -> None:
    """Deleted rows do not go to the recycle bin, and the rule has to say so.

    Deleting a *file* is recoverable (it lands in the trash); deleting rows out of a
    worksheet is not, and the two live one skill apart.
    """
    delete = _rule("DELETE", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range")
    assert any("回收站" in note or "撤销" in note for note in delete.pitfalls), delete.pitfalls


# --------------------------------------------------------------- validation that bites


def test_empty_requests_array_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``sheets_batch_update`` with an empty ``requests`` answers success and does nothing.

    Same class as the task domain's empty ``update_fields``: a caller who built the array
    conditionally and ended up with none would be told the operation succeeded.
    """
    cap, out = _call(monkeypatch, "add_sheet", body={"requests": []})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == [], "a spec violation must not reach _invoke"


def test_unknown_merge_type_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``mergeType`` is a closed set; a plausible-looking wrong value is caught locally."""
    cap, out = _call(monkeypatch, "merge", body={"range": f"{SHEET_ID}!F11:G12", "mergeType": "MERGE"})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


def test_create_refuses_an_over_long_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """255 characters is the documented cap on a new spreadsheet's title."""
    cap, out = _call(monkeypatch, "create", body={"title": "长" * 256})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


def test_find_requires_both_condition_and_needle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A find with no ``find`` string would match nothing and report success."""
    cap, out = _call(monkeypatch, "find", body={"find_condition": {"range": f"{SHEET_ID}!A1:C5"}})
    assert out.get("ok") is False, out
    assert "find" in json.dumps(out, ensure_ascii=False)
    assert cap.requests == []


def test_replace_requires_a_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing without ``replacement`` is not "delete the matches" — it is a bad request."""
    cap, out = _call(monkeypatch, "replace", body={"find_condition": {"range": f"{SHEET_ID}!A1:C5"}, "find": "待跟进"})
    assert out.get("ok") is False, out
    assert cap.requests == []


# ----------------------------------------------------------- the writes stay refused

#: Endpoint → the tool that must be named instead, for every ``hard: true`` rule.
KEPT_TOOLS = [
    ("GET", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range", "feishu_sheet_read"),
    ("PUT", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values", "feishu_sheet_write"),
    ("POST", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append", "feishu_sheet_append"),
    ("PUT", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style", "feishu_sheet_format"),
]


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_cell_io_endpoint_names_its_tool_without_blocking(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, tool: str
) -> None:
    """These four point at a tool but stay *advice* — deliberately not ``hard``.

    The generic tool has warned rather than refused on ``/open-apis/sheets/`` since before
    this skill existed (``_PREFER_DEDICATED``), and for a reason that still holds: the
    guard is a prefix over the whole domain, so a hard refusal here would strand
    hand-built calls that are perfectly valid. The rule's job is to name the tool and say
    why; the range check itself lives in the tool.
    """
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps({"spreadsheet_token": SPREADSHEET, "range": f"{SHEET_ID}!A1:B2"}),
        body_json="{}",
        query_json="{}",
    )
    assert out.get("code") != "use_dedicated_tool", out
    assert cap.requests, f"{method} {uri} must remain reachable"
    assert _rule(method, uri).prefer_tool == tool


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_rule_says_why(method: str, uri: str, tool: str) -> None:
    """Advice without a reason reads as arbitrary, and gets ignored."""
    rule = _rule(method, uri)
    assert rule.prefer_tool == tool
    assert rule.prefer_hard is False, "see test_cell_io_endpoint_names_its_tool_without_blocking"
    assert rule.why.strip(), f"{method} {uri}: named a tool without saying why"


def test_kept_tools_still_exist() -> None:
    """A hard rule pointing at a deleted tool would be a dead end with no way forward."""
    source = (TOOLS_DIR / "feishu_sheet.py").read_text(encoding="utf-8")
    for _, _, tool in KEPT_TOOLS:
        assert f"async def {tool}(" in source, f"{tool} is named by a hard rule but no longer exists"


def test_writing_values_is_a_put_not_a_post() -> None:
    """The method is the whole difference between writing and appending.

    ``PUT .../values`` writes a range; ``POST .../values_append`` appends after the last
    row. Both spellings exist, so getting the method wrong is not a 404 that teaches you
    anything — the table is where this is written down.
    """
    write = _rule("PUT", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values")
    append = _rule("POST", "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append")
    assert write.prefer_tool == "feishu_sheet_write"
    assert append.prefer_tool == "feishu_sheet_append"


# --------------------------------------------------- refusals stay in their own lane


def test_structure_endpoints_are_not_swallowed_by_the_hard_write_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PUT .../values`` is hard-refused; the tabled structure endpoints must stay reachable.

    They live under the same ``/spreadsheets/:token`` prefix, so a rule matched on path
    alone would make merging cells or inserting rows impossible — refused with "use
    feishu_sheet_write", which names a tool that cannot do either.
    """
    for label in ("merge", "insert_rows", "delete_rows", "add_sheet", "find"):
        cap, out = _call(monkeypatch, label)
        assert out.get("ok") is not False, f"{label} was refused: {out}"
        assert cap.requests, f"{label} built no request"


def test_renaming_a_spreadsheet_is_declared_here_not_in_drive() -> None:
    """Exactly one skill may own an endpoint, or load order decides which rules apply.

    Renaming is discussed in ``feishu-drive`` (it is where someone looks for "rename a
    document"), but the *rule* lives here. Two rules for one endpoint at equal
    specificity would be resolved by filename order, which is not a decision anyone
    made on purpose.
    """
    all_rules = _spec.load_rules(SKILLS_DIR)
    owners = [
        r.source
        for r in all_rules
        if r.method == "PATCH" and r.uri == "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token"
    ]
    assert owners == ["feishu-sheet"], f"the spreadsheet PATCH rule is declared by {owners}"
