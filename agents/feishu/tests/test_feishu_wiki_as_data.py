"""The ``feishu-wiki`` skill's endpoint table builds the requests Feishu documents.

Knowledge-base *browsing* already worked (``feishu_wiki_list_spaces`` / ``_list_nodes`` /
``_create_doc``). What was missing is everything else about a wiki: searching inside one,
moving a page, pulling a Drive document into a space, and administering the space itself.
None of those was ever a tool, so the shapes below come from the official docs plus the
official SDK's request builders, and the tests prove the generic ``feishu_api`` path
produces exactly those.

Four things earn a test rather than a sentence:

**Search is user-token-only.** Feishu names 「搜索 Wiki」 and 「创建知识库」 as the two wiki
endpoints that reject a tenant token. The bot is also usually not a member of any space, so
a tenant-token search would come back *successful and empty* — the failure mode that reads
as "nothing matched" when it means "asked as the wrong identity". The rule pins ``token:
user`` and a test asserts the send path narrows to USER.

**``task_type=move`` is required and single-valued.** ``move_docs_to_wiki`` is async: it
returns a ``task_id`` and the move has not happened yet. Polling without ``task_type``
answers 131002, which reads like a bad ``task_id``. The rule requires it, pins it to the
query bucket, and closes its value set to the one value that exists.

**Adding a member depends on the space's own shape.** A ``public`` space cannot take members
(131101 — it is already visible to everyone, so only *admins* can be added), and a
``person`` space cannot take admins. Neither is knowable from the request, so both are
pitfalls and the rule for reading space detail says to check first.

**Removing a member needs ``member_type``/``member_role`` in the body** even though
``member_id`` is already in the URI. That is unusual enough that a caller will send only the
path parameter and get 131002; ``required`` catches it locally instead.

Two endpoints deliberately do **not** live here, and a test asserts each stays put:
``get_node`` (in ``feishu-api``, beside the prose about its empty-result retry) and
``update_title`` (in ``feishu-drive``, beside the other two thirds of "Feishu has no unified
rename API"). An endpoint declared in two skills is resolved by filename order, which is
nobody's decision.
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
WIKI_SKILL = SKILLS_DIR / "feishu-wiki" / "SKILL.md"

SPACE = "7008061636015026domain"
NODE = "wikcnKFuLNSeStbdlt1BfpA5Yc"
MEMBER = "ou_9c3b2f1a8d7e6c5b4a39281706f5e4d3"
TASK = "7369477418500063234"

#: Endpoints whose rule declares ``paginate``, so one call drains pages.
PAGED = {"search", "list_members"}

#: The wire shape each tabled endpoint must produce.
WAS: dict[str, dict[str, Any]] = {
    "search": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/wiki/v1/nodes/search",
        "paths": {},
        "queries": [("page_size", "20")],
        "body": {"query": "报销标准", "space_id": SPACE},
    },
    "move": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/move",
        "paths": {"space_id": SPACE, "node_token": NODE},
        "queries": [],
        "body": {"target_parent_token": "wikcnBdlt1BfpA5YcKFuLNSeSt", "target_space_id": "7008061636015512345"},
    },
    "copy": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/copy",
        "paths": {"space_id": SPACE, "node_token": NODE},
        "queries": [],
        "body": {"target_parent_token": "wikcnBdlt1BfpA5YcKFuLNSeSt", "title": "报销制度(2026 版)"},
    },
    "move_docs_to_wiki": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/nodes/move_docs_to_wiki",
        "paths": {"space_id": SPACE},
        "queries": [],
        "body": {
            "parent_wiki_token": NODE,
            "obj_type": "docx",
            "obj_token": "doxcnftGvBrLm8ah7fFRSGdyAce",
            "apply": True,
        },
    },
    "task": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/wiki/v2/tasks/:task_id",
        "paths": {"task_id": TASK},
        "queries": [("task_type", "move")],
        "body": None,
    },
    "get_space": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/wiki/v2/spaces/:space_id",
        "paths": {"space_id": SPACE},
        "queries": [],
        "body": None,
    },
    "setting": {
        "method": HttpMethod.PUT,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/setting",
        "paths": {"space_id": SPACE},
        "queries": [],
        "body": {"create_setting": "admin", "security_setting": "not_allow", "comment_setting": "allow"},
    },
    "list_members": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/members",
        "paths": {"space_id": SPACE},
        "queries": [("page_size", "50")],
        "body": None,
    },
    "add_member": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/members",
        "paths": {"space_id": SPACE},
        "queries": [("need_notification", "true")],
        "body": {"member_type": "openid", "member_id": MEMBER, "member_role": "member"},
    },
    "remove_member": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/members/:member_id",
        "paths": {"space_id": SPACE, "member_id": MEMBER},
        "queries": [],
        "body": {"member_type": "openid", "member_role": "member"},
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``. The
#: caller never passes ``page_size`` — the rule's ``paginate`` supplies it.
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

#: Search only accepts a user token, so it is sent the way a caller has to send it.
CALLS["search"] = {**CALLS["search"], "user_key": "ou_sender", "prefer": "user"}
CALLS["setting"] = {**CALLS["setting"], "user_key": "ou_sender", "prefer": "user"}
CALLS["add_member"] = {**CALLS["add_member"], "user_key": "ou_sender", "prefer": "user"}
CALLS["remove_member"] = {**CALLS["remove_member"], "user_key": "ou_sender", "prefer": "user"}


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


#: The key each paged endpoint's rule declares for its rows. The member list returns
#: ``members``, not ``items`` — a wrong key drains nothing and reads as an empty space.
PAGE_ITEMS_KEY = {"list_members": "members"}


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
        user_key=spec.get("user_key", ""),
        prefer=spec.get("prefer", "tenant"),
    )


def _rules() -> list[Any]:
    return _spec.parse_rules(WIKI_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


def test_skill_declares_exactly_the_tabled_endpoints() -> None:
    """No rule without a table row, no table row without a rule."""
    got = {(r.method, r.uri) for r in _rules()}
    tabled = {(WAS[k]["method"].name, WAS[k]["uri"]) for k in WAS}
    assert got == tabled, f"rules and table disagree: only-rule={got - tabled}, only-table={tabled - got}"


@pytest.mark.parametrize("label", sorted(WAS))
def test_every_endpoint_appears_in_both_the_table_and_the_rules(label: str) -> None:
    """Drift between the prose a model reads and the rules that execute becomes a diff."""
    text = WIKI_SKILL.read_text(encoding="utf-8")
    assert text.count(WAS[label]["uri"]) >= 2, f"{WAS[label]['uri']} needs a table row and a rule"


@pytest.mark.parametrize("label", sorted(WAS))
def test_exactly_one_skill_declares_each_endpoint(label: str) -> None:
    """Two rules at equal specificity are resolved by filename order — nobody's decision."""
    everything = _spec.load_rules(SKILLS_DIR)
    method, uri = WAS[label]["method"].name, WAS[label]["uri"]
    owners = [r.source for r in everything if r.method == method and r.uri == uri]
    assert owners == ["feishu-wiki"], f"{method} {uri} is declared by {owners}"


