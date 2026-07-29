from __future__ import annotations

import os

import anyio
import pytest

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._feishu_manager import FeishuManager, _sanitize_open_id
from psi_agent.gateway._session_manager import SessionManager


async def _make_managers(tg: object) -> tuple[AIManager, SessionManager]:
    am = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)
    await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")
    return am, sm


async def _drain(sm: SessionManager, am: AIManager) -> None:
    """删掉所有 spawn 出来的 Session/AI 常驻任务, 使 tg.__aexit__ 能干净退出。

    与 test_manager.py 一致——显式 delete 而非 cancel task-group scope。
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
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        socket1, sid1 = await fm.route("ou_alice")
        assert sid1 == "feishu-ou_alice"
        assert sm.has(sid1)

        # 二次幂等: 同 open_id 拿回同 socket/session_id, 不再新建。
        socket2, sid2 = await fm.route("ou_alice")
        assert (socket2, sid2) == (socket1, sid1)
        assert len(await sm.list_all()) == 1

        # 不同 open_id → 独立 session。
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
        am, sm = await _make_managers(tg)
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
        am, sm = await _make_managers(tg)
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
        am, sm = await _make_managers(tg)
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
        am, sm = await _make_managers(tg)
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
        am, sm = await _make_managers(tg)
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
async def test_route_group_chat_keys_on_chat_id(tmp_path: str) -> None:
    """群聊按 chat_id 建 session, 同群不同发送者共用一个 session。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        socket1, sid1 = await fm.route("ou_alice", chat_id="oc_team", chat_type="group")
        assert sid1 == "feishu-chat-oc_team"

        # 同群另一个人 → 同一 session, 不新建。
        socket2, sid2 = await fm.route("ou_bob", chat_id="oc_team", chat_type="group")
        assert (socket2, sid2) == (socket1, sid1)
        assert len(await sm.list_all()) == 1

        # 另一个群 → 独立 session。
        _, sid_other = await fm.route("ou_alice", chat_id="oc_other", chat_type="group")
        assert sid_other == "feishu-chat-oc_other"
        assert len(await sm.list_all()) == 2
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_p2p_still_keys_on_open_id(tmp_path: str) -> None:
    """私聊 (含带 chat_id 的 p2p) 仍按发送者 open_id 建 session。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        _, sid = await fm.route("ou_alice", chat_id="oc_dm", chat_type="p2p")
        assert sid == "feishu-ou_alice"

        # 同一人的私聊与其所在群互不干扰。
        _, sid_group = await fm.route("ou_alice", chat_id="oc_team", chat_type="group")
        assert sid_group == "feishu-chat-oc_team"
        assert len(await sm.list_all()) == 2
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_group_creates_per_chat_workspace(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        await fm.route("ou_alice", chat_id="oc_team", chat_type="group")
        expected = os.path.join(str(tmp_path), "chat-oc_team")
        assert await anyio.Path(expected).is_dir()
        assert sm.get_workspace("feishu-chat-oc_team") == expected
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_group_without_chat_id_falls_back_to_open_id(tmp_path: str) -> None:
    """chat_type=group 但 chat_id 缺失 → 退回按 open_id, 不炸也不建空名 session。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        _, sid = await fm.route("ou_alice", chat_id="", chat_type="group")
        assert sid == "feishu-ou_alice"
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_group_empty_open_id_allowed(tmp_path: str) -> None:
    """群聊路由键是 chat_id, 故 open_id 缺失也应能路由 (不再强制非空)。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        _, sid = await fm.route("", chat_id="oc_team", chat_type="group")
        assert sid == "feishu-chat-oc_team"
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_topic_chat_keys_on_chat_id(tmp_path: str) -> None:
    """话题群 (chat_type=topic) 与 group 同样按 chat_id 建 session。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        _, sid = await fm.route("ou_alice", chat_id="oc_topic", chat_type="topic")
        assert sid == "feishu-chat-oc_topic"
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_list_routes_reports_group_key(tmp_path: str) -> None:
    """群 session 在路由表里以 chat_id 为键, open_id 留空 (群不属于某个人)。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))
        await fm.route("ou_alice")
        await fm.route("ou_bob", chat_id="oc_team", chat_type="group")

        entries = {(r.open_id, r.chat_id, r.session_id) for r in fm.list_routes()}
        assert entries == {
            ("ou_alice", "", "feishu-ou_alice"),
            ("", "oc_team", "feishu-chat-oc_team"),
        }
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_adopts_existing_group_session(tmp_path: str) -> None:
    """重启后群 session 已被 state 恢复 → adopt 不重建。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        info = await sm.create(ai_id="ai1", id="feishu-chat-oc_team", workspace=str(tmp_path))

        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))
        socket, sid = await fm.route("ou_alice", chat_id="oc_team", chat_type="group")
        assert (socket, sid) == (info.channel_socket, "feishu-chat-oc_team")
        assert len(await sm.list_all()) == 1
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_group_and_lookalike_open_id_do_not_collide(tmp_path: str) -> None:
    """私聊 open_id 恰好长得像群前缀 (``chat-oc_team``) 时, 不能撞进群 ``oc_team`` 的 session。

    群 session_id 是 ``feishu-chat-<chat_id>``, 若私聊直接拼 ``feishu-<open_id>`` 则二者会
    撞成同名 —— 两个陌生人共享上下文, 是隐私事故, 故私聊侧须把 ``-`` 转义掉。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))

        _, group_sid = await fm.route("ou_alice", chat_id="oc_team", chat_type="group")
        _, dm_sid = await fm.route("chat-oc_team", chat_id="oc_dm", chat_type="p2p")

        assert group_sid != dm_sid
        assert sm.get_workspace(group_sid) != sm.get_workspace(dm_sid)
        assert len(await sm.list_all()) == 2
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_route_adopts_existing_session(tmp_path: str) -> None:
    """模拟重启: session 已存在 (被 state 恢复), route 直接 adopt 不重建。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        # 预先手建一个同名 session (等价于 state 恢复后的场景)。
        info = await sm.create(ai_id="ai1", id="feishu-ou_alice", workspace=str(tmp_path))

        fm = FeishuManager(_sm=sm, _ai_id="ai1", _workspace_root=str(tmp_path))
        socket, sid = await fm.route("ou_alice")
        assert sid == "feishu-ou_alice"
        assert socket == info.channel_socket
        assert len(await sm.list_all()) == 1  # 未重建
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)
