"""Tests for agent-package channel_events loader + synthetic runner + maps."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._event_defs import ChannelEventDef, load_channel_event_defs
from psi_agent.channel._synthetic import SyntheticContext, start_synthetic_producers
from psi_agent.session.event_protocol import parse_event_envelope

HAITUN = Path(__file__).resolve().parents[3] / "agents" / "feishu"


@pytest.mark.anyio
async def test_load_feishu_member_added_def() -> None:
    defs = await load_channel_event_defs(HAITUN, "feishu")
    names = {d.name for d in defs}
    assert "feishu.chat.member_added" in names
    hit = next(d for d in defs if d.name == "feishu.chat.member_added")
    assert hit.platform_event == "im.chat.member.user.added_v1"
    assert hit.map_fn is not None
    assert hit.produce_fn is None


@pytest.mark.anyio
async def test_load_feishu_demo_tick_synthetic() -> None:
    defs = await load_channel_event_defs(HAITUN, "feishu")
    hit = next(d for d in defs if d.name == "feishu.synthetic.demo_tick")
    assert hit.kind == "synthetic"
    assert hit.produce_fn is not None
    assert hit.map_fn is None
    assert hit.platform_event == ""


def test_member_added_map_event() -> None:
    map_path = HAITUN / "channel_events" / "feishu" / "member_added" / "map.py"
    spec = importlib.util.spec_from_file_location("member_map", map_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    envs = mod.map_event(
        {
            "event": {
                "chat_id": "oc_1",
                "operator_id": {"open_id": "ou_op"},
                "users": [{"name": "A", "user_id": {"open_id": "ou_m"}}],
            }
        }
    )
    assert len(envs) == 1
    assert envs[0]["event"] == "feishu.chat.member_added"
    assert envs[0]["payload"]["member_open_id"] == "ou_m"
    assert envs[0]["routing"]["open_id"] == "ou_op"


@pytest.mark.anyio
async def test_synthetic_emit_posts_event(tmp_path: Path) -> None:
    """produce.py emit goes through SyntheticContext → resolve_core.post_event."""
    slug = tmp_path / "channel_events" / "feishu" / "once"
    await anyio.Path(str(slug)).mkdir(parents=True)
    await anyio.Path(str(slug / "EVENT.yaml")).write_text(
        "name: feishu.synthetic.once\nsource: feishu\nkind: synthetic\n",
        encoding="utf-8",
    )
    await anyio.Path(str(slug / "produce.py")).write_text(
        "async def produce(ctx):\n    await ctx.emit({'payload': {'n': 1}, 'routing': {'open_id': 'ou_x'}})\n",
        encoding="utf-8",
    )
    defs = await load_channel_event_defs(tmp_path, "feishu")
    assert len(defs) == 1 and defs[0].produce_fn is not None

    posted: list[dict[str, Any]] = []
    done = anyio.Event()

    class _FakeCore:
        async def post_event(self, envelope: dict[str, object]) -> dict[str, object]:
            posted.append(dict(envelope))
            done.set()
            return {"ok": True, "matched": [], "fired": []}

    async def resolve_core(open_id: str | None) -> ChannelCore:
        assert open_id == "ou_x"
        return cast(ChannelCore, _FakeCore())

    async with anyio.create_task_group() as tg:
        n = start_synthetic_producers(defs, resolve_core=resolve_core, task_group=tg)
        assert n == 1
        with anyio.fail_after(2):
            await done.wait()
        tg.cancel_scope.cancel()

    assert posted[0]["event"] == "feishu.synthetic.once"
    assert posted[0]["source"] == "feishu"
    assert posted[0]["payload"] == {"n": 1}


@pytest.mark.anyio
async def test_synthetic_context_emit_defaults() -> None:
    posted: list[dict[str, Any]] = []

    class _FakeCore:
        async def post_event(self, envelope: dict[str, object]) -> dict[str, object]:
            posted.append(dict(envelope))
            return {"ok": True}

    async def resolve_core(_open_id: str | None) -> ChannelCore:
        return cast(ChannelCore, _FakeCore())

    edef = ChannelEventDef(
        dir_name="x",
        name="feishu.synthetic.x",
        source="feishu",
        kind="synthetic",
        platform_event="",
        description="",
        map_fn=None,
        produce_fn=None,
        path=Path("."),
    )
    ctx = SyntheticContext(
        event_name=edef.name,
        source=edef.source,
        _resolve_core=resolve_core,
        _edef=edef,
    )
    await ctx.emit({"payload": {}})
    assert posted[0]["schema_version"] == 1
    assert posted[0]["raw_event"] == "synthetic:feishu.synthetic.x"


def _load_identity_map() -> Any:
    map_path = HAITUN / "channel_events" / "feishu" / "identity_changed" / "map.py"
    spec = importlib.util.spec_from_file_location("identity_map", map_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.anyio
async def test_load_haitun_synthetic_interfaces() -> None:
    defs = await load_channel_event_defs(HAITUN, "feishu")
    by_name = {d.name: d for d in defs}
    expected = {
        "haitun.task.completed",
        "haitun.task.overdue",
        "haitun.goal.progress",
        "haitun.handoff.needed",
        "haitun.handoff.activated",
        "haitun.blocker.raised",
        "haitun.deliverable.ready",
        "haitun.review.requested",
        "haitun.hr.handbook_ack_required",
        "haitun.hr.handbook_confirmed",
        "haitun.hr.stage_changed",
        "haitun.hr.resume_received",
        "haitun.finance.expense_submitted",
        "haitun.finance.attendance_review_needed",
        "haitun.finance.report_ready",
    }
    assert expected <= set(by_name)
    for name in expected:
        hit = by_name[name]
        assert hit.kind == "synthetic"
        assert hit.source == "haitun"
        assert hit.produce_fn is not None
        assert hit.map_fn is None


def test_parse_haitun_source_ok() -> None:
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "haitun",
            "event": "haitun.task.completed",
            "payload": {"task_id": "t1", "title": "x"},
        }
    )
    assert env.source == "haitun"
    assert env.event == "haitun.task.completed"


@pytest.mark.anyio
async def test_load_feishu_user_created_def() -> None:
    defs = await load_channel_event_defs(HAITUN, "feishu")
    hit = next(d for d in defs if d.name == "feishu.hr.user_created")
    assert hit.platform_event == "contact.user.created_v3"
    assert hit.map_fn is not None


def test_user_created_map_event() -> None:
    map_path = HAITUN / "channel_events" / "feishu" / "user_created" / "map.py"
    spec = importlib.util.spec_from_file_location("user_created_map", map_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    envs = mod.map_event(
        {
            "header": {"event_id": "e2"},
            "event": {"object": {"open_id": "ou_n", "user_id": "u_n", "name": "新人"}},
        }
    )
    assert len(envs) == 1
    assert envs[0]["event"] == "feishu.hr.user_created"
    assert envs[0]["payload"]["name"] == "新人"


@pytest.mark.anyio
async def test_load_feishu_identity_changed_def() -> None:
    defs = await load_channel_event_defs(HAITUN, "feishu")
    hit = next(d for d in defs if d.name == "feishu.hr.identity_changed")
    assert hit.kind == "platform_map"
    assert hit.platform_event == "contact.user.updated_v3"
    assert hit.map_fn is not None


def test_identity_changed_emits_on_job_title() -> None:
    mod = _load_identity_map()
    envs = mod.map_event(
        {
            "header": {"event_id": "evt_1", "event_type": "contact.user.updated_v3"},
            "event": {
                "object": {
                    "open_id": "ou_a",
                    "user_id": "u_a",
                    "name": "Alice",
                    "job_title": "Staff Engineer",
                },
                "old_object": {"job_title": "Engineer"},
            },
        }
    )
    assert len(envs) == 1
    assert envs[0]["event"] == "feishu.hr.identity_changed"
    assert "job_title" in envs[0]["payload"]["changed_fields"]
    assert envs[0]["payload"]["job_title"] == "Staff Engineer"
    assert envs[0]["idempotency_key"] == "feishu:identity_changed:evt_1"
    assert envs[0]["routing"]["open_id"] == "ou_a"


def test_identity_changed_skips_non_identity_fields() -> None:
    mod = _load_identity_map()
    envs = mod.map_event(
        {
            "event": {
                "object": {"open_id": "ou_a", "name": "Alice", "avatar": {"avatar_origin": "x"}},
                "old_object": {"avatar": {"avatar_origin": "y"}},
            }
        }
    )
    assert envs == []


@pytest.mark.anyio
async def test_identity_changed_declares_filters() -> None:
    """The mapper above returns [] by design, so it must declare ``filters: true``.

    Otherwise the live path warns "event dropped" on every avatar / phone edit
    in the org — routine noise that trains readers to ignore the diagnostic.
    """
    defs = await load_channel_event_defs(HAITUN, "feishu")
    by_name = {d.name: d for d in defs}
    assert by_name["feishu.hr.identity_changed"].filters is True
    # Mappers that only return [] on malformed payloads must NOT claim to filter.
    assert by_name["feishu.chat.member_added"].filters is False
    assert by_name["feishu.hr.user_created"].filters is False


@pytest.mark.anyio
async def test_filters_defaults_false_and_parses_from_yaml(tmp_path: Path) -> None:
    slug = tmp_path / "channel_events" / "feishu" / "filtered"
    await anyio.Path(str(slug)).mkdir(parents=True)
    await anyio.Path(str(slug / "map.py")).write_text("def map_event(raw):\n    return []\n", encoding="utf-8")
    header = "name: feishu.test.filtered\nsource: feishu\nkind: platform_map\nplatform_event: im.test.v1\n"
    await anyio.Path(str(slug / "EVENT.yaml")).write_text(header, encoding="utf-8")
    defs = await load_channel_event_defs(tmp_path, "feishu")
    assert defs[0].filters is False
    await anyio.Path(str(slug / "EVENT.yaml")).write_text(header + "filters: true\n", encoding="utf-8")
    defs = await load_channel_event_defs(tmp_path, "feishu")
    assert defs[0].filters is True


def test_identity_changed_status_resigned() -> None:
    mod = _load_identity_map()
    envs = mod.map_event(
        {
            "event": {
                "object": {
                    "open_id": "ou_b",
                    "status": {"is_resigned": True, "is_activated": False},
                },
                "old_object": {
                    "status": {"is_resigned": False, "is_activated": True},
                },
            }
        }
    )
    assert len(envs) == 1
    fields = envs[0]["payload"]["changed_fields"]
    assert "status" in fields
    assert "status.is_resigned" in fields
