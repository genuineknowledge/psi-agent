from __future__ import annotations

from typing import Protocol, cast

_MISSING_ADAPTER_MESSAGE = (
    "[Fusion-Guard] Fusion-Guard Dolphin adapter is not installed. "
    "Install Fusion-Guard and ensure its dolphin/ adapter package is on PYTHONPATH."
)


class _SecureBash(Protocol):
    async def __call__(self, command: str, *, cwd: str | None = None) -> str: ...


async def bash(command: str, cwd: str = "") -> str:
    """Execute a bash command through the out-of-tree Fusion-Guard adapter.

    Args:
        command: The bash command to execute.
        cwd: Working directory. Defaults to the current Dolphin workspace.
    """
    try:
        # Keep this lazy so Dolphin can load the example before Fusion-Guard is installed.
        runner = __import__("fusion_guard_security.runner", fromlist=["secure_bash"])
    except ModuleNotFoundError as exc:
        if exc.name in {"fusion_guard_security", "fusion_guard_security.runner"}:
            return _MISSING_ADAPTER_MESSAGE
        raise

    secure_bash = cast(_SecureBash, runner.__dict__["secure_bash"])
    return await secure_bash(command, cwd=cwd or None)
