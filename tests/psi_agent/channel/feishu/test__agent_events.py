"""Tests for Feishu agent-event forwarding: SDK unwrapping + idempotency keys."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import anyio
import pytest
from loguru import logger

import psi_agent.channel.feishu._agent_events as agent_events
from psi_agent.channel._event_defs import (
    ChannelEventDef,
    channel_events_fingerprint,
    load_channel_event_defs,
)
from psi_agent.channel.feishu._agent_events import (
    _delivery_id,
    _forward_one,
    _LiveEventDefs,
    _plainify,
    _raw_to_dict,
    _register_platform_map,
)

HAITUN = Path(__file__).resolve().parents[4] / "agents" / "feishu"


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


def _def_with(map_fn: Any, name: str = "feishu.test.probe", *, filters: bool = False) -> ChannelEventDef:
    return ChannelEventDef(
        dir_name="probe",
        name=name,
        source="feishu",
        kind="platform_map",
        platform_event="im.message.receive_v1",
        description="",
        map_fn=map_fn,
        produce_fn=None,
        path=HAITUN,
        filters=filters,
    )


@pytest.mark.anyio
async def test_empty_mapping_is_logged_with_shape_and_paths(caplog: pytest.LogCaptureFixture) -> None:
    """A mapper returning [] must not be silent — that is indistinguishable from dedup."""

    def _map(raw: dict[str, Any]) -> list[dict[str, Any]]:
        # The classic defect: chat_id lives at event.message.chat_id, not event.chat_id.
        event = raw.get("event") or {}
        return [] if not event.get("chat_id") else [{"payload": {}}]

    messages: list[str] = []
    handle = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
    try:
        posted = await _forward(
            _def_with(_map),
            {"header": {"event_id": "evt-e1"}, "event": {"message": {"chat_id": "oc_1"}}},
        )
    finally:
        logger.remove(handle)
    assert posted == []
    blob = "\n".join(messages)
    assert "returned no envelopes" in blob
    # The diagnostic must name the path the mapper should have used.
    assert "message.chat_id" in blob


@pytest.mark.anyio
async def test_raising_mapper_is_logged_with_shape() -> None:
    def _map(_raw: dict[str, Any]) -> list[dict[str, Any]]:
        raise KeyError("chat_id")

    messages: list[str] = []
    handle = logger.add(lambda m: messages.append(m.record["message"]), level="ERROR")
    try:
        posted = await _forward(_def_with(_map), {"header": {"event_id": "evt-e2"}, "event": {"message": {}}})
    finally:
        logger.remove(handle)
    assert posted == []
    blob = "\n".join(messages)
    assert "map_event raised" in blob
    assert "KeyError" in blob


@pytest.mark.anyio
async def test_declared_filter_does_not_warn_on_empty_result() -> None:
    """`filters: true` means [] is normal — warning on each would be routine noise.

    identity_changed subscribes to contact.user.updated_v3 and drops avatar /
    phone edits, which are most deliveries org-wide.
    """
    warnings: list[str] = []
    debugs: list[str] = []
    warn_handle = logger.add(lambda m: warnings.append(m.record["message"]), level="WARNING")
    debug_handle = logger.add(lambda m: debugs.append(m.record["message"]), level="DEBUG")
    try:
        posted = await _forward(
            _def_with(lambda _raw: [], filters=True),
            {"header": {"event_id": "evt-f1"}, "event": {"message": {"chat_id": "oc_1"}}},
        )
    finally:
        logger.remove(warn_handle)
        logger.remove(debug_handle)
    assert posted == []
    assert not [m for m in warnings if "returned no envelopes" in m]
    # Still diagnosable — same shape/paths detail, just at DEBUG.
    blob = "\n".join(debugs)
    assert "returned no envelopes" in blob
    assert "message.chat_id" in blob


@pytest.mark.anyio
async def test_undeclared_empty_result_still_warns_and_suggests_filters() -> None:
    """Without `filters: true`, [] stays a WARNING and names the escape hatch."""
    messages: list[str] = []
    handle = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
    try:
        await _forward(_def_with(lambda _raw: []), {"header": {"event_id": "evt-f2"}, "event": {}})
    finally:
        logger.remove(handle)
    blob = "\n".join(messages)
    assert "returned no envelopes" in blob
    assert "filters: true" in blob


def test_live_defs_group_by_platform_event() -> None:
    live = _LiveEventDefs()
    assert live.platform_events() == []
    one = _def_with(lambda _raw: [], name="feishu.test.one")
    live.replace([one])
    assert live.platform_events() == ["im.message.receive_v1"]
    assert [d.name for d in live.for_platform("im.message.receive_v1")] == ["feishu.test.one"]


def test_live_defs_swap_takes_effect_without_reregistration() -> None:
    """Hot reload works by swapping the entry the installed processor reads."""
    live = _LiveEventDefs()
    live.replace([_def_with(lambda _raw: [], name="feishu.test.before")])
    live.replace([_def_with(lambda _raw: [], name="feishu.test.after")])
    assert [d.name for d in live.for_platform("im.message.receive_v1")] == ["feishu.test.after"]


def test_live_defs_ignore_incomplete_defs() -> None:
    """Synthetic defs and mapper-less defs own no platform event."""
    live = _LiveEventDefs()
    synthetic = ChannelEventDef(
        dir_name="s",
        name="haitun.test.synthetic",
        source="haitun",
        kind="synthetic",
        platform_event="",
        description="",
        map_fn=None,
        produce_fn=None,
        path=HAITUN,
    )
    live.replace([synthetic, _def_with(None, name="feishu.test.nomap")])
    assert live.platform_events() == []


def test_live_defs_keep_both_owners_of_one_platform_event() -> None:
    """Two definitions may map the same Feishu event; both must still fire."""
    live = _LiveEventDefs()
    live.replace(
        [
            _def_with(lambda _raw: [], name="feishu.test.a"),
            _def_with(lambda _raw: [], name="feishu.test.b"),
        ]
    )
    assert sorted(d.name for d in live.for_platform("im.message.receive_v1")) == [
        "feishu.test.a",
        "feishu.test.b",
    ]


@pytest.mark.anyio
async def test_fingerprint_changes_when_map_py_is_edited(tmp_path: Path) -> None:
    """The watcher's change detector must notice an edited mapper."""
    event_dir = tmp_path / "channel_events" / "feishu" / "probe"
    event_dir.mkdir(parents=True)
    (event_dir / "EVENT.yaml").write_text(
        "name: feishu.test.probe\nsource: feishu\nkind: platform_map\nplatform_event: im.message.receive_v1\n",
        encoding="utf-8",
    )
    map_py = event_dir / "map.py"
    map_py.write_text("def map_event(raw):\n    return []\n", encoding="utf-8")

    before = await channel_events_fingerprint(tmp_path, "feishu")
    assert before
    assert await channel_events_fingerprint(tmp_path, "feishu") == before

    map_py.write_text("def map_event(raw):\n    return [{'payload': {}}]\n", encoding="utf-8")
    after = await channel_events_fingerprint(tmp_path, "feishu")
    assert after != before

    # A brand-new event directory must also register as a change.
    other = tmp_path / "channel_events" / "feishu" / "second"
    other.mkdir()
    (other / "EVENT.yaml").write_text("name: feishu.test.second\nkind: synthetic\n", encoding="utf-8")
    assert await channel_events_fingerprint(tmp_path, "feishu") != after


