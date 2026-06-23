from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

from psi_agent.session.tools import load_tools_from_workspace


@pytest.mark.anyio
async def test_load_tools_single_function(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(
        textwrap.dedent("""\
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.

            Args:
                command: The command to run.
            \"\"\"
            return "output"
    """)
    )

    tools, _ = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert tools["bash"].name == "bash"
    assert "Execute a bash command" in tools["bash"].description
    params = tools["bash"].parameters
    assert params["properties"]["command"]["type"] == "string"
    assert "command" in params["required"]


@pytest.mark.anyio
async def test_load_tools_multiple_functions(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(
        textwrap.dedent("""\
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """)
    )
    (tools_dir / "read_file.py").write_text(
        textwrap.dedent("""\
        async def read_file(path: str, encoding: str = "utf-8") -> str:
            \"\"\"Read a file.

            Args:
                path: Path to the file.
                encoding: File encoding.
            \"\"\"
            ...
    """)
    )

    tools, _ = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 2
    assert "bash" in tools
    assert "read_file" in tools


@pytest.mark.anyio
async def test_load_tools_ignores_private_functions(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(
        textwrap.dedent("""\
        async def _helper() -> None:
            ...
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """)
    )

    tools, _ = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert "bash" in tools
    assert "_helper" not in tools


@pytest.mark.anyio
async def test_load_tools_ignores_non_async(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(
        textwrap.dedent("""\
        def sync_helper() -> None:
            ...
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """)
    )

    tools, _ = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert "bash" in tools


@pytest.mark.anyio
async def test_load_tools_empty_dir(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    tools, _ = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 0


@pytest.mark.anyio
async def test_load_tools_missing_dir(tmp_path: Path) -> None:
    tools, _ = await load_tools_from_workspace(tmp_path / "nonexistent")
    assert len(tools) == 0


@pytest.mark.anyio
async def test_load_tools_all_non_private_functions(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "bash.py").write_text(
        textwrap.dedent("""\
        async def search(command: str) -> str:
            \"\"\"Search for something.\"\"\"
            ...
        async def bash(command: str) -> str:
            \"\"\"Execute a bash command.\"\"\"
            ...
    """)
    )

    tools, _ = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 2
    assert "bash" in tools
    assert "search" in tools


@pytest.mark.anyio
async def test_load_tools_skips_unsupported_type(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "broken.py").write_text(
        textwrap.dedent("""\
        async def broken(data: bytes) -> str:
            ...
    """)
    )

    tools, callables = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 0
    assert len(callables) == 0


@pytest.mark.anyio
async def test_load_tools_duplicate_name_warning_skip(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "a.py").write_text("async def echo(msg: str) -> str:\n    ...\n")
    (tools_dir / "b.py").write_text("async def echo(msg: str) -> str:\n    ...\n")

    tools, _callables = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 1
    assert "echo" in tools


@pytest.mark.anyio
async def test_load_tools_ignores_private_file(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "_utils.py").write_text("async def secret() -> str:\n    ...\n")

    tools, _callables = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 0


@pytest.mark.anyio
async def test_load_tools_syntax_error_caught(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "broken.py").write_text("this is not valid python {{{")

    tools, _callables = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 0


@pytest.mark.anyio
async def test_load_tools_spec_none_caught(tmp_path: Path, monkeypatch) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "echo.py").write_text("async def echo(msg: str) -> str:\n    ...\n")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda name, path: None)

    tools, _callables = await load_tools_from_workspace(tools_dir)
    assert len(tools) == 0
