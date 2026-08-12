from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest

import psi_agent.session as session_pkg
from psi_agent.session import Session
from psi_agent.session.conversation import Conversation
from psi_agent.session.schedule_registry import ACTIVATE_ALL
from psi_agent.session.system_prompt import SystemPrompt


@pytest.mark.anyio
async def test_system_py_not_exists(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    await anyio.Path(ws).mkdir()
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_system_py_missing_system_prompt_builder(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text("def unrelated():\n    pass", encoding="utf-8")
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_system_prompt_builder_not_async(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "def system_prompt_builder():\n    return 'hello'", encoding="utf-8"
    )
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_system_prompt_builder_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'test prompt'", encoding="utf-8"
    )
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert sp is not None

    result = await sp._builder()
    assert result == "test prompt"


@pytest.mark.anyio
async def test_syntax_error_in_system_py(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text("this is not valid python {{{", encoding="utf-8")
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_rebuild_checker_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'p'\n\n"
        "async def system_prompt_rebuild_checker() -> bool:\n    return True\n",
        encoding="utf-8",
    )
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert sp is not None
    assert await sp._builder() == "p"
    assert await sp._checker() is True


@pytest.mark.anyio
async def test_builder_and_checker_receive_current_user_message(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "async def system_prompt_builder(user_message):\n"
        "    return user_message['content']\n\n"
        "async def system_prompt_rebuild_checker(user_message):\n"
        "    return user_message['content'] == 'rebuild'\n",
        encoding="utf-8",
    )
    sp = await SystemPrompt.from_workspace(ws, "test-message")
    conversation = Conversation()

    await sp.ensure(conversation, {"role": "user", "content": "first"})
    assert conversation.messages[0]["content"] == "first"

    await sp.ensure(conversation, {"role": "user", "content": "rebuild"})
    assert conversation.messages[0]["content"] == "rebuild"


@pytest.mark.anyio
async def test_after_turn_hook_loads_and_runs(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "async def system_after_turn(user_message, assistant_message):\n    return user_message, assistant_message\n",
        encoding="utf-8",
    )
    sp = await SystemPrompt.from_workspace(ws, "test")
    user = {"role": "user", "content": "hello"}
    assistant = {"role": "assistant", "content": "hi"}

    assert await sp._after_turn(user, assistant) == (user, assistant)
    await sp.run_after_turn(user, assistant)


@pytest.mark.anyio
async def test_after_turn_hook_failure_is_recoverable() -> None:
    async def fail(_user_message: dict, _assistant_message: dict) -> None:
        raise RuntimeError("broken hook")

    sp = SystemPrompt(after_turn=fail)
    await sp.run_after_turn({"role": "user"}, {"role": "assistant"})


@pytest.mark.anyio
async def test_before_turn_hook_loads_and_returns_result(tmp_path: Path) -> None:
    systems = tmp_path / "systems"
    await anyio.Path(systems).mkdir()
    await anyio.Path(systems / "system.py").write_text(
        "async def system_before_turn(user_message):\n"
        "    return {'breakout': {'needed': True}, 'seen': user_message['content']}\n",
        encoding="utf-8",
    )
    sp = await SystemPrompt.from_workspace(tmp_path, "test")
    result = await sp.run_before_turn({"content": "learn"})
    assert result["seen"] == "learn"


@pytest.mark.anyio
async def test_before_turn_hook_timeout_returns_empty_dict() -> None:
    async def slow(_user_message: dict[str, Any]) -> dict[str, Any]:
        await anyio.sleep(1)
        return {"unexpected": True}

    sp = SystemPrompt(before_turn=slow, before_turn_timeout_seconds=0.01)
    assert await sp.run_before_turn({"content": "learn"}) == {}


async def test_before_turn_default_timeout_allows_real_supervisor_completion() -> None:
    assert SystemPrompt()._before_turn_timeout_seconds == 30.0


@pytest.mark.anyio
async def test_before_turn_hook_invalid_result_returns_empty_dict() -> None:
    async def invalid(_user_message: dict[str, Any]) -> str:
        return "bad"

    assert await SystemPrompt(before_turn=invalid).run_before_turn({"content": "learn"}) == {}


@pytest.mark.anyio
async def test_before_turn_hook_exception_returns_empty_dict() -> None:
    async def fail(_user_message: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("broken hook")

    assert await SystemPrompt(before_turn=fail).run_before_turn({"content": "learn"}) == {}


@pytest.mark.anyio
async def test_before_turn_hook_propagates_cancellation() -> None:
    entered = anyio.Event()
    completed: list[dict[str, Any]] = []

    async def wait_forever(_user_message: dict[str, Any]) -> None:
        entered.set()
        await anyio.sleep_forever()

    async def run_hook(sp: SystemPrompt) -> None:
        completed.append(await sp.run_before_turn({"content": "learn"}))

    sp = SystemPrompt(before_turn=wait_forever)
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_hook, sp)
        await entered.wait()
        task_group.cancel_scope.cancel()

    assert completed == []


def test_workspace_empty_string_uses_cwd(tmp_path: Path) -> None:
    session = Session(workspace="", channel_socket=str(tmp_path / "c.sock"), ai_socket=str(tmp_path / "a.sock"))
    assert session.workspace == ""


# ── Activation list parsing (--active-schedules / --deactive-schedules) ───────


def test_name_set_empty_by_default() -> None:
    """Nothing is activated by default - a schedule must be fired by exactly one Session."""
    assert Session._name_set("") == set()


def test_name_set_wildcard() -> None:
    assert Session._name_set(ACTIVATE_ALL) == {ACTIVATE_ALL}


def test_name_set_splits_and_trims() -> None:
    assert Session._name_set(" daily , weekly ,") == {"daily", "weekly"}


def test_name_set_wildcard_with_names() -> None:
    """Wildcard alongside names: is_active already covers all, so the names are redundant but harmless."""
    assert Session._name_set("*, daily") == {ACTIVATE_ALL, "daily"}


def test_public_exports_cover_gateway_dependencies():
    """Gateway 依赖的符号必须走 Session 的公开门面。

    gateway/_history_manager.py 与 _scheduler_manager.py 此前直接从
    session.history_display / session.schedule_registry 导入 —— 依赖是刻意的,
    通道却是非正式的。这条测试钉住正式导出面。
    """
    expected = {
        "ACTIVATE_ALL",
        "KIND_CHAT",
        "Session",
        "SessionAgent",
        "extract_send_paths",
        "is_displayable_chat_message",
        "message_kind",
        "strip_transfer_markers",
        "wire_role",
    }
    assert expected <= set(session_pkg.__all__)
    for name in expected:
        assert hasattr(session_pkg, name), f"{name} declared in __all__ but not importable"