@pytest.mark.anyio
async def test_fingerprint_empty_when_tree_absent(tmp_path: Path) -> None:
    assert await channel_events_fingerprint(tmp_path, "feishu") == ""


@pytest.mark.anyio
async def test_reloaded_mapper_is_used_on_next_delivery(tmp_path: Path) -> None:
    """End-to-end of gap 3: fix map.py, next event uses the fix — no restart."""
    event_dir = tmp_path / "channel_events" / "feishu" / "probe"
    event_dir.mkdir(parents=True)
    (event_dir / "EVENT.yaml").write_text(
        "name: feishu.test.probe\nsource: feishu\nkind: platform_map\nplatform_event: im.message.receive_v1\n",
        encoding="utf-8",
    )
    map_py = event_dir / "map.py"
    # Broken: reads chat_id from the wrong level, so it drops every event.
    map_py.write_text(
        "def map_event(raw):\n"
        "    event = raw.get('event') or {}\n"
        "    chat_id = event.get('chat_id')\n"
        "    if not chat_id:\n"
        "        return []\n"
        "    return [{'payload': {'chat_id': chat_id}}]\n",
        encoding="utf-8",
    )
    sample = {"header": {"event_id": "evt-r1"}, "event": {"message": {"chat_id": "oc_live"}}}

    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))
    broken = live.for_platform("im.message.receive_v1")[0]
    assert await _forward(broken, sample) == []

    # The agent fixes the field path; the watcher re-loads the tree.
    map_py.write_text(
        "def map_event(raw):\n"
        "    message = (raw.get('event') or {}).get('message') or {}\n"
        "    chat_id = message.get('chat_id')\n"
        "    if not chat_id:\n"
        "        return []\n"
        "    return [{'payload': {'chat_id': chat_id}}]\n",
        encoding="utf-8",
    )
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))
    fixed = live.for_platform("im.message.receive_v1")[0]
    posted = await _forward(fixed, sample)
    assert [e["payload"]["chat_id"] for e in posted] == ["oc_live"]