@pytest.mark.parametrize(
    ("method", "uri", "home"),
    [
        # Beside the prose explaining why an empty result means "ask again as the user".
        ("GET", "/open-apis/wiki/v2/spaces/get_node", "feishu-api"),
        # Beside the other two thirds of "Feishu has no unified rename API".
        ("POST", "/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/update_title", "feishu-drive"),
    ],
)
def test_the_two_wiki_endpoints_that_live_elsewhere_stay_there(method: str, uri: str, home: str) -> None:
    """This skill must not re-declare them.

    Both are wiki endpoints, so copying them here would look like tidying. It would instead
    create a second rule for one endpoint and hand the tie to filename order.
    """
    everything = _spec.load_rules(SKILLS_DIR)
    owners = [r.source for r in everything if r.method == method and r.uri == uri]
    assert owners == [home], f"{method} {uri} should be declared only by {home}, got {owners}"


def test_the_skill_points_at_the_endpoints_it_does_not_own() -> None:
    """A reader of this file still has to be able to find rename and get_node."""
    text = WIKI_SKILL.read_text(encoding="utf-8")
    assert "update_title" in text and "feishu-drive" in text
    assert "get_node" in text and "feishu-api" in text


# ------------------------------------------------------------------- wire-shape parity


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_builds_the_documented_request(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    cap, out = _call(monkeypatch, label)
    assert out["ok"] is True, f"{label} was refused: {out}"
    assert _sent(cap.request) == _want(label)


@pytest.mark.parametrize("label", sorted(PAGED))
def test_paged_endpoints_declare_their_items_key(label: str) -> None:
    rule = _rule(WAS[label]["method"].name, WAS[label]["uri"])
    assert rule.paginate, f"{label} should declare paginate"
    assert rule.paginate["items"] == PAGE_ITEMS_KEY.get(label, "items")


# ---------------------------------------------------------------- search is user-only


def test_search_is_routed_to_the_user_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu rejects a tenant token here; a tenant call would look empty, not refused.

    The generic path declares both token types as *candidates* on every request — narrowing
    to USER on the request itself would make it unsendable as tenant, which ``_invoke_write``
    needs for the "the bot should own this" case. What picks the identity is the ``prefer``
    argument handed to ``_invoke``, so that is what this asserts.
    """
    cap, out = _call(monkeypatch, "search")
    assert out["ok"] is True
    assert cap.kwargs[0]["prefer"] == "user"
    assert cap.kwargs[0]["user_key"] == "ou_sender"


def test_search_rule_says_user_token() -> None:
    """The rule records it too, so a caller who omits ``prefer`` still learns the strategy."""
    assert _rule("POST", "/open-apis/wiki/v1/nodes/search").token == "user"


def test_the_read_endpoints_stay_tenant_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads try the bot first and fall back, which is what ``tenant_then_user`` means.

    Only the four that Feishu restricts to a user identity — search, settings, and the two
    member writes — are ``token: user``. Marking a read user-only would make it fail for a
    caller with no authorization at all.
    """
    assert _rule("GET", "/open-apis/wiki/v2/spaces/:space_id").token == "tenant_then_user"
    assert _rule("GET", "/open-apis/wiki/v2/spaces/:space_id/members").token == "tenant_then_user"
    user_only = {r.uri for r in _rules() if r.token == "user"}
    assert user_only == {
        "/open-apis/wiki/v1/nodes/search",
        "/open-apis/wiki/v2/spaces/:space_id/setting",
        "/open-apis/wiki/v2/spaces/:space_id/members",
        "/open-apis/wiki/v2/spaces/:space_id/members/:member_id",
    }


def test_search_requires_a_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "search", body={"space_id": SPACE})
    assert out["ok"] is False
    assert "query" in str(out)
    assert not cap.requests


