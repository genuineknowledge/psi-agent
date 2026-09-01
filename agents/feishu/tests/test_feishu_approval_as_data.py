"""Parity: the ``feishu-approval`` skill reaches Feishu the same way the tools did.

Fifth domain through the migration, after ``contact``, ``chat``, ``message`` and
``bitable``. Same standard of proof: build the request through the generic
``feishu_api`` path driven by ``skills/feishu-approval/SKILL.md``, and compare it
against what the deleted tool sent.

Approval was picked ahead of the domains with a larger schema share because it is the
one whose impls are genuinely thin: six of the eight are ``_invoke(_build_x(...))``
plus a response reshape, and a reshape is not knowledge a table has to carry — the
model reads the raw fields either way. The two that stay are the two that *transform*
input or output rather than forward it, and both transformations are ones a model gets
silently wrong: ``create`` has to serialize ``form`` as a JSON string containing a
JSON array (handing Feishu the bare array is rejected), and ``get_definition`` parses
that same stringified form into a widget list so field ids can be copied rather than
invented.

Two properties of this domain are worth pinning beyond wire parity:

* **Approve and reject are separate endpoints**, not one endpoint with a flag. The
  deleted ``feishu_approval_decide(approve=...)`` chose the URI from a bool, so the
  table has to keep both rows reachable and distinguishable.
* **The paged endpoints do not use ``items``.** Tasks come back under ``tasks`` and
  instance listing under ``instance_code_list``. A wrong ``items`` key would page
  correctly and hoist nothing, which looks like an empty result rather than an error.

No ``confirm`` gate is declared on approve/reject, and that is deliberate rather than
an omission: the deleted tool had none, and ``feishu-leave-audit-board`` /
``feishu-reimbursement-audit-report`` auto-approve 小事 with no user round trip. Adding
a gate here would not preserve behaviour, it would break those skills. The
irreversibility is carried as a pitfall instead, which is what the tool did.
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
APPROVAL_SKILL = SKILLS_DIR / "feishu-approval" / "SKILL.md"

#: Every tabled approval endpoint declares both TENANT and USER as candidates. The two
#: subscribe builders narrowed to TENANT; see the token test for why that is not copied.
WAS_TOKENS = {"TENANT", "USER"}

# The wire contract of the 6 tools this skill replaced, captured mechanically by running
# each builder at migration time and frozen here. The builders are gone — keeping them
# alive only to be a test reference would be keeping dead production code, which is what
# this migration exists to remove. These literals are what Feishu received before.
WAS: dict[str, dict[str, Any]] = {
    "task_query": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/approval/v4/tasks/query",
        "paths": {},
        "queries": [("user_id", "ou_a"), ("topic", "1"), ("user_id_type", "open_id"), ("page_size", "100")],
        "body": None,
    },
    "task_query_paged": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/approval/v4/tasks/query",
        "paths": {},
        "queries": [
            ("user_id", "ou_a"),
            ("topic", "2"),
            ("user_id_type", "user_id"),
            ("page_size", "50"),
            ("page_token", "tok1"),
        ],
        "body": None,
    },
    "list_instances": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/approval/v4/instances",
        "paths": {},
        "queries": [
            ("approval_code", "APPR1"),
            ("start_time", "1700000000000"),
            ("end_time", "1700086400000"),
            ("page_size", "100"),
        ],
        "body": None,
    },
    "approve": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/approval/v4/tasks/approve",
        "paths": {},
        "queries": [("user_id_type", "open_id")],
        "body": {
            "approval_code": "APPR1",
            "instance_code": "INST1",
            "user_id": "ou_b",
            "task_id": "TASK1",
            "comment": "ok",
        },
    },
    "reject": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/approval/v4/tasks/reject",
        "paths": {},
        "queries": [("user_id_type", "open_id")],
        "body": {"approval_code": "APPR1", "instance_code": "INST1", "user_id": "ou_b", "task_id": "TASK1"},
    },
    "subscribe": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/approval/v4/approvals/:approval_code/subscribe",
        "paths": {"approval_code": "APPR1"},
        "queries": [],
        "body": None,
    },
    "unsubscribe": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/approval/v4/approvals/:approval_code/unsubscribe",
        "paths": {"approval_code": "APPR1"},
        "queries": [],
        "body": None,
    },
}

#: The two endpoints that keep a dedicated tool, and the shape the *tool* sent. Kept here
#: so the refusal tests can prove the generic path never builds these requests at all.
KEPT: dict[str, dict[str, Any]] = {
    "definition": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/approval/v4/approvals/:approval_code",
        "paths": {"approval_code": "APPR1"},
        # ``with_admin_id`` is added as a Python bool, which the SDK stringifies as
        # "True" rather than "true". Frozen as observed, not as it "should" look.
        "queries": [("user_id_type", "user_id"), ("with_admin_id", "True")],
        "body": None,
    },
    "instance_get": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/approval/v4/instances/:instance_id",
        "paths": {"instance_id": "INST1"},
        "queries": [("user_id_type", "open_id")],
        "body": None,
    },
    "create_instance": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/approval/v4/instances",
        "paths": {},
        "queries": [("user_id_type", "open_id")],
        "body": {
            "approval_code": "APPR1",
            "form": '[{"id":"w1","type":"input","value":"x"}]',
            "open_id": "ou_a",
            "title": "T",
        },
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    "task_query": {
        "method": "GET",
        "uri": "/open-apis/approval/v4/tasks/query",
        "query": {"user_id": "ou_a"},
    },
    "task_query_paged": {
        "method": "GET",
        "uri": "/open-apis/approval/v4/tasks/query",
        "query": {"user_id": "ou_a", "topic": "2", "user_id_type": "user_id", "page_size": 50, "page_token": "tok1"},
    },
    "list_instances": {
        "method": "GET",
        "uri": "/open-apis/approval/v4/instances",
        "query": {
            "approval_code": "APPR1",
            "start_time": "1700000000000",
            "end_time": "1700086400000",
        },
    },
    "approve": {
        "method": "POST",
        "uri": "/open-apis/approval/v4/tasks/approve",
        "query": {"user_id_type": "open_id"},
        "body": {
            "approval_code": "APPR1",
            "instance_code": "INST1",
            "user_id": "ou_b",
            "task_id": "TASK1",
            "comment": "ok",
        },
    },
    "reject": {
        "method": "POST",
        "uri": "/open-apis/approval/v4/tasks/reject",
        "query": {"user_id_type": "open_id"},
        "body": {"approval_code": "APPR1", "instance_code": "INST1", "user_id": "ou_b", "task_id": "TASK1"},
    },
    "subscribe": {
        "method": "POST",
        "uri": "/open-apis/approval/v4/approvals/:approval_code/subscribe",
        "paths": {"approval_code": "APPR1"},
    },
    "unsubscribe": {
        "method": "POST",
        "uri": "/open-apis/approval/v4/approvals/:approval_code/unsubscribe",
        "paths": {"approval_code": "APPR1"},
    },
}

#: Endpoints whose rule declares ``paginate``, so one call drains pages.
PAGED = {"task_query", "task_query_paged", "list_instances"}


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
    if label not in PAGED:
        return None
    key = "instance_code_list" if label == "list_instances" else "tasks"
    return [{"ok": True, "data": {key: [], "has_more": False}}]


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
    return _spec.parse_rules(APPROVAL_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


def test_skill_declares_every_migrated_endpoint() -> None:
    """The 5 tabled endpoints, plus the 3 that exist only to point back at a tool."""
    got = {(r.method, r.uri) for r in _rules()}
    tabled = {(WAS[k]["method"].name, WAS[k]["uri"]) for k in WAS}
    assert tabled <= got, f"tabled endpoint missing a rule: {tabled - got}"
    assert got - tabled == {
        ("GET", "/open-apis/approval/v4/approvals/:approval_code"),
        ("GET", "/open-apis/approval/v4/instances/:instance_id"),
        ("POST", "/open-apis/approval/v4/instances"),
    }


def test_every_rule_has_a_documented_row() -> None:
    """A rule the Markdown never mentions is a rule the model will never pick."""
    text = APPROVAL_SKILL.read_text(encoding="utf-8")
    table = text.split("```rules")[0]
    for rule in _rules():
        assert rule.uri in table, f"{rule.endpoint} is enforced but undocumented"


# ------------------------------------------------------- wire parity vs the builders


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_matches_the_deleted_builder(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Byte-for-byte the same request the tool sent: method, uri, paths, query, body."""
    cap, out = _call(monkeypatch, label)
    assert out.get("ok") is not False, out
    assert cap.requests, f"{label} sent no request at all"
    assert _sent(cap.requests[0]) == _want(label)


