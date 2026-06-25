"""Execute PowerShell commands (Windows-native counterpart of bash.py)."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import anyio
from loguru import logger


def _find_powershell() -> str:
    """Locate a PowerShell executable.

    Prefers PowerShell 7+ (``pwsh.exe``) when present, otherwise falls back
    to Windows PowerShell (``powershell.exe``), which ships with every
    modern Windows install. Absolute paths are tried first so the tool works
    even when ``PATH`` is minimal.
    """
    found = shutil.which("pwsh") or shutil.which("powershell")
    if found:
        return found

    candidates = [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ]
    for path in candidates:
        if Path(path).is_file():
            return path

    # Last resort: hope it is resolvable on PATH at runtime.
    return "powershell"


def _decode(raw: bytes) -> str:
    """Decode process output, tolerating the active Windows code page.

    PowerShell may emit UTF-8 or the locale's OEM/ANSI code page (e.g. GBK on
    Chinese Windows). Try UTF-8 first, then the platform default, and finally
    fall back to a lossy UTF-8 decode so a tool call never crashes on bytes.
    """
    for encoding in ("utf-8", "mbcs"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


async def powershell(command: str, *, cwd: str | None = None) -> str:
    """Execute a PowerShell command and return combined stdout and stderr output.

    Args:
        command: The PowerShell command to execute. Use with caution.
        cwd: Working directory. Defaults to the workspace root.
    """
    if cwd is None:
        cwd = str(Path(inspect.getfile(powershell)).parent.parent)

    pwsh = _find_powershell()
    # -NoProfile: skip user profile for predictable, fast startup.
    # -NonInteractive: never block waiting on a prompt.
    # -OutputFormat Text: plain text rather than CLIXML serialization.
    args = [pwsh, "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-Command", command]

    logger.info(f"Executing PowerShell command: {command} (cwd={cwd}, pwsh={pwsh})")
    try:
        result = await anyio.run_process(args, cwd=cwd, check=False)
        stdout = _decode(result.stdout).strip()
        stderr = _decode(result.stderr).strip()
        output = stdout
        if stderr:
            output += f"\n[stderr]\n{stderr}"
        output = output.strip() or "(no output)"
        logger.debug(f"PowerShell result: {output[:200]}")
        return output
    except Exception as e:
        logger.error(f"PowerShell command failed: {e}")
        return f"Error executing command: {e}"