class _FakeDispatcher:
    def __init__(self) -> None:
        self._processorMap: dict[str, Any] = {}


class _FakeChannel:
    def __init__(self) -> None:
        self.dispatcher = _FakeDispatcher()


def _write_event_dir(root: Path, slug: str, event_name: str, map_source: str) -> Path:
    event_dir = root / "channel_events" / "feishu" / slug
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "EVENT.yaml").write_text(
        f"name: {event_name}\nsource: feishu\nkind: platform_map\nplatform_event: im.message.receive_v1\n",
        encoding="utf-8",
    )
    (event_dir / "map.py").write_text(map_source, encoding="utf-8")
    return event_dir


@pytest.mark.anyio
async def test_watcher_picks_up_a_brand_new_event_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gap 3 end-to-end: a directory added after startup registers itself."""
    monkeypatch.setattr(agent_events, "_RELOAD_INTERVAL_SECONDS", 0.01)
    channel = _FakeChannel()
    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))
    assert live.platform_events() == []

    async def _resolve(_open_id: str | None) -> Any:
        raise AssertionError("not reached")

    async with anyio.create_task_group() as tg:
        tg.start_soon(agent_events._watch_channel_events, live, tmp_path, channel, _resolve, lambda *a, **k: None)
        # The agent writes a new event while the Channel is already running.
        await anyio.sleep(0.05)
        _write_event_dir(
            tmp_path,
            "chat_message_received",
            "feishu.chat.message_received",
            "def map_event(raw):\n    return [{'payload': {}}]\n",
        )
        with anyio.fail_after(5):
            while not live.platform_events():
                await live.wait_for_reload()
        tg.cancel_scope.cancel()

    assert live.platform_events() == ["im.message.receive_v1"]
    # A processor is installed for the new event without a restart.
    assert "p2.im.message.receive_v1" in channel.dispatcher._processorMap


@pytest.mark.anyio
async def test_watcher_swaps_an_edited_mapper_without_reregistering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edited map.py takes effect, and the processor is installed only once."""
    monkeypatch.setattr(agent_events, "_RELOAD_INTERVAL_SECONDS", 0.01)
    map_py = (
        _write_event_dir(
            tmp_path,
            "chat_message_received",
            "feishu.chat.message_received",
            "def map_event(raw):\n    return []\n",
        )
        / "map.py"
    )
    channel = _FakeChannel()
    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))
    first = live.for_platform("im.message.receive_v1")[0]
    assert first.map_fn is not None
    assert first.map_fn({"event": {}}) == []

    async def _resolve(_open_id: str | None) -> Any:
        raise AssertionError("not reached")

    installed = _register_platform_map(live, channel, _resolve, lambda *a, **k: None)
    assert installed == 2  # p1 + p2
    processor = channel.dispatcher._processorMap["p2.im.message.receive_v1"]

    async with anyio.create_task_group() as tg:
        tg.start_soon(agent_events._watch_channel_events, live, tmp_path, channel, _resolve, lambda *a, **k: None)
        await anyio.sleep(0.05)
        map_py.write_text("def map_event(raw):\n    return [{'payload': {'ok': 1}}]\n", encoding="utf-8")
        with anyio.fail_after(5):
            while live.for_platform("im.message.receive_v1")[0].map_fn is first.map_fn:
                await live.wait_for_reload()
        tg.cancel_scope.cancel()

    reloaded = live.for_platform("im.message.receive_v1")[0]
    assert reloaded.map_fn is not None
    assert reloaded.map_fn({"event": {}}) == [{"payload": {"ok": 1}}]
    # Same processor object: the swap happens behind it, not by re-registering.
    assert channel.dispatcher._processorMap["p2.im.message.receive_v1"] is processor


