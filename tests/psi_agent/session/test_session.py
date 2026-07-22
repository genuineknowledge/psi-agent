from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.session import Session
from psi_agent.session.conversation import Conversation
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


def test_workspace_empty_string_uses_cwd(tmp_path: Path) -> None:
    session = Session(workspace="", channel_socket=str(tmp_path / "c.sock"), ai_socket=str(tmp_path / "a.sock"))
    assert session.workspace == ""