def test_search_is_the_v1_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """``wiki/v2`` has no search endpoint; the working one is ``wiki/v1/nodes/search``.

    Pinned because "upgrade the version prefix" is a tempting, silent-looking edit — v2
    answers 404 for this path, and the SDK builder this shape came from is ``wiki.v1``.
    """
    cap, _ = _call(monkeypatch, "search")
    assert cap.request.uri == "/open-apis/wiki/v1/nodes/search"


# ------------------------------------------------------- the async move and its polling


def test_polling_a_move_task_requires_task_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without it Feishu answers 131002, which reads like a bad task_id."""
    cap, out = _call(monkeypatch, "task", query={})
    assert out["ok"] is False
    assert "task_type" in str(out)
    assert not cap.requests


@pytest.mark.parametrize("bad", ["copy", "MOVE", "move_docs", "delete"])
def test_task_type_has_exactly_one_valid_value(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    cap, out = _call(monkeypatch, "task", query={"task_type": bad})
    assert out["ok"] is False
    assert not cap.requests


def test_task_type_must_ride_in_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned to ``query``, so passing it in the body is named as misplaced, not missing.

    Being told a field is missing when it was supplied sends the caller looking for the
    wrong mistake.
    """
    cap, out = _call(monkeypatch, "task", query={}, body={"task_type": "move"})
    assert out["ok"] is False
    assert "query" in str(out)
    assert not cap.requests


def test_moving_docs_into_a_wiki_requires_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _call(monkeypatch, "move_docs_to_wiki", body={"parent_wiki_token": NODE})
    assert out["ok"] is False
    assert "obj_token" in str(out) or "obj_type" in str(out)
    assert not cap.requests


def test_move_takes_no_required_body_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both target fields are optional: an empty body moves the node to the space's top.

    Marking either one required would break that legitimate call.
    """
    cap, out = _call(monkeypatch, "move", body={})
    assert out["ok"] is True, f"an empty move body should be allowed: {out}"
    assert cap.requests


# ------------------------------------------------------------ space settings and members


@pytest.mark.parametrize(
    ("field", "good", "bad"),
    [
        ("create_setting", ["admin_and_member", "admin"], ["member", "all", "everyone"]),
        ("security_setting", ["allow", "not_allow"], ["deny", "false", "disallow"]),
        ("comment_setting", ["allow", "not_allow"], ["deny", "off", "no"]),
    ],
)
def test_space_setting_values_are_closed_sets(
    monkeypatch: pytest.MonkeyPatch, field: str, good: list[str], bad: list[str]
) -> None:
    """``not_allow`` is the spelling — not ``deny``, ``disallow`` or ``false``."""
    for value in good:
        cap, out = _call(monkeypatch, "setting", body={field: value})
        assert out["ok"] is True, f"{field}={value} should be accepted: {out}"
        assert cap.request.body[field] == value
    for value in bad:
        cap, out = _call(monkeypatch, "setting", body={field: value})
        assert out["ok"] is False, f"{field}={value} should be refused"
        assert not cap.requests


def test_partial_settings_are_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three are optional — sending only the one being changed must not be refused."""
    cap, out = _call(monkeypatch, "setting", body={"comment_setting": "not_allow"})
    assert out["ok"] is True, f"a single-field setting update should be allowed: {out}"
    assert cap.request.body == {"comment_setting": "not_allow"}


