from __future__ import annotations

import asyncio
from pathlib import Path

from psi_agent.session.agent import _load_system_prompt_builder


def test_system_py_not_exists(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    # No systems/ directory at all
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_system_py_missing_system_prompt_builder(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("def unrelated():\n    pass")
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_system_prompt_builder_not_async(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("def system_prompt_builder():\n    return 'hello'")
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_system_prompt_builder_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("async def system_prompt_builder() -> str:\n    return 'test prompt'")
    builder = _load_system_prompt_builder(ws)
    assert builder is not None

    result = asyncio.run(builder())
    assert result == "test prompt"


def test_syntax_error_in_system_py(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("this is not valid python {{{")
    result = _load_system_prompt_builder(ws)
    assert result is None


def test_workspace_empty_string_uses_cwd(tmp_path: Path) -> None:
    from psi_agent.session import Session

    session = Session(workspace="", channel_socket=str(tmp_path / "c.sock"), ai_socket=str(tmp_path / "a.sock"))
    assert session.workspace == ""
