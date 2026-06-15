from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


def _bash_tool_paths() -> list[Path]:
    return sorted(Path("examples").glob("*/tools/bash.py"))


def _load_bash_tool(path: Path):
    spec = importlib.util.spec_from_file_location("bash_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
@pytest.mark.parametrize("path", _bash_tool_paths(), ids=lambda path: path.parts[1])
async def test_bash_tool_uses_bash_not_host_shell(path: Path) -> None:
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")

    module = _load_bash_tool(path)

    output = await module.tool("printf ok")

    assert _stdout_text(output) == "ok"


@pytest.mark.anyio
@pytest.mark.parametrize("path", _bash_tool_paths(), ids=lambda path: path.parts[1])
async def test_bash_tool_reports_missing_bash(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    module = _load_bash_tool(path)
    monkeypatch.setattr(module, "_find_bash", lambda: None)

    output = await module.tool("printf ok")

    assert "bash executable was not found" in _combined_text(output)


def _stdout_text(output) -> str:
    if isinstance(output, dict):
        assert output["exit_code"] == 0
        return str(output["stdout"])
    return str(output)


def _combined_text(output) -> str:
    if isinstance(output, dict):
        return str(output["stdout"]) + str(output["stderr"])
    return str(output)
