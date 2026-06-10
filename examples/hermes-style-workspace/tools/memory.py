"""Persistent memory tool — read, write, append, and clear workspace/memory.md."""

from __future__ import annotations

import os
import pathlib

import anyio


def _memory_path() -> anyio.Path:
    """Resolve workspace/memory.md path.

    Checks WORKSPACE_DIR env var first, then falls back to two levels above
    this file (workspace root).

    Returns:
        anyio.Path pointing to workspace/memory.md.
    """
    ws = os.environ.get("WORKSPACE_DIR", "")
    if ws:
        return anyio.Path(ws) / "memory.md"
    return anyio.Path(str(pathlib.Path(__file__).parents[1])) / "memory.md"


async def tool(action: str = "read", content: str = "") -> str:
    """Read, write, append, or clear persistent memory in workspace/memory.md.

    Args:
        action: One of ``"read"``, ``"write"``, ``"append"``, or ``"clear"``.
            - ``"read"``: Return current memory contents.
            - ``"write"``: Overwrite entire memory file with ``content``.
            - ``"append"``: Append ``content`` to existing memory (adds newline separator).
            - ``"clear"``: Clear the memory file entirely.
        content: Content to write or append (only used for write/append actions).

    Returns:
        Current memory contents on read, confirmation message on write/append/clear,
        or an error message for unknown actions.
    """
    memory_path = _memory_path()

    if action == "read":
        if not await memory_path.exists():
            return "(memory is empty)"
        text = (await memory_path.read_text(encoding="utf-8")).strip()
        return text if text else "(memory is empty)"

    if action == "write":
        await memory_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to tmp file then rename
        tmp_path = memory_path.parent / (memory_path.name + ".tmp")
        await tmp_path.write_text(content, encoding="utf-8")
        await tmp_path.rename(memory_path)
        return "Memory saved."

    if action == "append":
        await memory_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if await memory_path.exists():
            existing = (await memory_path.read_text(encoding="utf-8")).rstrip()
        separator = "\n\n" if existing else ""
        new_content = existing + separator + content
        tmp_path = memory_path.parent / (memory_path.name + ".tmp")
        await tmp_path.write_text(new_content, encoding="utf-8")
        await tmp_path.rename(memory_path)
        return "Memory appended."

    if action == "clear":
        if await memory_path.exists():
            tmp_path = memory_path.parent / (memory_path.name + ".tmp")
            await tmp_path.write_text("", encoding="utf-8")
            await tmp_path.rename(memory_path)
        return "Memory cleared."

    return f"Unknown action: {action!r}. Use 'read', 'write', 'append', or 'clear'."
