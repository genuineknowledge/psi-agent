from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


def _load_bash_tool():
    path = Path("examples/a-simple-bash-only-workspace/tools/bash.py")
    spec = importlib.util.spec_from_file_location("bash_tool", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
async def test_bash_tool_uses_bash_not_host_shell() -> None:
    if not shutil.which("bash"):
        pytest.skip("bash is not installed")

    module = _load_bash_tool()

    output = await module.tool("printf ok")

    assert output == "ok"


@pytest.mark.anyio
async def test_bash_tool_reports_missing_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_bash_tool()
    monkeypatch.setattr(module, "_find_bash", lambda: None)

    output = await module.tool("printf ok")

    assert "bash executable was not found" in output
