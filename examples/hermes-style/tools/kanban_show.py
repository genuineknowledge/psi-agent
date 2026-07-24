"""Kanban board tool — show and manage kanban tasks."""

from __future__ import annotations


async def tool(task_id: str = "") -> str:
    """Show kanban board status or a specific task.

    Args:
        task_id: Optional task ID to show details for. If empty, shows
            the full board overview.

    Returns:
        Board or task information, or a not-available message.
    """
    return (
        "kanban_show is not available in this workspace. "
        "No kanban board has been configured. "
        "To enable kanban functionality, connect this workspace to a "
        "kanban backend and replace this stub with real implementation."
    )
