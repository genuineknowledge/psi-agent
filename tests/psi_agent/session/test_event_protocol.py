"""Unit tests for Session event protocol + trigger dispatch."""

from __future__ import annotations

import socket as _socket
import textwrap
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent.session.agent import SessionAgent, current_tool_ai_socket
from psi_agent.session.ai_client import AiClient
from psi_agent.session.event_protocol import (
    EVENT_FEISHU_CHAT_MEMBER_ADDED,
    EventProtocolError,
    filter_matches,
    parse_event_envelope,
)
from psi_agent.session.schedule_registry import FIRE_TOOL
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry
from psi_agent.session.trigger_registry import TriggerRegistry


def test_parse_member_added_ok() -> None:
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_1", "member_open_id": "ou_2", "member_name": "A"},
            "idempotency_key": "k1",
        }
    )
    assert env.source == "feishu"
    assert env.event == EVENT_FEISHU_CHAT_MEMBER_ADDED
    assert env.payload["chat_id"] == "oc_1"
    assert env.idempotency_key == "k1"


def test_parse_unknown_event_accepted_without_session_catalog() -> None:
    """Session thin gate: business event names are not Session-catalog gated."""
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": "feishu.custom.from_channel_events",
            "payload": {"chat_id": "oc_1"},
        }
    )
    assert env.event == "feishu.custom.from_channel_events"


def test_parse_payload_must_be_object() -> None:
    with pytest.raises(EventProtocolError, match="payload must be a JSON object"):
        parse_event_envelope(
            {
                "schema_version": 1,
                "source": "feishu",
                "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
                "payload": "oc_1",
            }
        )


def test_parse_empty_payload_object_ok() -> None:
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {},
        }
    )
    assert env.payload == {}


def test_filter_matches_exact_subset() -> None:
    payload = {"chat_id": "oc_1", "member_open_id": "ou_2"}
    assert filter_matches(payload, {"chat_id": "oc_1"})
    assert not filter_matches(payload, {"chat_id": "oc_other"})
    assert not filter_matches(payload, {"missing": "x"})


@pytest.mark.anyio
async def test_load_and_match_trigger(tmp_path: Path) -> None:
    trig_dir = tmp_path / "triggers" / "join-ping"
    await anyio.Path(trig_dir).mkdir(parents=True)
    await anyio.Path(trig_dir / "TRIGGER.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: join-ping
            source: feishu
            event: {EVENT_FEISHU_CHAT_MEMBER_ADDED}
            filter:
              chat_id: oc_target
            fire: tool
            tool: echo_tool
            tool_args:
              text: hi
            visibility: silent
            ---
            """
        ),
        encoding="utf-8",
    )
    registry = await TriggerRegistry.load(tmp_path / "triggers")
    assert len(registry.triggers) == 1
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_target", "member_open_id": "ou_x"},
        }
    )
    hits = registry.match(env)
    assert [t.name for t in hits] == ["join-ping"]
    miss = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_other", "member_open_id": "ou_x"},
        }
    )
    assert registry.match(miss) == []


@pytest.mark.anyio
async def test_match_falls_back_to_raw_event(tmp_path: Path) -> None:
    trig_dir = tmp_path / "triggers" / "raw-fallback"
    await anyio.Path(trig_dir).mkdir(parents=True)
    await anyio.Path(trig_dir / "TRIGGER.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: raw-fallback
            source: feishu
            event: {EVENT_FEISHU_CHAT_MEMBER_ADDED}
            filter:
              chat_id: oc_norm_only
            raw_event: im.chat.member.user.added_v1
            raw_filter:
              chat_id: oc_raw
            fire: prompt
            visibility: silent
            ---
            """
        ),
        encoding="utf-8",
    )
    registry = await TriggerRegistry.load(tmp_path / "triggers")
    # Normalized filter misses; raw_event + raw_filter hits.
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_other", "member_open_id": "ou_x"},
            "raw_event": "im.chat.member.user.added_v1",
            "raw_payload": {"chat_id": "oc_raw"},
        }
    )
    hits = registry.match(env)
    assert [t.name for t in hits] == ["raw-fallback"]