@pytest.mark.parametrize("label", sorted(WAS))
def test_token_candidates_are_declared_for_every_endpoint(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """The generic path declares both candidates and lets ``prefer`` choose the send.

    The two subscribe builders narrowed to TENANT, and that difference is deliberately
    *not* reproduced. ``verify()`` in the SDK picks the type whose token is actually on
    the RequestOption, so narrowing does not express "prefer the bot" — it makes the
    request unsendable as the other identity, which is what broke the legitimate tenant
    sends before. Subscribing is a tenant-scoped operation either way: with no UAT there
    is nothing to send as a user, and with one Feishu answers with a permission error
    naming the missing scope, which beats a local SDK exception.
    """
    cap, _ = _call(monkeypatch, label)
    got = {str(t).split(".")[-1] for t in (cap.requests[0].token_types or set())}
    assert got == WAS_TOKENS


# ------------------------------------------------------------------ approve vs reject


def test_approve_and_reject_are_separate_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deleted tool picked the URI from a bool; both URIs must stay reachable.

    If one row shadowed the other, ``decide(approve=False)``'s replacement would post a
    rejection to the approve endpoint — a wrong decision recorded on a real approval,
    which is about the worst silent failure this domain has.
    """
    approved, _ = _call(monkeypatch, "approve")
    rejected, _ = _call(monkeypatch, "reject")
    assert approved.requests[0].uri == WAS["approve"]["uri"]
    assert rejected.requests[0].uri == WAS["reject"]["uri"]
    assert approved.requests[0].uri != rejected.requests[0].uri


@pytest.mark.parametrize("label", ["approve", "reject"])
@pytest.mark.parametrize("missing", ["approval_code", "instance_code", "user_id", "task_id"])
def test_deciding_without_the_full_identity_is_refused(
    monkeypatch: pytest.MonkeyPatch, label: str, missing: str
) -> None:
    """All four ids are required — Feishu needs the whole tuple to place the decision."""
    body = {k: v for k, v in CALLS[label]["body"].items() if k != missing}
    cap, out = _call(monkeypatch, label, body=body)
    assert out["ok"] is False
    assert out.get("code") == "spec_violation", out
    assert missing in json.dumps(out, ensure_ascii=False)
    assert cap.requests == [], "a spec violation must not reach the network"


def test_no_confirm_gate_on_deciding() -> None:
    """Deliberate: the deleted tool had none, and 小事 auto-approval depends on that.

    ``feishu-leave-audit-board`` and ``feishu-reimbursement-audit-report`` approve
    小事 with no user round trip. A ``confirm`` token here would not preserve the old
    behaviour — it would break both skills. The irreversibility is carried as a
    pitfall, which is exactly what the tool's docstring did.
    """
    for uri in ("/open-apis/approval/v4/tasks/approve", "/open-apis/approval/v4/tasks/reject"):
        rule = _rule("POST", uri)
        assert rule.confirm == "", f"{uri} must not gate: auto-approval of 小事 depends on it"
        assert any("不可撤销" in p for p in rule.pitfalls), f"{uri} should still warn it cannot be undone"


# ------------------------------------------------------------------------- the kept two


@pytest.mark.parametrize(
    ("method", "uri", "tool"),
    [
        ("GET", "/open-apis/approval/v4/approvals/:approval_code", "feishu_approval_get_definition"),
        ("GET", "/open-apis/approval/v4/instances/:instance_id", "feishu_approval_get"),
        ("POST", "/open-apis/approval/v4/instances", "feishu_approval_create"),
    ],
)
def test_kept_tool_endpoint_refuses_and_names_the_tool(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, tool: str
) -> None:
    """A hard rule refuses *before* the network and says which tool to use instead."""
    paths: dict[str, str] = {}
    if ":approval_code" in uri:
        paths = {"approval_code": "APPR1"}
    elif ":instance_id" in uri:
        paths = {"instance_id": "INST1"}
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps(paths),
        body_json="{}",
        query_json="{}",
    )
    assert out["ok"] is False
    assert out.get("code") == "use_dedicated_tool", out
    assert out.get("tool") == tool, out
    assert cap.requests == [], "a hard refusal must not reach the network"


@pytest.mark.parametrize(
    ("method", "uri"),
    [
        ("GET", "/open-apis/approval/v4/approvals/:approval_code"),
        ("GET", "/open-apis/approval/v4/instances/:instance_id"),
        ("POST", "/open-apis/approval/v4/instances"),
    ],
)
def test_kept_tool_rule_says_why(method: str, uri: str) -> None:
    """A refusal without a reason teaches the model nothing for next time."""
    rule = _rule(method, uri)
    assert rule.prefer_hard is True
    assert rule.why, f"{method} {uri} refuses without explaining what hand-building gets wrong"


def test_kept_tools_still_exist() -> None:
    """The three tools the hard rules point at must not have been deleted with the rest."""
    source = (TOOLS_DIR / "feishu_approval.py").read_text(encoding="utf-8")
    for tool in ("feishu_approval_get", "feishu_approval_get_definition", "feishu_approval_create"):
        assert f"async def {tool}(" in source, f"{tool} is named by a hard rule but no longer exists"


def test_reading_an_instance_keeps_its_derived_attachments() -> None:
    """``feishu_approval_get`` survives because of what it *derives*, not what it sends.

    The instance's ``form`` is a stringified array; the tool turns the file widgets in it
    into ``attachments`` entries tagged ``kind: url`` (a direct link that expires in about
    12 hours) or ``kind: drive`` (a media token). Those two kinds need different
    ``feishu_file_download`` arguments, and ``feishu-reimbursement-archive`` /
    ``feishu-reimbursement-audit-report`` consume the derived list directly. Tabling this
    endpoint would have handed the model a JSON string to parse and a distinction to
    rediscover, on the exact path where a missed expiry means the receipts are gone.
    """
    impl_src = (TOOLS_DIR / "_feishu" / "approval.py").read_text(encoding="utf-8")
    assert "def _parse_approval_attachments(" in impl_src
    assert "attachments" in impl_src


# PLACEHOLDER_OVERLAP


def test_subscribe_is_not_swallowed_by_the_definition_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """``subscribe`` lives under the hard-refused definition URI and must stay reachable.

    ``GET /approvals/:approval_code`` is hard-refused, and
    ``POST /approvals/:approval_code/subscribe`` is one segment below it. Method differs
    too, but specificity is what has to carry this: if the parent's authority leaked
    down, subscribing would be answered with "use feishu_approval_get_definition".
    """
    cap, out = _call(monkeypatch, "subscribe")
    assert out.get("ok") is not False, out
    assert cap.requests, "subscribe was refused by its parent rule"
    assert cap.requests[0].uri == WAS["subscribe"]["uri"]


def test_listing_instances_is_not_swallowed_by_the_create_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST /instances`` is hard-refused; ``GET /instances`` is a different endpoint.

    Same URI, different method. A rule that matched on path alone would make listing
    instances impossible — and that read is what every audit skill starts from.
    """
    cap, out = _call(monkeypatch, "list_instances")
    assert out.get("ok") is not False, out
    assert cap.requests, "listing instances was refused by the create rule"
    assert cap.requests[0].http_method == HttpMethod.GET


# --------------------------------------------------------------------------- paging


def test_task_paging_uses_the_tasks_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks come back under ``tasks``, not ``items`` — a wrong key hoists nothing."""
    pages = [
        {"ok": True, "data": {"tasks": [{"task_id": "T1"}], "has_more": True, "page_token": "p2"}},
        {"ok": True, "data": {"tasks": [{"task_id": "T2"}], "has_more": False}},
    ]
    cap, out = _generic(
        monkeypatch,
        pages=pages,
        method="GET",
        uri="/open-apis/approval/v4/tasks/query",
        query_json=json.dumps({"user_id": "ou_a"}),
        body_json="{}",
        paths_json="{}",
    )
    assert len(cap.requests) == 2, "the second page was never fetched"
    assert [t["task_id"] for t in out["tasks"]] == ["T1", "T2"]
    assert out["count"] == 2


def test_instance_listing_uses_the_instance_code_list_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance listing returns bare codes under ``instance_code_list``."""
    pages = [
        {"ok": True, "data": {"instance_code_list": ["c1"], "has_more": True, "page_token": "p2"}},
        {"ok": True, "data": {"instance_code_list": ["c2"], "has_more": False}},
    ]
    cap, out = _generic(
        monkeypatch,
        pages=pages,
        method="GET",
        uri="/open-apis/approval/v4/instances",
        query_json=json.dumps({"approval_code": "APPR1", "start_time": "1", "end_time": "2"}),
        body_json="{}",
        paths_json="{}",
    )
    assert len(cap.requests) == 2
    assert out["instance_code_list"] == ["c1", "c2"]


def test_paging_carries_the_token_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second request must actually ask for the page the first one handed back."""
    pages = [
        {"ok": True, "data": {"tasks": [], "has_more": True, "page_token": "NEXT"}},
        {"ok": True, "data": {"tasks": [], "has_more": False}},
    ]
    cap, _ = _generic(
        monkeypatch,
        pages=pages,
        method="GET",
        uri="/open-apis/approval/v4/tasks/query",
        query_json=json.dumps({"user_id": "ou_a"}),
        body_json="{}",
        paths_json="{}",
    )
    second = dict(cap.requests[1].queries or [])
    assert second.get("page_token") == "NEXT"


# ------------------------------------------------------------------------ constraints


def test_task_query_needs_a_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``user_id`` Feishu answers with someone else's tasks or an opaque error."""
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/approval/v4/tasks/query",
        query_json="{}",
        body_json="{}",
        paths_json="{}",
    )
    assert out["ok"] is False
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


