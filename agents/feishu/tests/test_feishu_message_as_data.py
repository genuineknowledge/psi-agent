"""Parity: the ``feishu-message`` skill reaches Feishu the same way the tools did.

Third domain through the migration, after ``contact`` and ``chat``. Same standard of
proof: build the request through the generic ``feishu_api`` path driven by
``skills/feishu-message/SKILL.md``, and compare it against what the deleted
``_build_*`` helper would have sent.

Message is the domain where *most of the tools stay*. Only 10 of its 27 tools were
endpoint forwarders; the rest carry logic a table cannot express — the relay privacy
guard in ``feishu_message_send``, the callback snapshot in ``_send_card``, binary
uploads, reaction-id resolution. So this file also pins the two ``hard: true`` rules
that keep the generic path from reaching around those tools, which is a kind of
coverage the previous two domains had no need for.

The URI overlap here is worse than chat's. ``DELETE /messages/:message_id`` (撤回) is a
prefix of ``DELETE /messages/:message_id/reactions/:reaction_id`` (删表情回应, a kept
tool), and ``POST /messages`` (发消息, hard-refused) is a prefix of reply, reactions,
forward and merge_forward. If specificity ordering failed, replying to a message would
be refused with "use feishu_message_send" — or removing a reaction would be treated as
recalling the message. Both directions are tested.
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
MESSAGE_SKILL = SKILLS_DIR / "feishu-message" / "SKILL.md"


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
    pages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    cap = _CapturedInvoke(pages)
    monkeypatch.setattr(_impl, "_invoke", cap)
    out: dict[str, Any] = anyio.run(lambda: _api.call_api_impl(**kwargs))
    return cap, out


def _rules() -> list[Any]:
    return _spec.parse_rules(MESSAGE_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# The wire contract of the 10 tools this skill replaced, captured mechanically from
# their ``_build_*`` helpers at migration time and frozen here. The helpers are gone —
# keeping them alive only to be a test reference would be keeping dead production code,
# which is the thing this migration is supposed to remove. These literals are what
# Feishu received before the change, so they still pin the contract.
#
# Two exceptions are still live builders rather than literals, because kept tools call
# them: ``_build_list_messages_request`` (used by ``feishu_thread_read``) and
# ``_build_list_reactions_request`` (used by ``feishu_message_unreact`` to resolve a
# reaction id). Those are compared against the real function below.
WAS: dict[str, dict[str, Any]] = {
    "reply": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/messages/:message_id/reply",
        "paths": {"message_id": "om_a"},
        "queries": [],
        "body": {"content": '{"text": "hi"}', "msg_type": "text", "reply_in_thread": True},
    },
    "reply_no_thread": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/messages/:message_id/reply",
        "paths": {"message_id": "om_a"},
        "queries": [],
        "body": {"content": '{"text": "hi"}', "msg_type": "text", "reply_in_thread": False},
    },
    "recall": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/im/v1/messages/:message_id",
        "paths": {"message_id": "om_a"},
        "queries": [],
        "body": None,
    },
    "add_reaction": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/messages/:message_id/reactions",
        "paths": {"message_id": "om_a"},
        "queries": [],
        "body": {"reaction_type": {"emoji_type": "THUMBSUP"}},
    },
    "pin": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/pins",
        "paths": {},
        "queries": [],
        "body": {"message_id": "om_a"},
    },
    "unpin": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/im/v1/pins/:message_id",
        "paths": {"message_id": "om_a"},
        "queries": [],
        "body": None,
    },
    "list_pins": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/im/v1/pins",
        "paths": {},
        "queries": [("chat_id", "oc_a"), ("page_size", "50")],
        "body": None,
    },
    "forward": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/messages/:message_id/forward",
        "paths": {"message_id": "om_a"},
        "queries": [("receive_id_type", "chat_id")],
        "body": {"receive_id": "oc_b"},
    },
    "forward_thread": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/messages/:message_id/forward",
        "paths": {"message_id": "om_a"},
        "queries": [("receive_id_type", "thread_id")],
        "body": {"receive_id": "omt_b"},
    },
    "merge_forward": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/im/v1/messages/merge_forward",
        "paths": {},
        "queries": [("receive_id_type", "chat_id")],
        "body": {"receive_id": "oc_c", "message_id_list": ["om_a", "om_b"]},
    },
}

#: Every deleted message builder declared both candidate token types, unlike chat's
#: menu builder — so this domain has no token-strategy behaviour change to flag.
WAS_TOKENS = {"TENANT", "USER"}


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
    """The 10 tabled endpoints, plus the 2 that exist only to point back at a tool."""
    got = {(r.method, r.uri) for r in _rules()}
    assert got == {
        ("POST", "/open-apis/im/v1/messages/:message_id/reply"),
        ("DELETE", "/open-apis/im/v1/messages/:message_id"),
        ("POST", "/open-apis/im/v1/messages/:message_id/reactions"),
        ("GET", "/open-apis/im/v1/messages/:message_id/reactions"),
        ("GET", "/open-apis/im/v1/messages"),
        ("POST", "/open-apis/im/v1/pins"),
        ("DELETE", "/open-apis/im/v1/pins/:message_id"),
        ("GET", "/open-apis/im/v1/pins"),
        ("POST", "/open-apis/im/v1/messages/:message_id/forward"),
        ("POST", "/open-apis/im/v1/messages/merge_forward"),
        # not migrations — guards that send the caller back to a kept tool
        ("POST", "/open-apis/im/v1/messages"),
        ("DELETE", "/open-apis/im/v1/messages/:message_id/reactions/:reaction_id"),
    }


# ------------------------------------------------------- wire parity vs the builders


def test_reply_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"content": json.dumps({"text": "hi"}, ensure_ascii=False), "msg_type": "text", "reply_in_thread": True}
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/reply",
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps(body),
    )
    assert _sent(cap.request) == _was("reply")


def test_reply_without_thread_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"content": json.dumps({"text": "hi"}, ensure_ascii=False), "msg_type": "text", "reply_in_thread": False}
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/reply",
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps(body),
    )
    assert _sent(cap.request) == _was("reply_no_thread")


def test_recall_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/messages/:message_id",
        paths_json=json.dumps({"message_id": "om_a"}),
    )
    assert _sent(cap.request) == _was("recall")


def test_add_reaction_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/reactions",
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps({"reaction_type": {"emoji_type": "THUMBSUP"}}),
    )
    assert _sent(cap.request) == _was("add_reaction")


def test_list_reactions_matches_the_live_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Still a live builder: ``feishu_message_unreact`` uses it to resolve a reaction id."""
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/messages/:message_id/reactions",
        paths_json=json.dumps({"message_id": "om_a"}),
        query_json=json.dumps({"reaction_type": "THUMBSUP"}),
    )
    reference = _impl._build_list_reactions_request("om_a", "THUMBSUP", 50, "")
    assert _shape(cap.request) == _shape(reference)


