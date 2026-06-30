from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.session import Session
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
    await anyio.Path(systems / "system.py").write_text("def unrelated():\n    pass")
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_system_prompt_builder_not_async(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text("def system_prompt_builder():\n    return 'hello'")
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_system_prompt_builder_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'test prompt'"
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
    await anyio.Path(systems / "system.py").write_text("this is not valid python {{{")
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert await sp._builder() == ""


@pytest.mark.anyio
async def test_rebuild_checker_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(
        "async def system_prompt_builder() -> str:\n    return 'p'\n\n"
        "async def system_prompt_rebuild_checker() -> bool:\n    return True\n"
    )
    sp = await SystemPrompt.from_workspace(ws, "test")
    assert sp is not None
    assert await sp._builder() == "p"
    assert await sp._checker() is True


def test_workspace_empty_string_uses_cwd(tmp_path: Path) -> None:
    session = Session(workspace="", channel_socket=str(tmp_path / "c.sock"), ai_socket=str(tmp_path / "a.sock"))
    assert session.workspace == ""
