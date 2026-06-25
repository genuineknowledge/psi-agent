from __future__ import annotations

from psi_agent.fusion_guard.runner import secure_bash


async def bash(command: str, cwd: str | None = None) -> str:
    """Execute a bash command through the Fusion-Guard safety adapter.

    Args:
        command: The bash command to execute.
        cwd: Working directory. Defaults to the current Dolphin workspace.
    """
    return await secure_bash(command, cwd=cwd)