@pytest.mark.anyio
async def test_watcher_survives_a_broken_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A map.py with a syntax error must not kill the watcher or the old mapper."""
    monkeypatch.setattr(agent_events, "_RELOAD_INTERVAL_SECONDS", 0.01)
    map_py = (
        _write_event_dir(
            tmp_path,
            "chat_message_received",
            "feishu.chat.message_received",
            "def map_event(raw):\n    return [{'payload': {'v': 1}}]\n",
        )
        / "map.py"
    )
    channel = _FakeChannel()
    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))

    async def _resolve(_open_id: str | None) -> Any:
        raise AssertionError("not reached")

    async with anyio.create_task_group() as tg:
        tg.start_soon(agent_events._watch_channel_events, live, tmp_path, channel, _resolve, lambda *a, **k: None)
        await anyio.sleep(0.05)
        map_py.write_text("def map_event(raw:\n", encoding="utf-8")  # syntax error
        await anyio.sleep(0.1)
        # Broken file loads nothing, so the event now has no owner — but the
        # watcher is still alive and recovers when the file is fixed.
        map_py.write_text("def map_event(raw):\n    return [{'payload': {'v': 2}}]\n", encoding="utf-8")
        with anyio.fail_after(5):
            while True:
                owners = live.for_platform("im.message.receive_v1")
                if owners and owners[0].map_fn and owners[0].map_fn({"event": {}}) == [{"payload": {"v": 2}}]:
                    break
                await live.wait_for_reload()
        tg.cancel_scope.cancel()


class _BuiltinProcessor:
    """Stands in for the SDK processor that ``channel.on("message")`` installs."""

    def __init__(self) -> None:
        self.seen: list[Any] = []

    # Named ``type`` to match the SDK's IEventProcessor protocol; that shadows the
    # builtin inside this class body, so the return type is spelled via ``builtins``.
    def type(self) -> builtins.type:
        return dict

    def do(self, data: Any) -> str:
        self.seen.append(data)
        return "builtin-result"


@pytest.mark.anyio
async def test_existing_processor_is_wrapped_not_skipped(tmp_path: Path) -> None:
    """The bot's own reply path owns ``p2.im.message.receive_v1``.

    Skipping that key (the old behaviour) left the mapper on ``p1.*`` only, which
    the WS transport never delivers to — the event loaded and never fired.
    """
    _write_event_dir(
        tmp_path,
        "message_received",
        "feishu.message.received",
        "def map_event(raw):\n    return [{'payload': {}}]\n",
    )
    channel = _FakeChannel()
    builtin = _BuiltinProcessor()
    channel.dispatcher._processorMap["p2.im.message.receive_v1"] = builtin
    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))

    async def _resolve(_open_id: str | None) -> Any:
        raise AssertionError("not reached")

    scheduled: list[Any] = []
    installed = _register_platform_map(live, channel, _resolve, lambda *a, **k: scheduled.append(a))

    assert installed == 2  # p1 registered fresh, p2 wrapped
    wrapper = channel.dispatcher._processorMap["p2.im.message.receive_v1"]
    assert isinstance(wrapper, agent_events._AgentEventFanout)
    # Deserialization target must still come from the built-in processor.
    assert wrapper.type() is dict

    result = wrapper.do({"event": {}})
    assert result == "builtin-result"  # built-in behaviour preserved
    assert builtin.seen == [{"event": {}}]  # and it ran
    assert scheduled  # the mapper was fanned out to as well


@pytest.mark.anyio
async def test_fanout_failure_does_not_break_the_builtin_handler(tmp_path: Path) -> None:
    """A broken mapper must never cost the user their reply."""
    _write_event_dir(
        tmp_path,
        "message_received",
        "feishu.message.received",
        "def map_event(raw):\n    return [{'payload': {}}]\n",
    )
    channel = _FakeChannel()
    builtin = _BuiltinProcessor()
    channel.dispatcher._processorMap["p2.im.message.receive_v1"] = builtin
    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))

    async def _resolve(_open_id: str | None) -> Any:
        raise AssertionError("not reached")

    def _explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("portal closed")

    _register_platform_map(live, channel, _resolve, _explode)
    wrapper = channel.dispatcher._processorMap["p2.im.message.receive_v1"]

    assert wrapper.do({"event": {}}) == "builtin-result"
    assert builtin.seen == [{"event": {}}]


@pytest.mark.anyio
async def test_reload_does_not_stack_fanouts(tmp_path: Path) -> None:
    """Re-registration is idempotent: no wrapper-around-wrapper on every reload."""
    _write_event_dir(
        tmp_path,
        "message_received",
        "feishu.message.received",
        "def map_event(raw):\n    return [{'payload': {}}]\n",
    )
    channel = _FakeChannel()
    builtin = _BuiltinProcessor()
    channel.dispatcher._processorMap["p2.im.message.receive_v1"] = builtin
    live = _LiveEventDefs()
    live.replace(await load_channel_event_defs(tmp_path, "feishu"))

    async def _resolve(_open_id: str | None) -> Any:
        raise AssertionError("not reached")

    scheduled: list[Any] = []

    def _record(*a: Any, **_k: Any) -> None:
        scheduled.append(a)

    _register_platform_map(live, channel, _resolve, _record)
    wrapper = channel.dispatcher._processorMap["p2.im.message.receive_v1"]
    # Second pass (what the hot-reload watcher does) must install nothing new.
    assert _register_platform_map(live, channel, _resolve, _record) == 0
    assert channel.dispatcher._processorMap["p2.im.message.receive_v1"] is wrapper

    wrapper.do({"event": {}})
    # One delivery fans out exactly once, not once per reload.
    assert len(scheduled) == 1
    assert builtin.seen == [{"event": {}}]
