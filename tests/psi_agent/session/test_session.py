from __future__ import annotations

import asyncio
from pathlib import Path

from psi_agent.session import Session
from psi_agent.session.system_prompt import SystemPrompt


def test_system_py_not_exists(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    sp = asyncio.run(SystemPrompt.from_workspace(ws, "test"))
    assert asyncio.run(sp._builder()) == ""


def test_system_py_missing_system_prompt_builder(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("def unrelated():\n    pass")
    sp = asyncio.run(SystemPrompt.from_workspace(ws, "test"))
    assert asyncio.run(sp._builder()) == ""


def test_system_prompt_builder_not_async(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("def system_prompt_builder():\n    return 'hello'")
    sp = asyncio.run(SystemPrompt.from_workspace(ws, "test"))
    assert asyncio.run(sp._builder()) == ""


def test_system_prompt_builder_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("async def system_prompt_builder() -> str:\n    return 'test prompt'")
    sp = asyncio.run(SystemPrompt.from_workspace(ws, "test"))
    assert sp is not None

    result = asyncio.run(sp._builder())
    assert result == "test prompt"


def test_syntax_error_in_system_py(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("this is not valid python {{{")
    sp = asyncio.run(SystemPrompt.from_workspace(ws, "test"))
    assert asyncio.run(sp._builder()) == ""


def test_rebuild_checker_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'p'\n\n"
        "async def system_prompt_rebuild_checker() -> bool:\n    return True\n"
    )
    sp = asyncio.run(SystemPrompt.from_workspace(ws, "test"))
    assert sp is not None
    assert asyncio.run(sp._builder()) == "p"
    assert asyncio.run(sp._checker()) is True


def test_workspace_empty_string_uses_cwd(tmp_path: Path) -> None:
    session = Session(workspace="", channel_socket=str(tmp_path / "c.sock"), ai_socket=str(tmp_path / "a.sock"))
    assert session.workspace == ""
