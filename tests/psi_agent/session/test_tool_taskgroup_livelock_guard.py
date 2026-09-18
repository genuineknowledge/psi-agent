"""工具 task group 的取消活锁: 从静默忙转变成一条带 session_id 的 ERROR。

2026-09-16 生产上一个飞书用户的 session 被锁死约 3 小时, 用户侧表现为「完全没反应」,
而且飞书机器人一个 open_id 只有一个长命 session, 用户自己无法恢复。实测链条::

    _feishu_auth_watch.py:157  _run                   ← watcher 自己的 asyncio task
      → _feishu/auth.py:715    _notify_auth_outcome
        → live_agent.py:128    resume_session_turn    ← 持有 turn_lock
          → session/agent.py   工具 task group __aexit__  ← 卡死在这里

一条执行流取消了它**自己所在**的任务, 而 ``CancelledError`` 被 ``contextlib.suppress``
吞掉, 任务于是处于「取消已提出却仍然活着」的状态; 外层 anyio task group 的 ``__aexit__``
便通过 ``loop.call_soon`` 无限重试交付取消 —— 事件循环 100% 忙转, ``turn_lock`` 永不释放。

**这次事故整个过程零日志、零指标**, 诊断只能靠从宿主机 root 拿 py-spy 打
``_deliver_cancellation`` 帧。本文件测的就是这个可观测缺口被补上了。

判据为什么长这样 (每条都踩过坑):

- 活锁判据必须**带 task group 那一层**。活锁只在取消交付撞上 task group 退出等待时
  出现; 直接 ``await`` 一个自指取消 (少了 task group) 实测**不会**活锁 —— 子任务照常
  返回、task group 正常退出。少了这层永远测不出来。
- 日志断言自己挂 ``logger.add`` sink 并同时记 level: 本仓库用 loguru, 它默认不走
  stdlib logging, ``caplog`` 一条都收不到 —— 阴性用例会假绿。
- 不能只写正例。守卫要是退化成「永不取消」也能让正例全绿, 所以有一条
  ``test_a_normal_cross_task_cancellation_still_takes_effect`` 反例盯着。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket as _s
import threading
from pathlib import Path
from typing import Any

import anyio
import pytest
from aiohttp import web
from loguru import logger

from psi_agent.session import agent as agent_module
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.protocol import AgentError
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry

# 判据自己的超时上限。远大于探测阈值 (用例里会把阈值调到亚秒级), 又远小于 pytest
# 的外层超时 —— 活锁时 ``fail_after`` **打不出来** (忙转不让事件循环走到超时回调),
# pytest 会被外层超时杀掉、退出码 143, 那本身就是活锁的判据。
_FAIL_AFTER_SECONDS = 20.0


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _tool_call_chunk(tool_name: str, arguments: str = "{}") -> bytes:
    return _sse(
        {
            "id": "mock",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": tool_name, "arguments": arguments},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )


def _stop_chunk(content: str = "done") -> bytes:
    return _sse(
        {
            "id": "mock",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
        }
    )


class _MockAI:
    """一个只会「先要一次工具、再收尾」的假 AI。"""

    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None

    async def start(self, tool_name: str) -> str:
        count = 0

        async def handler(request: web.Request) -> web.StreamResponse:
            nonlocal count
            count += 1
            resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(_tool_call_chunk(tool_name) if count == 1 else _stop_chunk())
            await resp.write(b"data: [DONE]\n\n")
            return resp

        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        await web.SockSite(self._runner, sock).start()
        return f"http://127.0.0.1:{sock.getsockname()[1]}"

    async def cleanup(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


async def _run_turn(tmp_path: Path, *, session_id: str, tool_name: str, func: Any) -> list[Any]:
    """跑一个「模型要了一次工具」的完整回合, 返回 chunks。"""
    server = _MockAI()
    ai_socket = await server.start(tool_name)
    tool = ToolFunction.from_callable(func)
    try:
        agent = SessionAgent(
            ai_client=AiClient(ai_socket),
            tool_registry=ToolRegistry(files={"t": FileEntry("", {tool_name: tool}, {tool_name: func})}),
            conversation=Conversation(path=tmp_path / f"{session_id}.jsonl"),
        )
        return [chunk async for chunk in agent.run({"role": "user", "content": "call a tool"})]
    finally:
        await server.cleanup()


def _in_a_disposable_loop(coro_factory: Any, *, seconds: float) -> list[str]:
    """在一条**自己的**事件循环里跑一次, 跑完把循环整个丢掉。

    为什么不能直接在 pytest 的循环里跑: 探测只是**放手不等**, 那个吸收了取消的任务
    仍在原地忙转 —— 这一点无法从进程内解除 (实测取消它、取消等它的子任务都无效)。
    留在 pytest 共享的 runner 循环里, 它会让整个 pytest 进程收不了尾: 判据自己
    PASS 了, 进程却挂到外层超时被杀。

    实测过循环关掉后忙转确实停了: 关闭后进程 2 秒空转只烧掉 0.00s CPU。所以「用一条
    用完即弃的循环」既是判据能跑完的前提, 也顺带记下了这个事实。

    返回收集到的 ERROR 日志行。sink 挂在这条线程里, 但 loguru 的 logger 是全进程共享
    的, 所以主线程照样读得到。
    """
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="ERROR")
    failure: list[BaseException] = []

    def worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.wait_for(coro_factory(), timeout=seconds))
        except BaseException as exc:  # 原样搬回主线程去断言
            failure.append(exc)
        finally:
            # 不 shutdown_asyncgens / 不等 pending: 忙转的那个任务等不掉, 等它
            # 就等于把上面说的那个挂死搬到这里来。close() 才是让它停下的手段。
            loop.close()

    thread = threading.Thread(target=worker, name="livelock-probe", daemon=True)
    thread.start()
    # 比循环内的超时再宽一点: 超了说明连「放手」都没做到。
    thread.join(timeout=seconds + 15.0)
    logger.remove(sink_id)
    assert not thread.is_alive(), "探测线程收不了尾: 说明回合没能从活锁里脱身"
    # ``asyncio.wait_for`` 的超时是活锁判据本身: 忙转时它打不出来, 于是线程超时,
    # 上面那条断言先响。
    assert not isinstance(failure[0] if failure else None, TimeoutError), "回合卡在活锁里没有返回"
    return messages


@pytest.mark.anyio
async def test_a_tool_that_cancels_its_own_task_and_swallows_it_is_reported_not_spun_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事故的真实形状: 工具在 task group 子任务里取消**自己所在**的任务并 suppress 掉。

    ``asyncio.current_task()`` 判不出这个形状 —— 工具跑在 task group 的**子任务**里,
    当前任务不是被取消的那个, 所以调用点凭直觉写的检查都是无效的。这里不 import
    任何飞书的东西: 自指工具就地构造, 免得内核判据反向依赖 ``agents/feishu``。
    """
    # 阈值调到亚秒级, 判据才不用真等 30 秒。这是个模块常量而不是新配置字段:
    # 生产不需要拧它, 只有判据需要。
    monkeypatch.setattr(agent_module, "TOOL_BATCH_LIVELOCK_SECONDS", 0.5)

    async def self_cancelling_tool() -> str:
        """取消自己所在的任务, 并把 CancelledError 吞掉 (事故那处的形状)。"""
        me = asyncio.current_task()
        assert me is not None

        # 关键: 取消**发生在一个子任务里**, 目标是当前这条执行流所在的任务。
        # 事故里这一层是 forget_and_wait 起的收尾等待。
        async def cancel_the_flow_we_are_in() -> None:
            me.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                async with asyncio.timeout(1.0):
                    await me

        async with anyio.create_task_group() as inner:
            inner.start_soon(cancel_the_flow_we_are_in)
        return "unreachable"

    async def turn() -> None:
        # 回合以 ``AgentError`` 收尾 —— 明确的错误, 而不是继续等。
        with pytest.raises(AgentError, match="stalled"):
            await _run_turn(
                tmp_path,
                session_id="stuck-session",
                tool_name="self_cancelling_tool",
                func=self_cancelling_tool,
            )

    messages = _in_a_disposable_loop(turn, seconds=_FAIL_AFTER_SECONDS)

    livelock_lines = [m for m in messages if "livelock" in m.lower()]
    assert livelock_lines, f"活锁没被记下来, 只有: {messages}"
    line = livelock_lines[0]
    # session_id: 生产上一个进程里跑着很多 session, 少了它这条日志指不到人。
    assert "stuck-session" in line, line
    # 卡住的工具名: 下一步排查的入口。
    assert "self_cancelling_tool" in line, line
    # 「疑似自指取消」的判定: 把一句「卡住了」变成一个可行动的结论。
    assert "self-cancel" in line.lower(), line


