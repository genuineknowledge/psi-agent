from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.session.tools import load_tools_from_workspace

WINDOWS_WORKSPACE = Path("examples/a-simple-windows-workspace")


@pytest.mark.anyio
async def test_windows_workspace_powershell_tool_registers() -> None:
    """The Windows workspace must expose a loadable ``powershell`` tool.

    Regression guard: the tool loader swallows import errors per file
    (``tools.py`` logs and continues), so a real error in ``powershell.py``
    would silently leave the workspace with zero tools instead of failing
    loudly. This test imports the real example file through the production
    loader and asserts the tool actually registers.

    Note: ``powershell.py`` uses the unparenthesized ``except A, B:`` form,
    which is valid on this project's required interpreter (Python >= 3.14,
    PEP 758) and is what ``ruff format`` normalizes to. This test loads it
    through the real 3.14 loader to keep that guarantee honest.
    """
    tools, callables = await load_tools_from_workspace(WINDOWS_WORKSPACE / "tools")

    assert "powershell" in tools, f"powershell tool failed to load; got {list(tools)}"
    assert "powershell" in callables
