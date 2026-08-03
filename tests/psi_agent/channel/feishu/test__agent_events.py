"""Tests for Feishu agent-event forwarding: SDK unwrapping + idempotency keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from psi_agent.channel._event_defs import ChannelEventDef, load_channel_event_defs
from psi_agent.channel.feishu._agent_events import _delivery_id, _forward_one, _plainify, _raw_to_dict

HAITUN = Path(__file__).resolve().parents[4] / "examples" / "haitun-workspace"


class _UserId:
    """Stand-in for lark_channel's UserId: plain attrs, no dict()/to_dict()."""

    def __init__(self, open_id: str) -> None:
        self.open_id = open_id
        self.user_id = "uid-" + open_id
        self.union_id = "on-" + open_id


class _Member:
    def __init__(self, name: str, open_id: str) -> None:
        self.name = name
        self.tenant_key = "tk"
        self.user_id = _UserId(open_id)


class _EventData:
    def __init__(self, chat_id: str, members: list[_Member]) -> None:
        self.chat_id = chat_id
        self.users = members
        self.operator_id = _UserId("ou_operator")
        self.external = False
        self.name = "触发事件测试"
        self._private = "hidden"


class _Header:
    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        self.event_type = "im.chat.member.user.added_v1"
        self.create_time = "1754200000000"


class _P2Event:
    """Shape of lark_channel's P2ImChatMemberUserAddedV1."""

    def __init__(self, event_id: str, chat_id: str, members: list[_Member]) -> None:
        self.header = _Header(event_id)
        self.event = _EventData(chat_id, members)
        self.schema = "2.0"


def test_plainify_unwraps_nested_sdk_objects() -> None:
    out = _plainify(_Member("张三", "ou_a"))
    assert out == {
        "name": "张三",
        "tenant_key": "tk",
        "user_id": {"open_id": "ou_a", "user_id": "uid-ou_a", "union_id": "on-ou_a"},
    }


def test_plainify_skips_private_attrs() -> None:
    out = _plainify(_EventData("oc_x", []))
    assert "_private" not in out


def test_plainify_survives_self_reference() -> None:
    class _Loop:
        def __init__(self) -> None:
            self.me: Any = self

    # Must terminate (depth-capped) rather than recurse forever.
    assert isinstance(_plainify(_Loop()), dict)


def test_raw_to_dict_exposes_event_fields_not_repr() -> None:
    """Regression: SDK objects used to degrade to repr(), hiding every field."""
    raw = _raw_to_dict(_P2Event("evt-1", "oc_chat", [_Member("张三", "ou_new")]))
    event = raw["event"]
    assert isinstance(event, dict)
    assert event["chat_id"] == "oc_chat"
    assert event["users"][0]["user_id"]["open_id"] == "ou_new"
    assert raw["header"]["event_id"] == "evt-1"
    assert "raw" not in raw


# Every real lark P2 model shipped by the SDK, so the unwrapping is proven for
# all event families — not just the member-added one that surfaced the bug.
_P2_MODELS = [
    (
        "im.message.receive_v1",
        "p2_im_message_receive_v1",
        "P2ImMessageReceiveV1",
        {
            "sender": {"sender_id": {"open_id": "ou_s"}, "sender_type": "user"},
            "message": {"message_id": "om_1", "chat_id": "oc_1", "message_type": "text"},
        },
        ("message", "message_id"),
    ),
    (
        "im.message.recalled_v1",
        "p2_im_message_recalled_v1",
        "P2ImMessageRecalledV1",
        {"message_id": "om_2", "chat_id": "oc_2", "recall_type": "message_owner"},
        ("chat_id",),
    ),
    (
        "im.message.reaction.created_v1",
        "p2_im_message_reaction_created_v1",
        "P2ImMessageReactionCreatedV1",
        {"message_id": "om_3", "reaction_type": {"emoji_type": "SMILE"}, "operator_type": "user"},
        ("message_id",),
    ),
    (
        "im.chat.updated_v1",
        "p2_im_chat_updated_v1",
        "P2ImChatUpdatedV1",
        {"chat_id": "oc_4", "operator_id": {"open_id": "ou_o"}, "after_change": {"name": "新群名"}},
        ("chat_id",),
    ),
    (
        "im.chat.disbanded_v1",
        "p2_im_chat_disbanded_v1",
        "P2ImChatDisbandedV1",
        {"chat_id": "oc_5", "operator_id": {"open_id": "ou_o"}, "name": "某群"},
        ("chat_id",),
    ),
    (
        "im.chat.member.user.deleted_v1",
        "p2_im_chat_member_user_deleted_v1",
        "P2ImChatMemberUserDeletedV1",
        {"chat_id": "oc_6", "users": [{"user_id": {"open_id": "ou_gone"}}]},
        ("chat_id",),
    ),
]


