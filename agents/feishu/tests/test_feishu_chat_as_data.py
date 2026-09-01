"""Parity: the ``feishu-chat`` skill reaches Feishu the same way the tools did.

Second domain through the migration, after ``contact``. Same standard of proof: build
the request through the generic ``feishu_api`` path driven by
``skills/feishu-chat/SKILL.md``, build the same call through the hand-written
``_build_*`` helper the dedicated tool used, and compare what would actually be sent.

Chat is the domain where URI overlap bites: ``DELETE /chats/:chat_id`` (解散群, gated by
``confirm``) is a prefix of ``DELETE /chats/:chat_id/members`` (踢人, not gated). If
specificity ordering fails, either removing a member demands the dismissal dance, or —
far worse — dismissing a group stops asking for it. Both directions are tested.

The dismissal gate itself is tested for the property that actually protects the group:
the confirmation code is sent to the *user* and never appears in the tool result, so a
model cannot satisfy the gate from what it can read.
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
CHAT_SKILL = SKILLS_DIR / "feishu-chat" / "SKILL.md"


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


class _CapturedInvoke:
    """Stands in for ``_invoke`` and keeps the request instead of sending it."""

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[BaseRequest] = []
        self._pages = pages or [{"ok": True, "data": {}}]

    async def __call__(self, request: BaseRequest, **_: Any) -> dict[str, Any]:
        self.requests.append(request)
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


def _rules() -> list[Any]:
    return _spec.parse_rules(CHAT_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# The wire contract of the 12 tools this skill replaced, captured mechanically from
# their ``_build_*`` helpers at migration time and frozen here. The helpers are gone —
# keeping them alive only to be a test reference would be keeping dead production code,
# which is the thing this migration is supposed to remove. These literals are what
# Feishu received before the change, so they still pin the contract.
WAS: dict[str, dict[str, Any]] = {
    "chat_search": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/im/v1/chats/search",
        "paths": {},
        "queries": [("page_size", "50"), ("query", "主群")],
        "body": None,
    },
    "chat_list": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/im/v1/chats",
        "paths": {},
        "queries": [("page_size", "100"), ("sort_type", "ByCreateTimeAsc"), ("user_id_type", "open_id")],
        "body": None,
    },
    "create_chat": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/chats",
        "paths": {},
        "queries": [("set_bot_manager", "true"), ("user_id_type", "open_id")],
        "body": {"name": "项目群", "user_id_list": ["ou_a"], "owner_id": "ou_b"},
    },
    "members_add": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/chats/:chat_id/members",
        "paths": {"chat_id": "oc_1"},
        "queries": [("member_id_type", "open_id"), ("succeed_type", "1")],
        "body": {"id_list": ["ou_a"]},
    },
    "members_remove": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/im/v1/chats/:chat_id/members",
        "paths": {"chat_id": "oc_1"},
        "queries": [("member_id_type", "open_id")],
        "body": {"id_list": ["ou_a"]},
    },
    "update_chat": {
        "method": HttpMethod.PUT,
        "uri": "/open-apis/im/v1/chats/:chat_id",
        "paths": {"chat_id": "oc_1"},
        "queries": [("user_id_type", "open_id")],
        "body": {"name": "新群名"},
    },
    "transfer_owner": {
        "method": HttpMethod.PUT,
        "uri": "/open-apis/im/v1/chats/:chat_id",
        "paths": {"chat_id": "oc_1"},
        "queries": [("user_id_type", "open_id")],
        "body": {"owner_id": "ou_new"},
    },
    "moderation": {
        "method": HttpMethod.PUT,
        "uri": "/open-apis/im/v1/chats/:chat_id/moderation",
        "paths": {"chat_id": "oc_1"},
        "queries": [("user_id_type", "open_id")],
        "body": {"moderation_setting": "only_owner"},
    },
    "dismiss": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/im/v1/chats/:chat_id",
        "paths": {"chat_id": "oc_1"},
        "queries": [],
        "body": None,
    },
    "menu_get": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/im/v1/chats/:chat_id/menu_tree",
        "paths": {"chat_id": "oc_1"},
        "queries": [],
        "body": None,
    },
    "menu_delete": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/im/v1/chats/:chat_id/menu_tree",
        "paths": {"chat_id": "oc_1"},
        "queries": [],
        "body": {"chat_menu_top_level_ids": ["m1"]},
    },
    "tabs_list": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs",
        "paths": {"chat_id": "oc_1"},
        "queries": [],
        "body": None,
    },
    "tab_delete": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs",
        "paths": {"chat_id": "oc_1"},
        "queries": [],
        "body": {"tab_ids": ["t1"]},
    },
}


WAS_MENU_TOKENS = {"TENANT"}  # what the deleted menu builder declared, unlike its 10 siblings


def _was(name: str) -> dict[str, Any]:
    """The frozen shape, in the same form ``_shape`` returns (minus tokens)."""
    w = WAS[name]
    return {
        "method": w["method"],
        "uri": w["uri"],
        "paths": w["paths"],
        "queries": sorted(w["queries"]),
        "body": w["body"],
    }


def _sent(req: BaseRequest) -> dict[str, Any]:
    """What the generic path is about to send, comparable to ``_was``."""
    out = _shape(req)
    out.pop("tokens")
    return out


# ------------------------------------------------------------------ the skill parses


def test_skill_declares_every_migrated_endpoint() -> None:
    """The 16 tools this skill replaces cover 15 distinct endpoints."""
    got = {(r.method, r.uri) for r in _rules()}
    assert got == {
        ("GET", "/open-apis/im/v1/chats/search"),
        ("GET", "/open-apis/im/v1/chats"),
        ("POST", "/open-apis/im/v1/chats"),
        ("GET", "/open-apis/im/v1/chats/:chat_id/members"),
        ("POST", "/open-apis/im/v1/chats/:chat_id/members"),
        ("DELETE", "/open-apis/im/v1/chats/:chat_id/members"),
        ("PUT", "/open-apis/im/v1/chats/:chat_id"),
        ("PUT", "/open-apis/im/v1/chats/:chat_id/moderation"),
        ("DELETE", "/open-apis/im/v1/chats/:chat_id"),
        ("GET", "/open-apis/im/v1/chats/:chat_id/menu_tree"),
        ("POST", "/open-apis/im/v1/chats/:chat_id/menu_tree"),
        ("DELETE", "/open-apis/im/v1/chats/:chat_id/menu_tree"),
        ("GET", "/open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs"),
        ("POST", "/open-apis/im/v1/chats/:chat_id/chat_tabs"),
        ("DELETE", "/open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs"),
    }


# ------------------------------------------------------- wire parity vs the builders


def test_chat_search_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/chats/search",
        query_json=json.dumps({"query": "主群"}),
    )
    assert _sent(cap.request) == _was("chat_search")


def test_chat_members_list_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/chats/:chat_id/members",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        query_json=json.dumps({"member_id_type": "open_id"}),
    )
    reference = _impl._build_chat_members_request("oc_1", "open_id", 100, "")
    assert _shape(cap.request) == _shape(reference)


def test_create_chat_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"name": "项目群", "user_id_list": ["ou_a"], "owner_id": "ou_b"}
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/chats",
        body_json=json.dumps(body),
        query_json=json.dumps({"user_id_type": "open_id"}),
    )
    assert _sent(cap.request) == _was("create_chat")
    assert ("set_bot_manager", "true") in _sent(cap.request)["queries"]


def test_update_chat_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"name": "新群名"}
    cap, _ = _generic(
        monkeypatch,
        method="PUT",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps(body),
        query_json=json.dumps({"user_id_type": "open_id"}),
    )
    assert _sent(cap.request) == _was("update_chat")


def test_transfer_owner_is_the_same_endpoint_as_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """转让群主 was its own tool but is ``PUT /chats/:chat_id`` with ``owner_id``."""
    cap, _ = _generic(
        monkeypatch,
        method="PUT",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps({"owner_id": "ou_new"}),
        query_json=json.dumps({"user_id_type": "open_id"}),
    )
    assert _sent(cap.request) == _was("transfer_owner")


def test_moderation_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="PUT",
        uri="/open-apis/im/v1/chats/:chat_id/moderation",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps({"moderation_setting": "only_owner"}),
        query_json=json.dumps({"user_id_type": "open_id"}),
    )
    assert _sent(cap.request) == _was("moderation")


def test_menu_get_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything but the candidate token set is identical — see the test below."""
    cap, _ = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/im/v1/chats/:chat_id/menu_tree",
        paths_json=json.dumps({"chat_id": "oc_1"}),
    )
    assert _sent(cap.request) == _was("menu_get")