def test_defaults_come_from_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deleted tool hard-coded topic=1 / open_id / page_size=100; the table now does."""
    cap, _ = _call(monkeypatch, "task_query")
    sent = dict(cap.requests[0].queries or [])
    assert sent["topic"] == "1"
    assert sent["user_id_type"] == "open_id"
    assert str(sent["page_size"]) == "100"


def test_topic_is_restricted_to_the_real_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """``topic`` is an enum; a made-up value returns an empty list rather than an error."""
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/approval/v4/tasks/query",
        query_json=json.dumps({"user_id": "ou_a", "topic": "9"}),
        body_json="{}",
        paths_json="{}",
    )
    assert out["ok"] is False
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


def test_listing_instances_needs_an_approval_code(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/approval/v4/instances",
        query_json=json.dumps({"start_time": "1", "end_time": "2"}),
        body_json="{}",
        paths_json="{}",
    )
    assert out["ok"] is False
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


# ------------------------------------------------------- knowledge that must survive


#: Facts the deleted impls carried in code or docstrings. Losing one of these turns a
#: migration into a downgrade, so each is pinned to text the model will actually read.
PITFALL_FACTS: list[tuple[str, str, str]] = [
    ("GET", "/open-apis/approval/v4/tasks/query", "tasks"),
    ("GET", "/open-apis/approval/v4/tasks/query", "process_id"),
    ("GET", "/open-apis/approval/v4/instances", "instance_code_list"),
    ("GET", "/open-apis/approval/v4/instances", "毫秒"),
    ("POST", "/open-apis/approval/v4/approvals/:approval_code/subscribe", "幂等"),
]


@pytest.mark.parametrize(("method", "uri", "needle"), PITFALL_FACTS)
def test_pitfall_text_survives(method: str, uri: str, needle: str) -> None:
    rule = _rule(method, uri)
    blob = " ".join(rule.pitfalls)
    assert needle in blob, f"{method} {uri}: pitfall text lost the {needle!r} warning"


def test_attachment_expiry_stays_documented() -> None:
    """The 12-hour window survives in the skill's prose even though the endpoint is refused.

    The rule for reading an instance now points at ``feishu_approval_get`` instead of
    carrying pitfalls, but the expiry is what makes an archive run succeed or lose the
    receipts, so it has to stay somewhere the model reads before planning the download.
    """
    text = APPROVAL_SKILL.read_text(encoding="utf-8")
    assert "12 小时" in text
    assert "feishu_file_download" in text


def test_status_code_tables_survive_in_prose() -> None:
    """``_APPROVAL_TASK_STATUS`` / ``_APPROVAL_INSTANCE_STATUS`` mapped numbers to words.

    The generic path returns Feishu's raw numbers, so the mapping has to live somewhere
    the model can read or a status of ``2`` is ambiguous between 已办 and approved. It
    is not expressible as a rule — the vocabulary has checks, not value transforms —
    so it moved into the skill's Markdown as a table.
    """
    text = APPROVAL_SKILL.read_text(encoding="utf-8")
    for needle in ("待办", "已办", "approved", "rejected", "revoked", "terminated"):
        assert needle in text, f"status vocabulary lost {needle!r}"


def test_the_two_codes_are_explained() -> None:
    """``approval_code`` vs ``instance_code`` is the domain's most common mix-up."""
    text = APPROVAL_SKILL.read_text(encoding="utf-8").split("```rules")[0]
    assert "approval_code" in text and "instance_code" in text
    assert "审批定义" in text, "the skill must say what approval_code identifies"


def test_the_three_identities_are_explained() -> None:
    """Who an approval action is attributed to is body-carried, not caller-derived.

    Unlike every other domain, ``prefer``/``user_key`` is not what decides whose action
    this is: create records the applicant from ``open_id``/``user_id`` in the body, and
    decide records the approver from ``user_id``. A model that assumes the usual rule
    would submit applications under the wrong person.
    """
    text = APPROVAL_SKILL.read_text(encoding="utf-8").split("```rules")[0]
    assert "申请人" in text
    assert "审批人" in text
    assert "实际处理人" in text
