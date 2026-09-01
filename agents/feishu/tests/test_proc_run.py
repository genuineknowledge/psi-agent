"""Timeouts must keep the output the command already produced."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_proc_run: Any = importlib.import_module("_proc_run")
_bash: Any = importlib.import_module("bash")


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


# ── run_capturing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_captures_output_produced_before_timeout() -> None:
    """The regression this pins: partial output used to be discarded.

    Losing it is what made the agent retry the same command at a higher limit
    instead of looking at where it stalled.
    """
    result = await _proc_run.run_capturing(
        _py("import sys, time; print('STEP1'); sys.stdout.flush(); time.sleep(30)"),
        timeout_seconds=5,
    )

    assert result.timed_out is True
    assert "STEP1" in result.stdout


@pytest.mark.asyncio
async def test_timeout_with_no_output_is_distinguishable() -> None:
    result = await _proc_run.run_capturing(_py("import time; time.sleep(30)"), timeout_seconds=3)

    assert result.timed_out is True
    assert result.stdout == ""


@pytest.mark.asyncio
async def test_successful_run_reports_zero_and_output() -> None:
    result = await _proc_run.run_capturing(_py("print('hello')"), timeout_seconds=30)

    assert result.timed_out is False
    assert result.returncode == 0
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_nonzero_exit_is_reported_with_its_output() -> None:
    result = await _proc_run.run_capturing(
        _py("import sys; print('partial'); sys.exit(3)"),
        timeout_seconds=30,
    )

    assert result.returncode == 3
    assert "partial" in result.stdout
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_stderr_is_captured_separately() -> None:
    result = await _proc_run.run_capturing(
        _py("import sys; print('OUT'); print('ERR', file=sys.stderr)"),
        timeout_seconds=30,
    )

    assert "OUT" in result.stdout
    assert "ERR" in result.stderr


@pytest.mark.asyncio
async def test_utf8_output_survives_round_trip() -> None:
    """Windows children default to the system codepage; PYTHONUTF8 counters it."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    result = await _proc_run.run_capturing(_py("print('姓名测试')"), timeout_seconds=30, env=env)

    assert "姓名测试" in result.stdout
    assert "�" not in result.stdout


@pytest.mark.asyncio
async def test_process_is_dead_after_timeout() -> None:
    result = await _proc_run.run_capturing(_py("import time; time.sleep(30)"), timeout_seconds=3)

    assert result.timed_out is True
    assert result.orphaned is False


@pytest.mark.asyncio
async def test_elapsed_is_bounded_by_the_limit() -> None:
    result = await _proc_run.run_capturing(_py("import time; time.sleep(30)"), timeout_seconds=3)

    # Generous upper bound: the kill escalation is allowed a couple of seconds.
    assert result.elapsed < 15


# ── bash tool surface ─────────────────────────────────────────────────────────


# A bare interpreter name, not ``sys.executable``: the command goes through
# ``bash -lc``, which eats the backslashes in a Windows path (``C:\Users\...``
# arrives as ``C:Users...`` and is not found).
_PY = "python"


@pytest.mark.asyncio
async def test_bash_timeout_message_carries_partial_output() -> None:
    out = await _bash.bash(
        f"{_PY} -c \"import sys, time; print('BEFORE'); sys.stdout.flush(); time.sleep(30)\"",
        timeout_seconds=5,
    )

    assert "timed out" in out
    assert "BEFORE" in out
    assert "output produced before the timeout" in out


@pytest.mark.asyncio
async def test_bash_timeout_says_so_when_nothing_was_printed() -> None:
    out = await _bash.bash(f'{_PY} -c "import time; time.sleep(30)"', timeout_seconds=3)

    assert "timed out" in out
    assert "no output was produced" in out


@pytest.mark.asyncio
async def test_bash_distinguishes_silent_success_from_no_run() -> None:
    """``(no output)`` used to cover both, so the agent re-ran to tell them apart."""
    out = await _bash.bash(f'{_PY} -c "pass"', timeout_seconds=30)

    assert "exit code 0" in out


@pytest.mark.asyncio
async def test_bash_still_reports_nonzero_exit_and_output() -> None:
    out = await _bash.bash(f"{_PY} -c \"import sys; print('x'); sys.exit(3)\"", timeout_seconds=30)

    assert "x" in out
    assert "[Exit code: 3]" in out