def test_menu_gains_the_user_fallback_the_old_tool_advertised() -> None:
    """The one behaviour change in this domain, made explicit.

    The deleted menu builder declared ``{TENANT}`` alone while its tool took a
    ``user_key`` — so that argument could never be used as a fallback. Ten of the
    eleven chat builders declare ``{TENANT, USER}``; menu was the odd one out. The
    generic path declares both, which makes ``user_key`` actually work. The *preferred*
    token is still tenant, so the first attempt is unchanged; only a tenant refusal
    now falls back instead of failing.
    """
    assert {"TENANT"} == WAS_MENU_TOKENS
    assert _rule("GET", "/open-apis/im/v1/chats/:chat_id/menu_tree").token == "tenant"


def test_tabs_list_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs",
        paths_json=json.dumps({"chat_id": "oc_1"}),
    )
    assert _sent(cap.request) == _was("tabs_list")


def test_tab_delete_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"tab_ids": ["t1"]}
    cap, _ = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps(body),
    )
    assert _sent(cap.request) == _was("tab_delete")


# ------------------------------------------------- the overlapping DELETE paths

DISMISS = "解散群"


def _codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture every DM the gate sends. AppData is already isolated by conftest."""
    sent: list[str] = []

    async def _send(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
        sent.append(text)
        return {"ok": True, "message_id": "om_1"}

    monkeypatch.setattr(_impl, "send_message_impl", _send)
    return sent


def _code_from(message: str) -> str:
    match = re.search(r"确认码: (\d{6})", message)
    assert match, f"no 6-digit code in the message: {message!r}"
    return match.group(1)


def test_dismiss_is_gated_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """解散群 must not reach Feishu until the *user* supplies the code."""
    sent = _codes(monkeypatch)
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        user_key="ou_boss",
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert out.get("need_confirmation") is True
    assert len(sent) == 1, "the user must be told, out of band, what is about to happen"
    assert "oc_1" in sent[0]


def test_the_confirm_code_is_never_returned_to_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: a code the model can read is a code the model can echo.

    The old gate handed back ``confirm_token: 解散群`` — a constant already written in
    the skill file the model reads to find the endpoint. It could satisfy the gate in
    the same turn without ever asking a human. Nothing in the result may carry the code.
    """
    sent = _codes(monkeypatch)
    _, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        user_key="ou_boss",
    )
    code = _code_from(sent[0])
    assert code not in json.dumps(out, ensure_ascii=False)


