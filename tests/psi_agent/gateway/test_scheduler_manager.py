from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._scheduler_manager import SchedulerManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.session.schedule_registry import ACTIVATE_ALL


async def _make_managers(tg: object) -> tuple[AIManager, SessionManager]:
    am = AIManager(_prefix="sched-test", _tg=tg)
    sm = SessionManager(_aim=am, _prefix="sched-test", _tg=tg)
    await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")
    return am, sm


async def _drain(sm: SessionManager, am: AIManager) -> None:
    for info in await sm.list_all(include_scheduler=True):
        await sm.delete(info.id)
    for info in await am.list_all():
        await am.delete(info.id)


async def _write_schedule(workspace: Path, name: str = "daily") -> None:
    task_dir = anyio.Path(workspace) / "schedules" / name
    await task_dir.mkdir(parents=True, exist_ok=True)
    await (task_dir / "TASK.md").write_text(f'---\nname: {name}\ncron: "0 12 * * *"\n---\nTask body', encoding="utf-8")


# ── id 派生 ───────────────────────────────────────────────────────────────────


async def _sid(workspace: Path | str) -> str:
    key = await SchedulerManager._workspace_key(str(workspace))
    return SchedulerManager._session_id_from_key(key)


@pytest.mark.anyio
async def test_scheduler_session_id_is_deterministic(tmp_path: Path) -> None:
    assert await _sid(tmp_path) == await _sid(tmp_path)
    assert (await _sid(tmp_path)).startswith("scheduler-")


@pytest.mark.anyio
async def test_scheduler_session_id_differs_per_workspace(tmp_path: Path) -> None:
    a = tmp_path / "ws-a"
    b = tmp_path / "ws-b"
    a.mkdir()
    b.mkdir()
    assert await _sid(a) != await _sid(b)


@pytest.mark.anyio
async def test_workspace_key_normalises_path_variants(tmp_path: Path) -> None:
    """大小写 / 斜杠差异不该产出两个调度 Session。"""
    key = SchedulerManager._workspace_key
    plain = str(tmp_path)
    assert await key(plain) == await key(plain.replace("\\", "/"))
    assert await key(plain) == await key(str(tmp_path / "."))


# ── ensure ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_ensure_spawns_one_scheduler_session(tmp_path: Path) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await _write_schedule(tmp_path)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        sid = await schedm.ensure(str(tmp_path))
        assert sid == await _sid(tmp_path)
        assert sm.has(sid)
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_is_idempotent(tmp_path: Path) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await _write_schedule(tmp_path)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        first = await schedm.ensure(str(tmp_path))
        second = await schedm.ensure(str(tmp_path))
        assert first == second
        assert len(await sm.list_all(include_scheduler=True)) == 1
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_skips_workspace_without_schedules(tmp_path: Path) -> None:
    """按需 spawn: 没有 schedules 就不开 Session (免得 N 个飞书用户各挂一个空的)。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        assert await sm.list_all(include_scheduler=True) == []
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_skips_empty_schedules_dir(tmp_path: Path) -> None:
    """schedules/ 存在但没有任何 TASK.md 也算没有定时任务。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await anyio.Path(tmp_path / "schedules" / "stub").mkdir(parents=True)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        assert await sm.list_all(include_scheduler=True) == []
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_picks_up_schedules_created_later(tmp_path: Path) -> None:
    """显式 ensure 路径: schedules 已出现时直接拉起 (watch_loop 之外, 如重启恢复 / 建会话路由)。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        await _write_schedule(tmp_path)
        sid = await schedm.ensure(str(tmp_path))
        assert sid != ""
        assert sm.has(sid)
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_without_ai_id_does_not_spawn(tmp_path: Path) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await _write_schedule(tmp_path)
        schedm = SchedulerManager(_sm=sm)

        assert await schedm.ensure(str(tmp_path)) == ""
        assert await sm.list_all(include_scheduler=True) == []
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


# ── watch_loop (首个定时任务自动拉起) ────────────────────────────────────────


@pytest.mark.anyio
async def test_ensure_queues_pending_when_no_schedules(tmp_path: Path) -> None:
    """ensure 跳过时把 workspace 记入 _pending, 由 watch_loop 稍后重查。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        key = await SchedulerManager._workspace_key(str(tmp_path))
        assert key in schedm._pending
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_without_ai_id_not_queued(tmp_path: Path) -> None:
    """没有可用 AI 时不入队 —— 反正 spawn 不起来, 不必每 30s 空查一次。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm)

        assert await schedm.ensure(str(tmp_path)) == ""
        assert schedm._pending == {}
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_watch_loop_spawns_scheduler_after_first_schedule(tmp_path: Path) -> None:
    """用户建第一个定时任务后由 watch_loop 自动拉起 —— 不再等下一次 ensure。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        assert await sm.list_all(include_scheduler=True) == []

        await _write_schedule(tmp_path)
        await schedm._sweep_once()

        sid = await _sid(tmp_path)
        assert sm.has(sid)
        assert sid not in schedm._pending
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_watch_loop_keeps_pending_until_schedules_appear(tmp_path: Path) -> None:
    """schedules 未出现时 sweep 不 spawn、条目保留; 出现后才拉起并出队。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        key = await SchedulerManager._workspace_key(str(tmp_path))

        await schedm._sweep_once()
        assert await sm.list_all(include_scheduler=True) == []
        assert key in schedm._pending

        await _write_schedule(tmp_path)
        await schedm._sweep_once()
        assert sm.has(await _sid(tmp_path))
        assert key not in schedm._pending
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_watch_loop_drops_pending_when_already_spawned(tmp_path: Path) -> None:
    """并发 ensure 已拉起调度 Session 时, sweep 只出队不重复 spawn。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        assert await schedm.ensure(str(tmp_path)) == ""
        await _write_schedule(tmp_path)

        # 另一条路径 (例如下一次 /feishu/route) 抢先 ensure 成功。
        sid = await schedm.ensure(str(tmp_path))
        assert sid != ""
        await schedm._sweep_once()

        assert len(await sm.list_all(include_scheduler=True)) == 1
        assert await schedm.ensure(str(tmp_path)) == sid
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_ensure_empty_workspace_returns_blank(tmp_path: Path) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")
        assert await schedm.ensure("   ") == ""
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_different_workspaces_get_separate_schedulers(tmp_path: Path) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        a = tmp_path / "user-a"
        b = tmp_path / "user-b"
        await _write_schedule(a)
        await _write_schedule(b)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        sid_a = await schedm.ensure(str(a))
        sid_b = await schedm.ensure(str(b))
        assert sid_a != sid_b
        assert len(await sm.list_all(include_scheduler=True)) == 2
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_path_variants_reuse_same_scheduler(tmp_path: Path) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await _write_schedule(tmp_path)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        first = await schedm.ensure(str(tmp_path))
        second = await schedm.ensure(str(tmp_path).replace("\\", "/"))
        assert first == second
        assert len(await sm.list_all(include_scheduler=True)) == 1
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


