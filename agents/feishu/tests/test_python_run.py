"""A Python snippet must survive the trip without any escaping."""

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

_pr: Any = importlib.import_module("python_run")


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the tool at a throwaway workspace so scratch files land there.

    ``WORKSPACE_DIR`` is the env var ``_runtime_paths.workspace_dir()`` reads;
    setting anything else leaves the tool on its package fallback — the real
    ``agents/feishu`` — which makes the scratch-hygiene assertions
    below pass vacuously against an empty tmp dir.

    Returned already resolved so async tests never call ``Path.resolve()``
    themselves — a blocking pathlib call in an async body is ASYNC240.
    """
    resolved = tmp_path.resolve()
    monkeypatch.setenv("WORKSPACE_DIR", str(resolved))
    return resolved


# ── the reason this tool exists ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backslashes_and_quotes_need_no_escaping() -> None:
    """Via ``bash`` + ``python -c`` this needs three layers of escaping."""
    code = r"""
import re
print(re.findall(r"\d+", "a1b22"))
print(r"C:\Users\12815\x")
print('it\'s "quoted"')
"""
    out = await _pr.python_run(code)

    assert "['1', '22']" in out
    assert r"C:\Users\12815\x" in out
    assert 'it\'s "quoted"' in out


@pytest.mark.asyncio
async def test_multiline_snippet_runs_as_written() -> None:
    out = await _pr.python_run("total = 0\nfor i in range(4):\n    total += i\nprint(total)\n")

    assert "6" in out


@pytest.mark.asyncio
async def test_utf8_output_is_intact() -> None:
    out = await _pr.python_run("print('姓名测试')\n")

    assert "姓名测试" in out
    assert "�" not in out


# ── failure reporting ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_syntax_error_is_reported_without_running() -> None:
    out = await _pr.python_run("def f(:\n    pass\n")

    assert "syntax error" in out
    assert "line 1" in out
    assert "nothing was run" in out


@pytest.mark.asyncio
async def test_traceback_line_numbers_match_the_snippet() -> None:
    out = await _pr.python_run("x = 1\ny = 2\nraise ValueError('boom')\n")

    assert "ValueError: boom" in out
    assert "line 3" in out
    assert "[Exit code: 1]" in out


@pytest.mark.asyncio
async def test_scratch_path_is_not_leaked_into_output() -> None:
    """The scratch file is gone by the time the agent reads this, and its
    random name says nothing; the line numbers are the useful part."""
    out = await _pr.python_run("raise RuntimeError('x')\n")

    assert "<snippet>" in out
    assert "snippet-" not in out
    assert ".psi-scratch" not in out


@pytest.mark.asyncio
async def test_timeout_keeps_output_printed_first() -> None:
    out = await _pr.python_run("import sys, time\nprint('EARLY')\nsys.stdout.flush()\ntime.sleep(30)\n", 5)

    assert "timed out" in out
    assert "EARLY" in out


@pytest.mark.asyncio
async def test_timeout_without_output_says_so() -> None:
    out = await _pr.python_run("import time\ntime.sleep(30)\n", 3)

    assert "timed out" in out
    assert "no output was produced" in out


@pytest.mark.asyncio
async def test_silent_success_is_distinguishable() -> None:
    out = await _pr.python_run("pass\n")

    assert "exit code 0" in out


@pytest.mark.asyncio
async def test_empty_code_is_refused() -> None:
    assert "No code to run" in await _pr.python_run("   \n")
    assert "No code to run" in await _pr.python_run("")


# ── scratch file hygiene ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scratch_file_is_removed_after_a_normal_run(_workspace: Path) -> None:
    await _pr.python_run("print('x')\n")

    scratch = _workspace / ".psi-scratch"
    assert list(scratch.glob("snippet-*.py")) == []


@pytest.mark.asyncio
async def test_scratch_file_is_removed_after_a_crash(_workspace: Path) -> None:
    await _pr.python_run("raise SystemExit(2)\n")

    assert list((_workspace / ".psi-scratch").glob("snippet-*.py")) == []


@pytest.mark.asyncio
async def test_runs_from_the_workspace_directory(_workspace: Path) -> None:
    out = await _pr.python_run("import os\nprint(os.getcwd())\n")

    assert str(_workspace) in out