def test_echoing_the_skill_file_phrase_does_not_dismiss(monkeypatch: pytest.MonkeyPatch) -> None:
    """The literal 解散群 is documentation, not a key — it must no longer work."""
    _codes(monkeypatch)
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        user_key="ou_boss",
        confirm=DISMISS,
    )
    assert cap.requests == []
    assert out.get("need_confirmation") is True


def test_dismiss_proceeds_with_the_code_the_user_was_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _codes(monkeypatch)
    _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        user_key="ou_boss",
    )
    cap, _ = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        user_key="ou_boss",
        confirm=_code_from(sent[0]),
    )
    assert _sent(cap.request) == _was("dismiss")


def test_a_code_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _codes(monkeypatch)
    args: dict[str, Any] = {
        "method": "DELETE",
        "uri": "/open-apis/im/v1/chats/:chat_id",
        "paths_json": json.dumps({"chat_id": "oc_1"}),
        "user_key": "ou_boss",
    }
    _generic(monkeypatch, **args)
    code = _code_from(sent[0])
    _generic(monkeypatch, confirm=code, **args)
    cap, out = _generic(monkeypatch, confirm=code, **args)
    assert cap.requests == [], "a replayed code must not dismiss a second group"
    assert out.get("need_confirmation") is True


def test_a_code_does_not_authorize_a_different_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint string is identical for every group; only chat_id differs.

    A code obtained for a scratch group must not dissolve the company-wide one.
    """
    sent = _codes(monkeypatch)
    _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_scratch"}),
        user_key="ou_boss",
    )
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_everyone"}),
        user_key="ou_boss",
        confirm=_code_from(sent[0]),
    )
    assert cap.requests == []
    assert out.get("need_confirmation") is True


def test_dismiss_without_user_key_is_refused_not_waved_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """No identity to ask means no approval is obtainable — fail closed."""
    sent = _codes(monkeypatch)
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id",
        paths_json=json.dumps({"chat_id": "oc_1"}),
    )
    assert cap.requests == []
    assert out.get("need_confirmation") is True
    assert sent == []


def test_removing_a_member_is_not_treated_as_dismissing_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``DELETE /chats/:chat_id`` is a prefix of the members path.

    If the dismissal rule won on prefix alone, every 踢人 call would demand the
    confirmation dance — and a model told to get a code to remove one person is being
    trained to dismiss groups.
    """
    body = {"id_list": ["ou_a"]}
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/chats/:chat_id/members",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps(body),
        query_json=json.dumps({"member_id_type": "open_id"}),
    )
    assert out.get("need_confirmation") is not True
    assert _sent(cap.request) == _was("members_remove")


