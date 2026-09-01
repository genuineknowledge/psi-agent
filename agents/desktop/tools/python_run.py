"""Run a Python snippet without shell quoting in the way.

``bash("python -c \"...\"")`` puts a snippet through three layers of quoting —
JSON, then the shell, then Python's own string rules — and a mistake in any of
them surfaces as a Python error about the *code*, which points nowhere near the
real problem. Session logs show what that costs: multi-line snippets arriving
as one line of stacked ``\\\\`` and ``\\"``, and the agent eventually inventing
its own workaround of writing a scratch file, running it, then deleting it.

This tool is that workaround, done once and properly: the snippet travels as an
argument, so nothing re-parses it and nothing needs escaping.
"""

from __future__ import annotations

import ast
import os
import sys
import uuid

import _proc_run
import _runtime_paths as _paths

# Scratch files live under the workspace rather than the system temp dir so a
# run that is killed hard leaves its evidence somewhere the agent can find.
_SCRATCH_DIR = ".psi-scratch"

# What the snippet's path is called in output. Tracebacks name the scratch file,
# which is deleted by the time the agent reads them and whose random name says
# nothing; the line numbers are what matter and they line up with what was sent.
_DISPLAY_NAME = "<snippet>"


async def python_run(code: str, timeout_seconds: int = 60) -> str:
    """Run a Python snippet and return its output.

    Prefer this over ``bash`` with ``python -c`` for anything longer than a
    single simple line: the code is passed as an argument, so it never goes
    through shell quoting and backslashes and quotes need no escaping.

    Runs on the same interpreter that hosts the agent, from the workspace
    directory. A syntax error is reported without starting a process. On
    timeout, whatever the snippet printed first is still returned.

    Args:
        code: Python source to run — plain multi-line text, unescaped.
        timeout_seconds: Maximum seconds to wait before the process is stopped.

    Returns:
        Combined stdout and stderr, with the exit code appended on failure.
    """
    if not code.strip():
        return "[Error] No code to run."

    # Parse first: a syntax error needs no process, and reporting it here gives
    # a line number instead of an interpreter startup failure.
    try:
        ast.parse(code)
    except SyntaxError as e:
        where = f"line {e.lineno}" if e.lineno else "an unknown line"
        return f"[Error] The snippet has a syntax error at {where}: {e.msg}\n(nothing was run)"
    except ValueError as e:
        return f"[Error] The snippet is not parseable: {e}\n(nothing was run)"

    workspace = _paths.resolve_workspace()
    scratch_dir = workspace / _SCRATCH_DIR
    if not await scratch_dir.exists():
        await scratch_dir.mkdir(parents=True, exist_ok=True)
    script = scratch_dir / f"snippet-{uuid.uuid4().hex[:12]}.py"
    await script.write_text(code, encoding="utf-8")

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        result = await _proc_run.run_capturing(
            [sys.executable, str(script)],
            timeout_seconds=timeout_seconds,
            env=env,
            cwd=_paths.workspace_dir(),
        )
    finally:
        # Left behind only when the process outlived termination and could still
        # be executing the file.
        await script.unlink(missing_ok=True)

    combined = (result.stdout + result.stderr).replace(str(script), _DISPLAY_NAME).rstrip()

    if result.timed_out:
        head = f"[Error] The snippet timed out after {timeout_seconds}s."
        if result.orphaned:
            head += "\n[Warning] The process survived termination and may still be running."
        if not combined:
            return f"{head}\n(no output was produced before the timeout)"
        return f"{head}\n--- output produced before the timeout ---\n{combined}"

    if result.returncode != 0:
        return f"{combined}\n[Exit code: {result.returncode}]".lstrip("\n")

    return combined or "(exit code 0, no output)"
