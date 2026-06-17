"""Execute code tool — run Python code in a subprocess and return output."""

from __future__ import annotations

import asyncio
import sys
import tempfile

import anyio


async def execute_code(code: str, timeout: int = 30) -> str:
    """Execute a Python code string in a subprocess.

    Args:
        code: Python source code to execute.
        timeout: Maximum seconds to wait. Defaults to 30.

    Returns:
        Combined stdout/stderr output, or error/timeout message.
    """
    if not code.strip():
        return "[Error] 'code' must not be empty."

    # Write code to a temp file so tracebacks show proper line numbers
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        tmp_path = f.name
        f.write(code)

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return f"[Error] Execution timed out after {timeout}s."

        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        combined = (out + err).rstrip()

        if process.returncode != 0:
            combined += f"\n[Exit code: {process.returncode}]"

        return combined or "(no output)"
    finally:
        try:
            await anyio.Path(tmp_path).unlink()
        except Exception:
            pass
