from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast

import anyio
import pytest

from psi_agent.session import live_agent
from psi_agent.session.agent import SessionAgent
from psi_agent.session.history_display import (
    KIND_TRIGGER_DISPLAY,
    KIND_TRIGGER_SILENT,
    is_displayable_chat_message,
    message_kind,
    with_kind,
)
from psi_agent.session.protocol import AgentChunk


class _FakeAgent:
    """Just enough SessionAgent for a resume: a turn lock and a chunk-yielding ``run``."""

    def __init__(self, *, block: anyio.Event | None = None) -> None:
        self._lock = anyio.Lock()
        self.turns: list[dict[str, Any]] = []
        self.response_kinds: list[str | None] = []
        self.closed = 0
        self._block = block

    def run(
        self,
        user_message: dict[str, Any],
        extra_params: dict[str, Any] | None = None,
        *,
        response_kind: str | None = None,
    ) -> AsyncGenerator[AgentChunk]:
        async def _gen() -> AsyncGenerator[AgentChunk]:
            try:
                if self._block is not None:
                    await self._block.wait()
                self.turns.append(user_message)
                self.response_kinds.append(response_kind)
                yield AgentChunk(content="done")
            finally:
                # 真 ``run`` 在这里跑 rollback/commit; 计数用来锁住「生成器被显式收尾」。
                self.closed += 1

        return _gen()


def _as_agent(agent: _FakeAgent) -> SessionAgent:
    """The stand-in, typed as what ``register`` takes.

    ``live_agent`` deliberately annotates the real ``SessionAgent`` rather than a
    structural protocol — the seam is meant to stay narrow — so the fake is cast at that
    one boundary instead of widening the production signature for a test. Assertions keep
    using the concrete ``_FakeAgent``, which is where ``turns`` lives.
    """
    return cast("SessionAgent", agent)


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    live_agent.reset_all()
    yield
    live_agent.reset_all()


def test_register_is_scoped_to_the_block() -> None:
    """注册与「正在服务」同生命周期: 过期句柄会续跑一个没人听的对话。"""
    agent = _FakeAgent()
    assert live_agent.get("s1") is None
    with live_agent.register("s1", _as_agent(agent)):
        assert live_agent.get("s1") is agent
    assert live_agent.get("s1") is None


def test_blank_session_ids_are_not_registered() -> None:
    """空 id 不能共用一个槽位 —— 续跑必须落回它来的那个对话。"""
    agent = _FakeAgent()
    with live_agent.register("   ", _as_agent(agent)):
        assert live_agent.get("") is None


def test_registrations_are_keyed_per_session() -> None:
    """Gateway 一进程多 Session: 全局「当前 agent」会续跑到最后注册的那个。"""
    first, second = _FakeAgent(), _FakeAgent()
    with live_agent.register("s1", _as_agent(first)), live_agent.register("s2", _as_agent(second)):
        assert live_agent.get("s1") is first
        assert live_agent.get("s2") is second


@pytest.mark.anyio
async def test_resume_runs_one_turn_carrying_the_content() -> None:
    """``run`` 是生成器: 不迭代它等于什么都没跑, 所以这里必须把回合驱动到底。"""
    agent = _FakeAgent()
    with live_agent.register("s1", _as_agent(agent)):
        ran = await live_agent.resume_session_turn("s1", "carry me")
    assert ran is True
    assert len(agent.turns) == 1
    assert agent.turns[0]["content"] == "carry me"
    assert agent.turns[0]["role"] == "user"


@pytest.mark.anyio
async def test_resume_is_out_of_band_and_never_a_chat_bubble() -> None:
    """带外回合: 注入块和回答都不该出现在 /history 的聊天气泡里。

    给 ``chat`` 会有两处后果 —— 那段给模型的指令正文像用户亲手打的一样进记录, 而回合已经
    用工具把话说过一遍, 气泡是第二遍 (正是本次要修掉的双回复的另一种形态)。
    """
    agent = _FakeAgent()
    with live_agent.register("s1", _as_agent(agent)):
        await live_agent.resume_session_turn("s1", "<feishu_auth_granted>\nopen_id: ou_a\n</feishu_auth_granted>")

    user_row = agent.turns[0]
    assert message_kind(user_row) == KIND_TRIGGER_SILENT
    assert agent.response_kinds == [KIND_TRIGGER_SILENT]
    assert is_displayable_chat_message(user_row) is False
    assistant_row = with_kind({"role": "assistant", "content": "文档建好了"}, KIND_TRIGGER_SILENT)
    assert is_displayable_chat_message(assistant_row) is False


@pytest.mark.anyio
async def test_resume_can_be_asked_to_surface_in_the_console() -> None:
    """要让回答进 Web Console 得显式传 display —— 缺省不显示是刻意的, 不是漏配。"""
    agent = _FakeAgent()
    with live_agent.register("s1", _as_agent(agent)):
        await live_agent.resume_session_turn("s1", "x", kind=KIND_TRIGGER_DISPLAY)
    assert agent.response_kinds == [KIND_TRIGGER_DISPLAY]
    assert is_displayable_chat_message(with_kind({"role": "assistant", "content": "done"}, KIND_TRIGGER_DISPLAY))


@pytest.mark.anyio
async def test_resume_closes_the_agent_generator() -> None:
    """agent loop 自己的收尾 (rollback/commit) 必须在本帧退出前跑完。"""
    agent = _FakeAgent()
    with live_agent.register("s1", _as_agent(agent)):
        await live_agent.resume_session_turn("s1", "x")
    assert agent.closed == 1


@pytest.mark.anyio
async def test_resume_reports_false_when_nothing_is_serving() -> None:
    """调用方要能分辨「续跑不了」, 否则活会被静默丢掉而不是退回通知。"""
    assert await live_agent.resume_session_turn("missing", "x") is False


@pytest.mark.anyio
async def test_resume_waits_for_an_in_flight_turn() -> None:
    """续跑不是特权: 跳过 turn 锁会让两轮交错写同一份 conversation。"""
    release = anyio.Event()
    agent = _FakeAgent(block=release)
    finished: list[str] = []

    async def _hold_the_lock() -> None:
        async with agent._lock:
            await release.wait()
            finished.append("holder")

    async def _resume() -> None:
        with live_agent.register("s1", _as_agent(agent)):
            await live_agent.resume_session_turn("s1", "second")
        finished.append("resume")

    with anyio.fail_after(3):
        async with anyio.create_task_group() as tg:
            tg.start_soon(_hold_the_lock)
            await anyio.sleep(0.05)  # 让持锁方先真正拿到锁
            tg.start_soon(_resume)
            await anyio.sleep(0.05)
            assert finished == []  # 续跑还在等锁, 没有插队
            release.set()

    assert finished == ["holder", "resume"]


def test_resume_payload_is_tagged_and_skips_blanks() -> None:
    """带标签是为了让模型分得清「你等的事发生了」和「有人在说话」。"""
    rendered = live_agent.resume_payload("feishu_auth_granted", {"open_id": "ou_a", "detail": "", "status": "granted"})
    assert rendered.startswith("<feishu_auth_granted>")
    assert rendered.endswith("</feishu_auth_granted>")
    assert "open_id: ou_a" in rendered
    assert "status: granted" in rendered
    assert "detail" not in rendered
