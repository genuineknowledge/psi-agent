from __future__ import annotations

import os

import anyio
import pytest

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._feishu_manager import FeishuManager, _sanitize_open_id
from psi_agent.gateway._session_manager import SessionManager


async def _make_managers(tg: object, tmp_path: str = "") -> tuple[AIManager, SessionManager]:
    am = AIManager(_prefix="gw-test", _tg=tg)
    # Keep agent package empty/light for unit tests (avoid loading examples/haitun).
    agent = tmp_path or os.getcwd()
    sm = SessionManager(
        _aim=am,
        _prefix="gw-test",
        _tg=tg,
        _default_agent=agent,
        _default_workspace=agent,
        _app_data_root=os.path.join(agent, ".appdata") if tmp_path else "",
    )
    await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")
    return am, sm


async def _drain(sm: SessionManager, am: AIManager) -> None:
    """Delete spawned Session/AI tasks so tg.__aexit__ can exit cleanly.

    Same pattern as test_manager.py — explicit delete rather than cancelling the task group.
    """
    for info in await sm.list_all():
        await sm.delete(info.id)
    for info in await am.list_all():
        await am.delete(info.id)


def test_sanitize_open_id() -> None:
    assert _sanitize_open_id("ou_abc123") == "ou_abc123"
    assert _sanitize_open_id("a/b c:d") == "a_b_c_d"


@pytest.mark.anyio
async def test_route_spawns_and_is_idempotent(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        socket1, sid1 = await fm.route("ou_alice")
        assert sid1 == "feishu-ou_alice"
        assert sm.has(sid1)

        # Idempotent: same open_id returns same socket/session_id.
        socket2, sid2 = await fm.route("ou_alice")
        assert (socket2, sid2) == (socket1, sid1)
        assert len(await sm.list_all()) == 1

        # Different open_id → independent session.
        _, sid_bob = await fm.route("ou_bob")
        assert sid_bob == "feishu-ou_bob"
        assert len(await sm.list_all()) == 2
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_creates_per_user_workspace(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        await fm.route("ou_alice")
        expected = os.path.join(str(tmp_path), "ou_alice")
        assert await anyio.Path(expected).is_dir()
        assert sm.get_workspace("feishu-ou_alice") == expected
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_request_ai_id_and_workspace_override(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai2")
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        custom_ws = os.path.join(str(tmp_path), "custom")
        _, sid = await fm.route("ou_alice", ai_id="ai2", workspace=custom_ws)
        assert sm.get_workspace(sid) == custom_ws
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_no_ai_id_raises(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        fm = FeishuManager(_sm=sm, _ai_id="", _workspace_root=str(tmp_path))
        with pytest.raises(ValueError, match="no ai_id"):
            await fm.route("ou_alice")
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_empty_open_id_raises(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))
        with pytest.raises(ValueError, match="open_id"):
            await fm.route("")
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_list_routes(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))
        await fm.route("ou_alice")
        await fm.route("ou_bob")

        routes = fm.list_routes()
        pairs = {(r.open_id, r.session_id) for r in routes}
        assert pairs == {("ou_alice", "feishu-ou_alice"), ("ou_bob", "feishu-ou_bob")}
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_adopts_existing_session(tmp_path: str) -> None:
    """Restart simulation: session already exists (state restored); route adopts it."""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg, str(tmp_path))
        info = await sm.create(
            ai_id="ai1",
            id="feishu-ou_alice",
            workspace=str(tmp_path),
            agent=str(tmp_path),
        )

        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))
        socket, sid = await fm.route("ou_alice")
        assert sid == "feishu-ou_alice"
        assert socket == info.channel_socket
        assert len(await sm.list_all()) == 1
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)
