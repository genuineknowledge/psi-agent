"""Todo tool — persistent todo list stored in workspace todo.md."""

from __future__ import annotations

import os

import anyio


def _todo_path() -> anyio.Path:
    workspace = os.environ.get("PSI_WORKSPACE_DIR", ".")
    return anyio.Path(workspace) / "todo.md"


async def todo(
    action: str,
    text: str = "",
    index: int = 0,
) -> str:
    """Manage a persistent todo list stored in todo.md.

    Args:
        action: One of "add", "list", "complete", "clear".
        text: Item text (required for "add").
        index: 1-based item index (required for "complete").

    Returns:
        Confirmation message or current list contents.
    """
    todo_file = _todo_path()

    if action == "add":
        if not text:
            return "[Error] 'text' is required for action 'add'."
        current = await todo_file.read_text() if await todo_file.exists() else ""
        items = _parse_items(current)
        items.append(text.strip())
        await todo_file.write_text(_render(items))
        return f"Added: {text.strip()} (total: {len(items)} items)"

    if action == "list":
        if not await todo_file.exists():
            return "Todo list is empty."
        content = await todo_file.read_text()
        items = _parse_items(content)
        if not items:
            return "Todo list is empty."
        return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))

    if action == "complete":
        if not await todo_file.exists():
            return "Todo list is empty."
        content = await todo_file.read_text()
        items = _parse_items(content)
        if not 1 <= index <= len(items):
            return f"[Error] Invalid index {index}. List has {len(items)} items."
        removed = items.pop(index - 1)
        await todo_file.write_text(_render(items))
        return f"Completed: {removed} ({len(items)} items remaining)"

    if action == "clear":
        await todo_file.write_text("")
        return "Todo list cleared."

    return f"[Error] Unknown action '{action}'. Use: add, list, complete, clear."


def _parse_items(content: str) -> list[str]:
    lines = [line.strip() for line in content.splitlines()]
    return [line.lstrip("- ").strip() for line in lines if line and not line.startswith("#")]


def _render(items: list[str]) -> str:
    if not items:
        return ""
    return "\n".join(f"- {item}" for item in items) + "\n"