@pytest.mark.parametrize("member_type", ["openid", "userid", "unionid", "email", "openchat", "opendepartmentid"])
def test_every_documented_member_type_is_accepted(monkeypatch: pytest.MonkeyPatch, member_type: str) -> None:
    body = {"member_type": member_type, "member_id": MEMBER, "member_role": "member"}
    cap, out = _call(monkeypatch, "add_member", body=body)
    assert out["ok"] is True, f"{member_type} should be accepted: {out}"
    assert cap.request.body["member_type"] == member_type


@pytest.mark.parametrize("bad", ["open_id", "user_id", "union_id", "chat_id", "department_id", "mobile"])
def test_snake_case_member_types_are_refused(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """This endpoint spells them without underscores, unlike the rest of the platform.

    ``open_id`` is correct almost everywhere else in Feishu's API — here it is ``openid``.
    That inconsistency is exactly what a caller will get wrong.
    """
    body = {"member_type": bad, "member_id": MEMBER, "member_role": "member"}
    cap, out = _call(monkeypatch, "add_member", body=body)
    assert out["ok"] is False, f"{bad} should be refused"
    assert not cap.requests
    assert "openid" in str(out)


@pytest.mark.parametrize("bad", ["editor", "viewer", "owner", "Admin", "full_access"])
def test_member_role_is_admin_or_member(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """Not the drive permission vocabulary — a wiki space has only admin and member."""
    body = {"member_type": "openid", "member_id": MEMBER, "member_role": bad}
    cap, out = _call(monkeypatch, "add_member", body=body)
    assert out["ok"] is False
    assert not cap.requests


@pytest.mark.parametrize("missing", ["member_type", "member_id", "member_role"])
def test_adding_a_member_needs_all_three(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    body = {k: v for k, v in CALLS["add_member"]["body"].items() if k != missing}
    cap, out = _call(monkeypatch, "add_member", body=body)
    assert out["ok"] is False
    assert missing in str(out)
    assert not cap.requests


@pytest.mark.parametrize("missing", ["member_type", "member_role"])
def test_removing_a_member_still_needs_type_and_role_in_the_body(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """``member_id`` is in the URI, yet these two must be repeated in the body (131002)."""
    body = {k: v for k, v in CALLS["remove_member"]["body"].items() if k != missing}
    cap, out = _call(monkeypatch, "remove_member", body=body)
    assert out["ok"] is False
    assert missing in str(out)
    assert not cap.requests


# ------------------------------------------------------- facts that only prose can carry


@pytest.mark.parametrize(
    "fact",
    [
        # The domain's defining failure mode: success with an empty payload.
        "返回「成功 + 空内容」",
        # Search is one of the two wiki endpoints that reject a tenant token.
        "唯二不支持 tenant token",
        # Moving needs permission on three separate containers.
        "三处",
        # The async move's polling identity restriction.
        "只有发起任务的身份能查结果",
        # Pulling a doc into a wiki changes its URL — tell the user first.
        "旧链接失效",
        # Public and personal spaces accept members/admins in opposite ways.
        "131101",
        # Changing space settings requires being an admin of that space.
        "必须是这个知识空间的管理员",
        # Removing a member does not remove their documents.
        "移除成员不删他建的文档",
    ],
)
def test_pitfall_text_survives(fact: str) -> None:
    assert fact in WIKI_SKILL.read_text(encoding="utf-8"), f"missing from the skill: {fact}"


def test_the_three_token_kinds_are_explained() -> None:
    """``space_id`` / ``node_token`` / ``obj_token`` mixed up is most of this domain's errors."""
    text = WIKI_SKILL.read_text(encoding="utf-8")
    for token in ("space_id", "node_token", "obj_token"):
        assert token in text
    assert "读正文用它" in text


def test_the_browsing_tools_are_listed_as_tools() -> None:
    """The empty-result retry is why these stay in Python; the skill has to say so.

    ``rules`` can retry on *refusal*, never on "succeeded but returned nothing", so a table
    row cannot replace ``feishu_wiki_list_spaces``. Someone reading only the endpoint table
    would otherwise conclude the tools are redundant and delete them.
    """
    text = WIKI_SKILL.read_text(encoding="utf-8")
    for tool in (
        "feishu_wiki_list_spaces",
        "feishu_wiki_list_nodes",
        "feishu_wiki_create_doc",
        "feishu_wiki_create_space",
    ):
        assert tool in text, f"{tool} should be listed as a kept tool"
    assert "空结果不是被拒" in text


def test_the_kept_tools_still_exist() -> None:
    """The skill names them; if one were renamed, this fails instead of the prose going stale."""
    tools: Any = importlib.import_module("feishu_wiki")
    for tool in (
        "feishu_wiki_list_spaces",
        "feishu_wiki_list_nodes",
        "feishu_wiki_create_doc",
        "feishu_wiki_create_doc_with_content",
        "feishu_wiki_create_space",
    ):
        assert hasattr(tools, tool), f"{tool} is named by the skill but no longer exists"