@pytest.mark.anyio
async def test_a_normal_cross_task_cancellation_still_takes_effect(tmp_path: Path) -> None:
    """反例: 守卫不得退化成「永不取消」。

    没有这条, 一个「什么都不取消」的实现也能让上面那条全绿。这里取消的是**别人**的
    任务 —— 正常的跨任务取消, 必须照旧生效。
    """
    outcome: list[str] = []

    async def victim() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            outcome.append("cancelled")
            raise

    async def cancelling_tool() -> str:
        """起一个别的任务再取消它 —— 与自指取消无关的正常路径。"""
        task = asyncio.ensure_future(victim())
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return "victim cancelled"

    with anyio.fail_after(_FAIL_AFTER_SECONDS):
        chunks = await _run_turn(
            tmp_path,
            session_id="healthy-session",
            tool_name="cancelling_tool",
            func=cancelling_tool,
        )

    assert outcome == ["cancelled"], outcome
    # 回合正常收尾: 工具结果进了历史, 模型的收尾回复也出来了。
    assert any("victim cancelled" in (c.reasoning or "") for c in chunks), chunks
    assert any("done" in (c.content or "") for c in chunks), chunks


@pytest.mark.anyio
async def test_a_merely_slow_tool_keeps_its_turn_and_is_not_called_a_livelock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二条反例: 超过阈值本身**不足以**终止回合。

    没有这条, 一个「超时就掀桌」的实现也能让活锁那条全绿 —— 代价是任何真的跑得久的
    工具都会被夺走回合。判据: 工具睡过阈值 (0.3s 阈值 / 睡 1.2s), 回合仍必须正常拿到
    结果, 且日志里**不出现**活锁 ERROR; 取而代之的是一条说明「是慢不是活锁」的 WARNING。

    实测支撑这条区分的是取消交付计数: 活锁那个任务 ``cancelling()`` 每秒涨约三万
    (0.2s 采到 10812), 而卡在 ``sleep(3600)`` 里的慢工具恒为 0。
    """
    monkeypatch.setattr(agent_module, "TOOL_BATCH_LIVELOCK_SECONDS", 0.3)
    records: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda m: records.append((m.record["level"].name, m.record["message"])),
        level="WARNING",
    )

    async def slow_tool() -> str:
        """跑得比阈值久, 但没有任何自指取消。"""
        await asyncio.sleep(1.2)
        return "slow but fine"

    try:
        with anyio.fail_after(_FAIL_AFTER_SECONDS):
            chunks = await _run_turn(
                tmp_path,
                session_id="slow-session",
                tool_name="slow_tool",
                func=slow_tool,
            )
    finally:
        logger.remove(sink_id)

    # 回合没被夺走: 工具结果照样回来了。
    assert any("slow but fine" in (c.reasoning or "") for c in chunks), chunks
    assert not [m for _lvl, m in records if "livelock in session" in m], records
    slow_lines = [m for lvl, m in records if lvl == "WARNING" and "slow tool rather than a livelock" in m]
    assert slow_lines, f"慢工具该留一条 WARNING 说明还在等, 实得: {records}"
    assert "slow-session" in slow_lines[0], slow_lines[0]
    assert "slow_tool" in slow_lines[0], slow_lines[0]


@pytest.mark.anyio
async def test_an_error_out_of_the_tool_batch_still_propagates(tmp_path: Path) -> None:
    """第三条反例: 探测层不许**吞掉** task group 自己抛的异常。

    这条是变异复核补出来的 —— 把 ``await task`` 删掉 (等价于「正常路径也 detach」)
    之后, 上面三条判据全都照绿, 说明没有一条盯着「异常照旧往外传」。而这条路真实存在:
    ``screen_tool_call`` 在每个工具的 ``except Exception`` **之外**, 它抛出来就会穿过
    task group。少了这条断言, 一个吞异常的探测层能让工具闸门静默失效。

    用工具闸门本身来触发, 而不是造一个假异常: 判据于是落在真实路径上。
    """
    boom = RuntimeError("guard exploded")

    def _explode(session_id: str, fn: str, a: dict[str, Any]) -> None:
        raise boom

    async def any_tool() -> str:
        """不会被跑到 —— 闸门在它之前就抛了。"""
        return "never"

    with anyio.fail_after(_FAIL_AFTER_SECONDS):
        # ``anyio`` 把子任务异常裹成 ExceptionGroup, 原样往外传就该能在这里接到。
        with pytest.raises(BaseException) as caught:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(agent_module, "screen_tool_call", _explode)
                await _run_turn(
                    tmp_path,
                    session_id="exploding-session",
                    tool_name="any_tool",
                    func=any_tool,
                )

    assert boom in _flatten(caught.value), caught.value


def _flatten(exc: BaseException) -> list[BaseException]:
    """把 ExceptionGroup 摊平 —— 异常在哪一层被裹起来不是这条判据关心的。"""
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for inner in exc.exceptions:
            out.extend(_flatten(inner))
        return out
    return [exc]