# ── 对 SPA / state 隐藏 ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_scheduler_session_hidden_from_list_all(tmp_path: Path) -> None:
    """调度 Session 不出现在用户会话列表 (也因此不进 state/latest.json)。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await _write_schedule(tmp_path)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")

        await sm.create(ai_id="ai1", id="user-1", workspace=str(tmp_path))
        await schedm.ensure(str(tmp_path))

        visible = await sm.list_all()
        assert [info.id for info in visible] == ["user-1"]
        assert len(await sm.list_all(include_scheduler=True)) == 2
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_scheduler_session_activates_all_schedules(tmp_path: Path) -> None:
    """Gateway 把整个 workspace 交给调度 Session —— 名单是通配 ``*``, 而 ``scheduler``
    只是由它派生出来的展示/过滤用属性。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        await _write_schedule(tmp_path)
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1")
        sid = await schedm.ensure(str(tmp_path))

        infos = {i.id: i for i in await sm.list_all(include_scheduler=True)}
        assert infos[sid].active_schedules == (ACTIVATE_ALL,)
        assert infos[sid].scheduler is True
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_user_session_activates_no_schedules(tmp_path: Path) -> None:
    """用户会话默认一条都不激活 —— 一条 schedule 必须恰好被一个 Session 触发。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert info.active_schedules == ()
        assert info.scheduler is False
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_named_subset_session_is_not_hidden(tmp_path: Path) -> None:
    """只激活部分条目的普通会话仍是用户会话 —— 不该从 SPA / state 里消失。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        info = await sm.create(ai_id="ai1", workspace=str(tmp_path), active_schedules=("daily",))
        assert info.active_schedules == ("daily",)
        assert info.scheduler is False
        assert [i.id for i in await sm.list_all()] == [info.id]
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_blacklist_is_recorded_and_keeps_scheduler_flag(tmp_path: Path) -> None:
    """`("*",)` + 黑名单: 仍是该 workspace 的调度 Session, 只是让出几条。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        info = await sm.create(
            ai_id="ai1",
            workspace=str(tmp_path),
            active_schedules=(ACTIVATE_ALL,),
            deactive_schedules=("daily",),
        )
        assert info.deactive_schedules == ("daily",)
        assert info.scheduler is True
        # 全量激活的调度 Session 对 SPA / state 隐藏, 让出几条也不改变这一点。
        assert await sm.list_all() == []
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


# ── 公司级种子任务 seed ───────────────────────────────────────────────────────


async def _write_agent_schedule(agent_pkg: Path, name: str, body: str = "seed body") -> None:
    task_dir = anyio.Path(agent_pkg) / "schedules" / name
    await task_dir.mkdir(parents=True, exist_ok=True)
    await (task_dir / "TASK.md").write_text(
        f'---\nname: {name}\ncron: "0 12 * * *"\n---\n{body}', encoding="utf-8"
    )


def _sched_dir(workspace: Path) -> anyio.Path:
    return anyio.Path(workspace) / "schedules"


@pytest.mark.anyio
async def test_seed_off_by_default(tmp_path: Path) -> None:
    """未配置 seed workspace 时, agent 包的种子任务一个都不落盘。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        agent_pkg = tmp_path / "agent-pkg"
        await _write_agent_schedule(agent_pkg, "todo-remind")
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1", seed_agent=str(agent_pkg))

        assert await schedm.ensure(str(tmp_path)) == ""  # 无 schedules, 不 spawn
        assert not await _sched_dir(tmp_path).is_dir()
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_seed_copies_missing_tasks_to_seed_workspace(tmp_path: Path) -> None:
    """配置了 seed workspace 后, ensure 把 agent 包缺失的任务幂等落盘并正常拉起。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        seed_ws = tmp_path / "seed-ws"
        agent_pkg = tmp_path / "agent-pkg"
        await _write_agent_schedule(agent_pkg, "todo-remind")
        await _write_agent_schedule(agent_pkg, "mentor-check-remind")
        schedm = SchedulerManager(
            _sm=sm, _ai_id="ai1", seed_workspace=str(seed_ws), seed_agent=str(agent_pkg)
        )

        sid = await schedm.ensure(str(seed_ws))
        assert sid  # seed 落盘后有 schedules, 调度 Session 照常拉起
        assert await (_sched_dir(seed_ws) / "todo-remind" / "TASK.md").exists()
        assert await (_sched_dir(seed_ws) / "mentor-check-remind" / "TASK.md").exists()
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_seed_never_overwrites_existing(tmp_path: Path) -> None:
    """workspace 已有同名任务时 seed 不覆盖 —— 本地改过的口径不被 agent 包顶掉。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        seed_ws = tmp_path / "seed-ws"
        agent_pkg = tmp_path / "agent-pkg"
        await _write_agent_schedule(agent_pkg, "todo-remind", body="agent 包口径")
        await _write_schedule(seed_ws, "todo-remind")  # workspace 已有一份
        local_task = _sched_dir(seed_ws) / "todo-remind" / "TASK.md"
        local_before = await local_task.read_text(encoding="utf-8")
        schedm = SchedulerManager(
            _sm=sm, _ai_id="ai1", seed_workspace=str(seed_ws), seed_agent=str(agent_pkg)
        )

        await schedm.ensure(str(seed_ws))
        assert await local_task.read_text(encoding="utf-8") == local_before
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_seed_skips_other_workspaces(tmp_path: Path) -> None:
    """seed 只落配置的那一个 workspace —— 其他用户 workspace 不获得公司级任务。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        seed_ws = tmp_path / "seed-ws"
        other_ws = tmp_path / "other-ws"
        other_ws.mkdir()
        agent_pkg = tmp_path / "agent-pkg"
        await _write_agent_schedule(agent_pkg, "todo-remind")
        schedm = SchedulerManager(
            _sm=sm, _ai_id="ai1", seed_workspace=str(seed_ws), seed_agent=str(agent_pkg)
        )

        assert await schedm.ensure(str(other_ws)) == ""  # 无 schedules, 不 spawn
        assert not await _sched_dir(other_ws).is_dir()
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_seed_requires_agent_package(tmp_path: Path) -> None:
    """没有 agent 包路径 (seed_agent 空) 时静默不 seed。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        seed_ws = tmp_path / "seed-ws"
        schedm = SchedulerManager(_sm=sm, _ai_id="ai1", seed_workspace=str(seed_ws))

        assert await schedm.ensure(str(seed_ws)) == ""
        assert not await _sched_dir(seed_ws).is_dir()
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_seed_ignores_entries_without_task_md(tmp_path: Path) -> None:
    """agent 包 schedules/ 下没有 TASK.md 的目录不是任务, 不拷。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        seed_ws = tmp_path / "seed-ws"
        agent_pkg = tmp_path / "agent-pkg"
        stray = anyio.Path(agent_pkg) / "schedules" / "stray-dir"
        await stray.mkdir(parents=True, exist_ok=True)
        await (stray / "notes.txt").write_text("not a task", encoding="utf-8")
        await _write_agent_schedule(agent_pkg, "todo-remind")
        schedm = SchedulerManager(
            _sm=sm, _ai_id="ai1", seed_workspace=str(seed_ws), seed_agent=str(agent_pkg)
        )

        await schedm.ensure(str(seed_ws))
        assert await (_sched_dir(seed_ws) / "todo-remind" / "TASK.md").exists()
        assert not await (_sched_dir(seed_ws) / "stray-dir").exists()
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_sweep_seeds_cold_seed_workspace(tmp_path: Path) -> None:
    """冷启动: seed workspace 从没被任何用户消息 ensure 过, watch_loop 也把它落盘。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am, sm = await _make_managers(tg)
        seed_ws = tmp_path / "seed-ws"
        agent_pkg = tmp_path / "agent-pkg"
        await _write_agent_schedule(agent_pkg, "todo-remind")
        schedm = SchedulerManager(
            _sm=sm, _ai_id="ai1", seed_workspace=str(seed_ws), seed_agent=str(agent_pkg)
        )

        await schedm._sweep_once()
        assert await (_sched_dir(seed_ws) / "todo-remind" / "TASK.md").exists()
    finally:
        await _drain(sm, am)
        await tg.__aexit__(None, None, None)