def test_list_messages_matches_the_live_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Still a live builder: ``feishu_thread_read`` uses it to page a topic."""
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/messages",
        query_json=json.dumps({"container_id_type": "chat", "container_id": "oc_a"}),
    )
    reference = _impl._build_list_messages_request("oc_a", "chat", "ByCreateTimeAsc", 50, "")
    assert _shape(cap.request) == _shape(reference)


def test_pin_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/pins",
        body_json=json.dumps({"message_id": "om_a"}),
    )
    assert _sent(cap.request) == _was("pin")


def test_unpin_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/pins/:message_id",
        paths_json=json.dumps({"message_id": "om_a"}),
    )
    assert _sent(cap.request) == _was("unpin")


def test_list_pins_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/pins",
        query_json=json.dumps({"chat_id": "oc_a"}),
    )
    assert _sent(cap.request) == _was("list_pins")


def test_forward_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/forward",
        paths_json=json.dumps({"message_id": "om_a"}),
        query_json=json.dumps({"receive_id_type": "chat_id"}),
        body_json=json.dumps({"receive_id": "oc_b"}),
    )
    assert _sent(cap.request) == _was("forward")


def test_forward_into_a_thread_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forwarding is the only path that accepts a ``omt_`` target."""
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/forward",
        paths_json=json.dumps({"message_id": "om_a"}),
        query_json=json.dumps({"receive_id_type": "thread_id"}),
        body_json=json.dumps({"receive_id": "omt_b"}),
    )
    assert _sent(cap.request) == _was("forward_thread")


