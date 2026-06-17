"""Update plan tool — read and write plan.md in the workspace directory."""

from __future__ import annotations

import os

import anyio


def _plan_path() -> anyio.Path:
    workspace = os.environ.get("PSI_WORKSPACE_DIR", ".")
    return anyio.Path(workspace) / "plan.md"


async def update_plan(action: str = "read", content: str = "") -> str:
    """Read or write the workspace plan.md file.

    Args:
        action: "read" to get current plan, "write" to overwrite with new content.
        content: New plan content (required for "write").

    Returns:
        Plan content for "read", or confirmation for "write".
    """
    plan_file = _plan_path()

    if action == "read":
        if not await plan_file.exists():
            return "(No plan.md found. Use action='write' to create one.)"
        return await plan_file.read_text()

    if action == "write":
        if not content.strip():
            return "[Error] 'content' must not be empty for action 'write'."
        await plan_file.write_text(content)
        return f"plan.md written ({len(content)} chars)."

    return f"[Error] Unknown action '{action}'. Use: read, write."
