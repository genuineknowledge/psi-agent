from __future__ import annotations

from psi_agent.fusion_guard import runner


async def bash(command: str, cwd: str = "") -> str:
    """Execute a bash command through the Fusion-Guard safety adapter.

    Args:
        command: The bash command to execute.
        cwd: Working directory. Defaults to the current Dolphin workspace.
    """
    return await runner.secure_bash(command, cwd=cwd or None)