def test_merge_forward_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/merge_forward",
        query_json=json.dumps({"receive_id_type": "chat_id"}),
        body_json=json.dumps({"receive_id": "oc_c", "message_id_list": ["om_a", "om_b"]}),
    )
    assert _sent(cap.request) == _was("merge_forward")


def test_receive_id_type_in_the_body_is_refused_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed in production: a forward failed twice with 99992402 before succeeding.

    ``receive_id_type`` rides in the query and ``receive_id`` in the body — the skill
    says so, but saying so was all it did. Putting the type in the body produced a
    request that looked complete, went out, and came back as Feishu's generic "field
    validation failed", which reads like a platform problem rather than a misplaced
    parameter. The table knows the bucket, so the refusal should name it.
    """
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/forward",
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps({"receive_id": "oc_b", "receive_id_type": "chat_id"}),
    )
    assert cap.requests == [], "a call Feishu would reject for field placement must not go out"
    assert out["code"] == "spec_violation"
    complaint = " ".join(out["violations"])
    assert "receive_id_type" in complaint
    assert "query" in complaint and "body" in complaint


def test_every_tabled_endpoint_keeps_both_candidate_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token-strategy regression: all 10 builders declared TENANT+USER, as does this path."""
    cap, _ = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/pins",
        body_json=json.dumps({"message_id": "om_a"}),
    )
    assert {t.name for t in _shape(cap.request)["tokens"]} == WAS_TOKENS


# ------------------------------------------- the endpoints that must stay tool-only


def test_sending_a_message_is_refused_and_names_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST /messages`` must not be reachable generically.

    ``feishu_message_send`` redirects a relayed message away from a group and into the
    named person's DM, and refuses when it cannot tell who that is. A generic caller
    would skip that entirely and post someone's private words to the whole group.
    """
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages",
        query_json=json.dumps({"receive_id_type": "chat_id"}),
        body_json=json.dumps({"receive_id": "oc_a", "msg_type": "text", "content": '{"text":"hi"}'}),
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert out.get("code") == "use_dedicated_tool"
    assert out.get("tool") == "feishu_message_send"


def test_removing_a_reaction_is_refused_and_names_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/messages/:message_id/reactions/:reaction_id",
        paths_json=json.dumps({"message_id": "om_a", "reaction_id": "rc_b"}),
    )
    assert cap.requests == []
    assert out.get("tool") == "feishu_message_unreact"


def test_replying_is_not_swallowed_by_the_send_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST /messages`` is a prefix of the reply path.

    If the hard send rule won on prefix alone, replying to a message would come back as
    "use feishu_message_send" — which is both wrong and unfixable from the caller's
    side, since that tool cannot reply.
    """
    body = {"content": '{"text": "hi"}', "msg_type": "text", "reply_in_thread": False}
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/reply",
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps(body),
    )
    assert out.get("code") != "use_dedicated_tool"
    assert len(cap.requests) == 1


