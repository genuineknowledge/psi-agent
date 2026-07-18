from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_bash: Any = importlib.import_module("bash")


@pytest.mark.asyncio
async def test_bash_python_chinese_stdout_is_utf8() -> None:
    """Chinese printed by a child Python must come back intact, not mojibake.

    On Windows the child would default to GBK stdout and the bytes would decode
    as U+FFFD replacement chars; PYTHONUTF8=1 (injected by the bash tool) fixes it.
    """
    out = await _bash.bash("python -c \"print('姓名测试')\"", timeout_seconds=30)
    assert "姓名测试" in out
    assert "�" not in out  # no replacement chars


@pytest.mark.asyncio
async def test_bash_plain_ascii_still_works() -> None:
    out = await _bash.bash("echo hello-123", timeout_seconds=30)
    assert "hello-123" in out
