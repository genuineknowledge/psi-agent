"""Manage workspace scheduled tasks (schedules/<name>/TASK.md)."""

from __future__ import annotations

import os
import pathlib
import re
from contextlib import suppress
from datetime import UTC, datetime

import anyio
import yaml
from croniter import croniter


def _schedules_dir() -> anyio.Path:
    workspace_dir = os.environ.get("WORKSPACE_DIR", "")
    if workspace_dir:
        return anyio.Path(workspace_dir) / "schedules"
    return anyio.Path(str(pathlib.Path(__file__).resolve().parents[1])) / "schedules"


def _validate_schedule_name(schedule_name: str) -> str | None:
    if not schedule_name.strip():
        return "Invalid schedule name: name cannot be empty."
    if "/" in schedule_name or "\\" in schedule_name:
        return f"Invalid schedule name {schedule_name!r}: must not contain path separators."
    if ".." in schedule_name:
        return f"Invalid schedule name {schedule_name!r}: must not contain '..'."
    if "\x00" in schedule_name:
        return f"Invalid schedule name {schedule_name!r}: must not contain null characters."
    if not re.fullmatch(r"[A-Za-z0-9_-]+", schedule_name):
        return f"Invalid schedule name {schedule_name!r}: only letters, digits, hyphens, and underscores are allowed."
    return None


def _validate_cron(cron: str) -> str | None:
    if not cron.strip():
        return "Invalid cron: expression cannot be empty."
    try:
        croniter(cron)
    except Exception as e:  # croniter raises assorted error types
        return f"Invalid cron expression {cron!r}: {e}"
    return None


def _parse_header(content: str) -> tuple[dict[str, object], str]:
    """Parse the YAML front matter the same way the schedule registry does."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}, content
    try:
        header = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, content
    if not isinstance(header, dict):
        return {}, content
    return header, content[match.end() :]


def _format_task_document(
    *,
    schedule_name: str,
    cron: str,
    description: str,
    content: str,
    created_by: str = "agent",
    created_at: str = "",
    updated_at: str = "",
) -> str:
    lines = [
        "---",
        f"name: {schedule_name}",
        f"description: {description or '(no description)'}",
        # Quote the cron value — a bare ``*/30 * * * *`` is invalid YAML.
        f'cron: "{cron}"',
        f"created_by: {created_by}",
    ]
    if created_at:
        lines.append(f"created_at: {created_at}")
    if updated_at:
        lines.append(f"updated_at: {updated_at}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + content.strip() + "\n"


async def _atomic_write(path: anyio.Path, content: str) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(content, encoding="utf-8")
    # replace(), not rename() — os.rename can't overwrite an existing file on Windows.
    await tmp.replace(path)


async def schedule_manage(
    action: str = "list",
    schedule_name: str = "",
    cron: str = "",
    description: str = "",
    content: str = "",
) -> str:
    """Create, patch, view, list, or delete workspace scheduled tasks.

    Scheduled tasks live in ``schedules/<name>/TASK.md``. Each file has a
    YAML header with ``name`` and ``cron`` (a 5-field cron expression) plus
    the task body the agent runs when the schedule fires.

    Args:
        action: One of "list", "view", "create", "patch", or "delete".
        schedule_name: Schedule directory name for view/create/patch/delete.
        cron: Cron expression for create, or to change it on patch.
        description: One-line description used on create/patch.
        content: TASK.md body (the instructions run on each fire) for create/patch.

    Returns:
        A result message, list output, or TASK.md content.
    """
    schedules_dir = _schedules_dir()
    action = action.strip().lower()

    if action == "list":
        if not await schedules_dir.exists():
            return "No schedules found."

        entries: list[str] = []
        async for task_dir in schedules_dir.iterdir():
            if not await task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            task_md = task_dir / "TASK.md"
            if not await task_md.exists():
                continue

            raw = await task_md.read_text(encoding="utf-8", errors="replace")
            header, _body = _parse_header(raw)
            name = header.get("name") or task_dir.name
            desc = header.get("description") or "(no description)"
            cron_expr = header.get("cron") or "(no cron)"
            tag = " [agent]" if header.get("created_by") == "agent" else ""
            entries.append(f"- {name} [{cron_expr}]{tag}: {desc}")

        return "Schedules:\n" + "\n".join(sorted(entries)) if entries else "No schedules found."

    if action == "view":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        task_md = schedules_dir / schedule_name / "TASK.md"
        if not await task_md.exists():
            return f"[Error] Schedule not found: {schedule_name!r}"
        return await task_md.read_text(encoding="utf-8", errors="replace")

    if action == "create":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        if err := _validate_cron(cron):
            return f"[Error] {err}"
        task_dir = schedules_dir / schedule_name
        if await task_dir.exists():
            return f"[Error] Schedule already exists: {schedule_name!r}. Use action='patch' to update it."

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        await _atomic_write(
            task_dir / "TASK.md",
            _format_task_document(
                schedule_name=schedule_name,
                cron=cron,
                description=description,
                content=content,
                created_at=now,
            ),
        )
        return f"Schedule created: {schedule_name!r} (cron: {cron!r})"

    if action == "patch":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        task_md = schedules_dir / schedule_name / "TASK.md"
        if not await task_md.exists():
            return f"[Error] Schedule not found: {schedule_name!r}"

        raw = await task_md.read_text(encoding="utf-8", errors="replace")
        header, body = _parse_header(raw)

        next_cron = cron.strip() or str(header.get("cron") or "")
        if err := _validate_cron(next_cron):
            return f"[Error] {err}"

        await _atomic_write(
            task_md,
            _format_task_document(
                schedule_name=str(header.get("name") or schedule_name),
                cron=next_cron,
                description=description or str(header.get("description") or ""),
                content=content.strip() or body.strip(),
                created_by=str(header.get("created_by") or "agent"),
                created_at=str(header.get("created_at") or ""),
                updated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        return f"Schedule patched: {schedule_name!r} (cron: {next_cron!r})"

    if action == "delete":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        task_dir = schedules_dir / schedule_name
        task_md = task_dir / "TASK.md"
        if not await task_md.exists():
            return f"[Error] Schedule not found: {schedule_name!r}"

        await task_md.unlink()
        # Remove the now-empty task directory; ignore if other files remain.
        with suppress(OSError):
            await task_dir.rmdir()
        return f"Schedule deleted: {schedule_name!r}"

    return "[Error] Unknown action. Use 'list', 'view', 'create', 'patch', or 'delete'."