@pytest.mark.anyio
async def test_dispatch_fire_tool(tmp_path: Path) -> None:
    called: dict[str, str] = {}
    ai_socket = "http://nonexistent/v1"

    async def echo_tool(text: str = "") -> str:
        called["text"] = text
        called["ai_socket"] = current_tool_ai_socket() or ""
        return f"ok:{text}"

    tools = ToolRegistry()
    tools._files["echo.py"] = FileEntry(
        file_hash="x",
        tools={"echo_tool": ToolFunction.from_callable(echo_tool)},
        funcs={"echo_tool": echo_tool},
        fresh=True,
    )

    trig_dir = tmp_path / "triggers" / "t1"
    await anyio.Path(trig_dir).mkdir(parents=True)
    await anyio.Path(trig_dir / "TRIGGER.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: t1
            event: {EVENT_FEISHU_CHAT_MEMBER_ADDED}
            filter:
              chat_id: oc_1
            fire: tool
            tool: echo_tool
            tool_args:
              text: joined
            visibility: silent
            ---
            """
        ),
        encoding="utf-8",
    )
    registry = await TriggerRegistry.load(tmp_path / "triggers")
    agent = SessionAgent(
        ai_client=AiClient(ai_socket),
        tool_registry=tools,
        trigger_registry=registry,
        workspace_path=tmp_path,
    )
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_1", "member_open_id": "ou_1"},
            "idempotency_key": "once-1",
        }
    )
    async with agent._lock:
        fired = await registry.dispatch(env, agent)
    assert fired == ["t1"]
    assert called["text"] == "joined"
    assert called["ai_socket"] == ai_socket
    assert current_tool_ai_socket() is None
    # duplicate idempotency
    async with agent._lock:
        fired2 = await registry.dispatch(env, agent)
    assert fired2 == []


@pytest.mark.anyio
async def test_post_events_http(tmp_path: Path) -> None:
    called: list[str] = []

    async def ping(text: str = "") -> str:
        called.append(text)
        return "ok"

    tools = ToolRegistry()
    tools._files["ping.py"] = FileEntry(
        file_hash="y",
        tools={"ping": ToolFunction.from_callable(ping)},
        funcs={"ping": ping},
        fresh=True,
    )
    trig_dir = tmp_path / "triggers" / "http-t"
    await anyio.Path(trig_dir).mkdir(parents=True)
    await anyio.Path(trig_dir / "TRIGGER.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: http-t
            event: {EVENT_FEISHU_CHAT_MEMBER_ADDED}
            filter:
              chat_id: oc_http
            fire: {FIRE_TOOL}
            tool: ping
            tool_args:
              text: from-http
            visibility: silent
            ---
            """
        ),
        encoding="utf-8",
    )
    registry = await TriggerRegistry.load(tmp_path / "triggers")
    agent = SessionAgent(
        ai_client=AiClient("http://nonexistent/v1"),
        tool_registry=tools,
        trigger_registry=registry,
        workspace_path=tmp_path,
    )
    app = web.Application()
    app.router.add_post("/events", agent.handle_event)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    base = f"http://127.0.0.1:{port}"
    try:
        await anyio.sleep(0.05)
        timeout = ClientTimeout(total=5)
        body = {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_http", "member_open_id": "ou_h"},
        }
        async with (
            ClientSession(timeout=timeout) as s,
            s.post(f"{base}/events", json=body) as resp,
        ):
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["matched"] == 1
            assert data["fired"] == ["http-t"]
        assert called == ["from-http"]

        async with (
            ClientSession(timeout=timeout) as s,
            s.post(
                f"{base}/events",
                json={**body, "event": "feishu.not.listed"},
            ) as resp,
        ):
            # Thin Session gate: unknown business names are accepted (matched=0).
            assert resp.status == 200
            data2 = await resp.json()
            assert data2["ok"] is True
            assert data2["matched"] == 0
            assert data2["fired"] == []
    finally:
        await runner.cleanup()
        sock.close()
