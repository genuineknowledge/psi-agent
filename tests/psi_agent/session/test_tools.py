from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from psi_agent.session.tools import load_tools_from_workspace


def test_load_tools_single_function(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(textwrap.dedent("""\
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.

            Args:
                command: The command to run.
            \"\"\"
            return "output"
    """))

    tools = load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert tools["bash"].name == "bash"
    assert "Execute a bash command" in tools["bash"].description
    params = tools["bash"].parameters
    assert params["properties"]["command"]["type"] == "string"
    assert "command" in params["required"]


def test_load_tools_multiple_functions(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(textwrap.dedent("""\
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """))
    (tools_dir / "read_file.py").write_text(textwrap.dedent("""\
        async def read_file(path: str, encoding: str = "utf-8") -> str:
            \"\"\"Read a file.

            Args:
                path: Path to the file.
                encoding: File encoding.
            \"\"\"
            ...
    """))

    tools = load_tools_from_workspace(tools_dir)
    assert len(tools) == 2
    assert "bash" in tools
    assert "read_file" in tools


def test_load_tools_ignores_private_functions(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(textwrap.dedent("""\
        async def _helper() -> None:
            ...
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """))

    tools = load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert "bash" in tools
    assert "_helper" not in tools


def test_load_tools_ignores_non_async(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(textwrap.dedent("""\
        def sync_helper() -> None:
            ...
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """))

    tools = load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert "bash" in tools


def test_load_tools_empty_dir(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    tools = load_tools_from_workspace(tools_dir)
    assert len(tools) == 0


def test_load_tools_missing_dir(tmp_path: Path) -> None:
    tools = load_tools_from_workspace(tmp_path / "nonexistent")
    assert len(tools) == 0


def test_load_tools_ignores_non_matching_function_name(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(textwrap.dedent("""\
        async def other_name(command: str) -> str:
            \"\"\"Not the right name.\"\"\"
            ...
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """))

    tools = load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert "bash" in tools
    assert "other_name" not in tools