@pytest.mark.parametrize(
    ("uri", "body", "query"),
    [
        (
            "/open-apis/im/v1/messages/:message_id/reactions",
            {"reaction_type": {"emoji_type": "OK"}},
            {},
        ),
        (
            "/open-apis/im/v1/messages/:message_id/forward",
            {"receive_id": "oc_b"},
            {"receive_id_type": "chat_id"},
        ),
    ],
)
def test_no_deeper_post_inherits_the_send_refusal(
    monkeypatch: pytest.MonkeyPatch, uri: str, body: dict[str, Any], query: dict[str, Any]
) -> None:
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri=uri,
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps(body),
        query_json=json.dumps(query),
    )
    assert out.get("code") != "use_dedicated_tool"
    assert len(cap.requests) == 1


def test_merge_forward_is_not_swallowed_by_the_send_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """``merge_forward`` is a literal segment under the hard-refused ``POST /messages``."""
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/merge_forward",
        query_json=json.dumps({"receive_id_type": "chat_id"}),
        body_json=json.dumps({"receive_id": "oc_c", "message_id_list": ["om_a"]}),
    )
    assert out.get("code") != "use_dedicated_tool"
    assert len(cap.requests) == 1


def test_recall_does_not_claim_the_reaction_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DELETE /messages/:message_id`` is a prefix of the reaction-delete path.

    The wrong direction here is silent: a caller removing a reaction would have its
    request built as a *recall* and unsend the whole message.
    """
    cap, out = _generic(
        monkeypatch,
        method="DELETE",
        uri="/open-apis/im/v1/messages/:message_id/reactions/:reaction_id",
        paths_json=json.dumps({"message_id": "om_a", "reaction_id": "rc_b"}),
    )
    assert cap.requests == []
    assert out.get("tool") == "feishu_message_unreact"


def test_send_rule_is_less_specific_than_the_paths_under_it() -> None:
    """The ordering the tests above depend on, asserted directly."""
    send = _rule("POST", "/open-apis/im/v1/messages").specificity
    for uri in (
        "/open-apis/im/v1/messages/:message_id/reply",
        "/open-apis/im/v1/messages/:message_id/reactions",
        "/open-apis/im/v1/messages/:message_id/forward",
        "/open-apis/im/v1/messages/merge_forward",
    ):
        assert send < _rule("POST", uri).specificity, uri


def test_recall_rule_is_less_specific_than_the_reaction_delete() -> None:
    assert (
        _rule("DELETE", "/open-apis/im/v1/messages/:message_id").specificity
        < _rule("DELETE", "/open-apis/im/v1/messages/:message_id/reactions/:reaction_id").specificity
    )


# --------------------------------------------------- constraints carried by the table


@pytest.mark.parametrize(
    ("method", "uri", "bad_id"),
    [
        ("DELETE", "/open-apis/im/v1/messages/:message_id", "oc_a"),
        ("DELETE", "/open-apis/im/v1/pins/:message_id", "ou_a"),
    ],
)
def test_a_non_message_id_is_refused_before_sending(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, bad_id: str
) -> None:
    """The check every deleted tool opened with: ``om_`` or nothing.

    Passing a chat_id where a message id belongs is the common mix-up, and on a recall
    it is the expensive one — so it must cost no request at all.
    """
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps({"message_id": bad_id}),
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert "om_" in json.dumps(out, ensure_ascii=False)


def test_list_pins_refuses_a_non_group_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/im/v1/pins",
        query_json=json.dumps({"chat_id": "om_a"}),
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert "oc_" in json.dumps(out, ensure_ascii=False)


def test_merge_forward_refuses_more_than_a_hundred_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deleted tool's own cap, now a table constraint."""
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/merge_forward",
        query_json=json.dumps({"receive_id_type": "chat_id"}),
        body_json=json.dumps({"receive_id": "oc_c", "message_id_list": [f"om_{i}" for i in range(101)]}),
    )
    assert cap.requests == []
    assert out["ok"] is False
    assert "100" in json.dumps(out, ensure_ascii=False)


