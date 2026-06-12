from __future__ import annotations

import asyncio
from pathlib import Path

from psi_agent.session import _load_system_prompt_builder


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


def test_system_class_build_system_prompt_loads(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text(
        """
class System:
    def __init__(self, workspace_dir):
        self.workspace_dir = workspace_dir

    async def build_system_prompt(self, model=None, tool_names=None):
        return f"{model}:{','.join(tool_names or [])}:{self.workspace_dir.name}"
""".strip()
    )

    builder = _load_system_prompt_builder(ws, model="test-model", tool_names=["bash", "read"])
    assert builder is not None

    result = asyncio.run(builder())
    assert result == "test-model:bash,read:ws"


def test_syntax_error_in_system_py(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    systems = ws / "systems"
    systems.mkdir(parents=True)
    (systems / "system.py").write_text("this is not valid python {{{")
    result = _load_system_prompt_builder(ws)
    assert result is None
