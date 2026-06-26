from __future__ import annotations

import builtins
import importlib.util
import sys
import textwrap
import types
from pathlib import Path

import pytest

from psi_agent.session.tools import load_tools_from_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]


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


@pytest.mark.anyio
async def test_fusion_guard_example_workspace_is_thin_and_delegates(monkeypatch) -> None:
    workspace = REPO_ROOT / "examples" / "fusion-guard-security-workspace"

    assert workspace.is_dir()
    assert sorted(path.name for path in workspace.iterdir()) == ["tools"]

    tools, callables = await load_tools_from_workspace(workspace / "tools")

    assert set(tools) == {"bash"}
    assert tools["bash"].parameters["required"] == ["command"]
    assert set(tools["bash"].parameters["properties"]) == {"command", "cwd"}

    calls: list[tuple[str, str | None]] = []
    package = types.ModuleType("fusion_guard_security")
    runner = types.ModuleType("fusion_guard_security.runner")

    async def secure_bash(command: str, cwd: str | None = None) -> str:
        calls.append((command, cwd))
        return "delegated"

    runner.secure_bash = secure_bash
    monkeypatch.setitem(sys.modules, "fusion_guard_security", package)
    monkeypatch.setitem(sys.modules, "fusion_guard_security.runner", runner)

    result = await callables["bash"]("pwd", cwd="/tmp")

    assert result == "delegated"
    assert calls == [("pwd", "/tmp")]


@pytest.mark.anyio
async def test_fusion_guard_example_workspace_reports_missing_external_adapter(monkeypatch) -> None:
    workspace = REPO_ROOT / "examples" / "fusion-guard-security-workspace"
    _tools, callables = await load_tools_from_workspace(workspace / "tools")
    original_import = builtins.__import__

    def block_fusion_guard(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("fusion_guard_security"):
            raise ModuleNotFoundError(
                "No module named 'fusion_guard_security.runner'", name="fusion_guard_security.runner"
            )
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_fusion_guard)

    result = await callables["bash"]("pwd")

    assert "Fusion-Guard Dolphin adapter is not installed" in result
    assert "Fusion-Guard" in result