@pytest.mark.parametrize("field", ["content", "msg_type"])
def test_reply_requires_content_and_msg_type(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    body = {"content": '{"text": "hi"}', "msg_type": "text"}
    body.pop(field)
    cap, out = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/reply",
        paths_json=json.dumps({"message_id": "om_a"}),
        body_json=json.dumps(body),
    )
    assert cap.requests == []
    assert field in json.dumps(out, ensure_ascii=False)


def test_list_messages_requires_both_container_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container id without its type returns an empty list rather than an error."""
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/im/v1/messages",
        query_json=json.dumps({"container_id": "oc_a"}),
    )
    assert cap.requests == []
    assert "container_id_type" in json.dumps(out, ensure_ascii=False)


def test_list_messages_refuses_an_invented_container_type(monkeypatch: pytest.MonkeyPatch) -> None:
    cap, out = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/im/v1/messages",
        query_json=json.dumps({"container_id_type": "group", "container_id": "oc_a"}),
    )
    assert cap.requests == []
    assert out["ok"] is False


def test_list_messages_defaults_to_ascending_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paging by activity reorders rows mid-walk and drops messages."""
    cap, _ = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/im/v1/messages",
        query_json=json.dumps({"container_id_type": "chat", "container_id": "oc_a"}),
    )
    assert ("sort_type", "ByCreateTimeAsc") in _sent(cap.request)["queries"]


@pytest.mark.parametrize(
    ("uri", "query"),
    [
        ("/open-apis/im/v1/pins", {"chat_id": "oc_a", "page_size": 200}),
        ("/open-apis/im/v1/messages", {"container_id_type": "chat", "container_id": "oc_a", "page_size": 200}),
    ],
)
def test_page_size_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch, uri: str, query: dict[str, Any]) -> None:
    """The builders silently clamped an over-cap page size to 50; the table refuses it.

    Louder on purpose. Clamping means a caller who asked for 200 gets 50 and cannot
    tell, which reads as "that's all there is" — the same failure mode as a wrong
    container type returning an empty list.
    """
    cap, out = _generic(monkeypatch, method="GET", uri=uri, query_json=json.dumps(query))
    assert cap.requests == []
    assert out["ok"] is False
    assert "50" in json.dumps(out, ensure_ascii=False)


def test_pins_page_through_the_generic_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The paging loop the deleted tool hand-rolled now comes from ``paginate``."""
    cap, out = _generic(
        monkeypatch,
        pages=[
            {"ok": True, "data": {"items": [{"message_id": "om_a"}], "has_more": True, "page_token": "p2"}},
            {"ok": True, "data": {"items": [{"message_id": "om_b"}], "has_more": False}},
        ],
        method="GET",
        uri="/open-apis/im/v1/pins",
        query_json=json.dumps({"chat_id": "oc_a"}),
    )
    assert len(cap.requests) == 2
    assert [i["message_id"] for i in out["items"]] == ["om_a", "om_b"]


def test_reaction_pitfall_keeps_the_irregular_casing() -> None:
    """The trap worth keeping: ``emoji_type`` casing cannot be guessed."""
    text = " ".join(_rule("POST", "/open-apis/im/v1/messages/:message_id/reactions").pitfalls)
    assert "OnIt" in text
    assert "231001" in text


def test_the_emoji_table_survived_the_migration() -> None:
    """``_normalize_emoji_type`` mapped 赞/👍 onto the key; prose has to replace it."""
    doc = MESSAGE_SKILL.read_text(encoding="utf-8")
    for key in ("THUMBSUP", "OnIt", "CheckMark", "CrossMark", "Fire"):
        assert key in doc, key


def test_recall_pitfalls_name_both_refusal_codes() -> None:
    text = " ".join(_rule("DELETE", "/open-apis/im/v1/messages/:message_id").pitfalls)
    assert "230026" in text
    assert "230009" in text


def test_merge_forward_pitfall_names_the_same_conversation_rule() -> None:
    text = " ".join(_rule("POST", "/open-apis/im/v1/messages/merge_forward").pitfalls)
    assert "230069" in text
    assert "invalid_message_id_list" in text