@pytest.mark.parametrize(
    "uri",
    [
        "/open-apis/im/v1/chats/:chat_id/members",
        "/open-apis/im/v1/chats/:chat_id/menu_tree",
        "/open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs",
    ],
)
def test_no_other_delete_inherits_the_dismissal_gate(monkeypatch: pytest.MonkeyPatch, uri: str) -> None:
    """Only the group itself is gated; the deeper DELETEs must stay ungated."""
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri=uri,
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps({"id_list": ["ou_a"], "chat_menu_top_level_ids": ["m1"], "tab_ids": ["t1"]}),
    )
    assert out.get("need_confirmation") is not True
    assert len(cap.requests) == 1


def test_dismissal_rule_is_less_specific_than_the_member_rule() -> None:
    """The ordering the two tests above depend on, asserted directly."""
    assert (
        _rule("DELETE", "/open-apis/im/v1/chats/:chat_id").specificity
        < _rule("DELETE", "/open-apis/im/v1/chats/:chat_id/members").specificity
    )


# --------------------------------------------------- constraints carried by the table


def test_succeed_type_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old tool defaulted to 1 so one bad id can't fail the batch."""
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/chats/:chat_id/members",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps({"id_list": ["ou_a"]}),
    )
    assert ("succeed_type", "1") in [(k, str(v)) for k, v in cap.request.queries]


def test_chat_list_pins_creation_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activity order can skip groups mid-paging; the table carries the safe default."""
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/chats",
    )
    assert ("sort_type", "ByCreateTimeAsc") in [(k, str(v)) for k, v in cap.request.queries]


@pytest.mark.parametrize(
    ("uri", "body", "bad"),
    [
        ("/open-apis/im/v1/chats", {"name": "x", "user_id_list": ["u"] * 51}, "user_id_list"),
        (
            "/open-apis/im/v1/chats/:chat_id/members",
            {"id_list": ["u"] * 51},
            "id_list",
        ),
    ],
)
def test_batch_caps_are_enforced_before_sending(
    monkeypatch: pytest.MonkeyPatch, uri: str, body: dict[str, Any], bad: str
) -> None:
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri=uri,
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps(body),
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert bad in json.dumps(out, ensure_ascii=False)


def test_required_fields_are_enforced_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(
        monkeypatch,
        method="PUT",
        uri="/open-apis/im/v1/chats/:chat_id/moderation",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps({}),
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert "moderation_setting" in json.dumps(out, ensure_ascii=False)


def test_bad_choice_is_refused_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(
        monkeypatch,
        method="PUT",
        uri="/open-apis/im/v1/chats/:chat_id/moderation",
        paths_json=json.dumps({"chat_id": "oc_1"}),
        body_json=json.dumps({"moderation_setting": "everyone"}),
    )
    assert cap.requests == []
    assert out["ok"] is False


def test_members_list_pages_through_the_generic_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The roster loop the deleted tool hand-rolled now comes from ``paginate``."""
    cap, out = _generic(
        monkeypatch,
        pages=[
            {"ok": True, "data": {"items": [{"member_id": "ou_a"}], "has_more": True, "page_token": "p2"}},
            {"ok": True, "data": {"items": [{"member_id": "ou_b"}], "has_more": False}},
        ],
        method="GET",
        uri="/open-apis/im/v1/chats/:chat_id/members",
        paths_json=json.dumps({"chat_id": "oc_1"}),
    )
    assert len(cap.requests) == 2
    assert [i["member_id"] for i in out["items"]] == ["ou_a", "ou_b"]


def test_moderation_pitfall_names_the_silently_ignored_field() -> None:
    """The trap worth keeping: 谁可以发言 is ignored by ``PUT /chats/:chat_id``."""
    text = " ".join(_rule("PUT", "/open-apis/im/v1/chats/:chat_id").pitfalls)
    assert "moderation" in text
    assert "静默" in text
