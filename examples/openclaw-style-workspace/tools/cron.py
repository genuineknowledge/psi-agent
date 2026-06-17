"""Cron tool — list and inspect workspace schedule entries."""

from __future__ import annotations

import os

import anyio


def _schedules_dir() -> anyio.Path:
    workspace = os.environ.get("PSI_WORKSPACE_DIR", ".")
    return anyio.Path(workspace) / "schedules"


async def cron(action: str = "list", name: str = "") -> str:
    """List or show workspace schedule (cron) entries from the schedules/ directory.

    Args:
        action: "list" to show all schedules, "show" to display a specific one.
        name: Schedule name (required for "show").

    Returns:
        Schedule names and cron expressions, or full TASK.md content.
    """
    sched_dir = _schedules_dir()

    if not await sched_dir.exists():
        return "No schedules/ directory found in workspace."

    if action == "list":
        entries: list[str] = []
        async for entry in sched_dir.iterdir():
            if not await entry.is_dir():
                continue
            task_file = entry / "TASK.md"
            if not await task_file.exists():
                continue
            content = await task_file.read_text()
            cron = _extract_frontmatter_field(content, "cron")
            cron_display = cron or "(no cron)"
            entries.append(f"- {entry.name}: {cron_display}")

        if not entries:
            return "No schedule entries found."
        entries.sort()
        return "Schedules:\n" + "\n".join(entries)

    if action == "show":
        if not name:
            return "[Error] 'name' is required for action 'show'."
        task_file = sched_dir / name / "TASK.md"
        if not await task_file.exists():
            return f"[Error] Schedule '{name}' not found."
        return await task_file.read_text()

    return f"[Error] Unknown action '{action}'. Use: list, show."


def _extract_frontmatter_field(content: str, field: str) -> str:
    """Extract a field value from YAML frontmatter."""
    in_frontmatter = False
    for line in content.splitlines():
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter and line.startswith(f"{field}:"):
            return line[len(field) + 1:].strip().strip('"').strip("'")
    return ""