@pytest.mark.parametrize(("event_type", "module", "cls_name", "body", "path"), _P2_MODELS)
def test_raw_to_dict_unwraps_every_p2_model(
    event_type: str, module: str, cls_name: str, body: dict[str, Any], path: tuple[str, ...]
) -> None:
    """Any P2 event must reach map_event as plain data with its header intact."""
    mod = pytest.importorskip(f"lark_channel.api.im.v1.model.{module}")
    cls = getattr(mod, cls_name)
    raw = _raw_to_dict(cls({"header": {"event_id": f"e-{event_type}", "event_type": event_type}, "event": body}))
    event = raw["event"]
    assert isinstance(event, dict), f"{event_type} degraded to {event!r}"
    assert "raw" not in event, f"{event_type} fell back to repr()"
    # Walk to a nested leaf to prove recursion, not just the top level.
    node: Any = event
    for key in path:
        assert isinstance(node, dict) and key in node, f"{event_type}: missing {'.'.join(path)}"
        node = node[key]
    assert _delivery_id(raw) == f"e-{event_type}"


def test_raw_to_dict_passes_dicts_through() -> None:
    raw = _raw_to_dict({"event": {"chat_id": "oc_y"}, "uuid": "u-9"})
    assert raw["event"]["chat_id"] == "oc_y"
    assert raw["uuid"] == "u-9"


def test_delivery_id_prefers_header_then_uuid() -> None:
    assert _delivery_id({"header": {"event_id": "evt-7"}}) == "evt-7"
    assert _delivery_id({"uuid": "u-3"}) == "u-3"
    assert _delivery_id({"header": {}, "event": {}}) == ""


async def _forward(edef: ChannelEventDef, raw: Any) -> list[dict[str, Any]]:
    """Run _forward_one against a recording ChannelCore stub."""
    posted: list[dict[str, Any]] = []

    class _Core:
        async def post_event(self, env: dict[str, Any]) -> None:
            posted.append(env)

    async def _resolve(_open_id: str | None) -> Any:
        return _Core()

    await _forward_one(edef, raw, _resolve)
    return posted


async def _member_added_def() -> ChannelEventDef:
    defs = await load_channel_event_defs(HAITUN, "feishu")
    return next(d for d in defs if d.name == "feishu.chat.member_added")


@pytest.mark.anyio
async def test_bundled_mapper_reads_sdk_event() -> None:
    """The shipped mapper must see real fields, not repr() text."""
    edef = await _member_added_def()
    posted = await _forward(edef, _P2Event("evt-1", "oc_chat", [_Member("张三", "ou_new")]))
    assert len(posted) == 1
    assert posted[0]["payload"]["chat_id"] == "oc_chat"
    assert posted[0]["payload"]["member_open_id"] == "ou_new"


@pytest.mark.anyio
async def test_rejoin_is_not_deduped_but_retry_is() -> None:
    """Same person re-joining gets a fresh key; a replayed delivery does not."""
    edef = await _member_added_def()
    first = await _forward(edef, _P2Event("evt-1", "oc_chat", [_Member("张三", "ou_new")]))
    again = await _forward(edef, _P2Event("evt-2", "oc_chat", [_Member("张三", "ou_new")]))
    retry = await _forward(edef, _P2Event("evt-1", "oc_chat", [_Member("张三", "ou_new")]))
    assert first[0]["idempotency_key"] != again[0]["idempotency_key"]
    assert first[0]["idempotency_key"] == retry[0]["idempotency_key"]


@pytest.mark.anyio
async def test_one_envelope_per_member() -> None:
    edef = await _member_added_def()
    posted = await _forward(edef, _P2Event("evt-3", "oc_chat", [_Member("张三", "ou_a"), _Member("李四", "ou_b")]))
    assert [e["payload"]["member_open_id"] for e in posted] == ["ou_a", "ou_b"]
    assert len({e["idempotency_key"] for e in posted}) == 2


@pytest.mark.anyio
async def test_framework_fills_key_when_mapper_omits_it() -> None:
    """A mapper with no key must still get a per-delivery one, not an empty one."""

    def _map(_raw: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"payload": {"a": 1}}, {"payload": {"a": 2}}]

    edef = ChannelEventDef(
        dir_name="keyless",
        name="feishu.test.keyless",
        source="feishu",
        kind="platform_map",
        platform_event="im.test.keyless_v1",
        description="",
        map_fn=_map,
        produce_fn=None,
        path=HAITUN,
    )
    first = await _forward(edef, {"header": {"event_id": "evt-k1"}, "event": {}})
    second = await _forward(edef, {"header": {"event_id": "evt-k2"}, "event": {}})
    keys = [e["idempotency_key"] for e in first + second]
    assert len(set(keys)) == 4, keys
    assert all(k for k in keys)
