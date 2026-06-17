"""Persistent memory tool — read, write, append, clear, and section-level updates for workspace/memory.md."""

from __future__ import annotations

import os
import pathlib

import anyio


def _memory_path() -> anyio.Path:
    ws = os.environ.get("WORKSPACE_DIR", "")
    if ws:
        return anyio.Path(ws) / "memory.md"
    return anyio.Path(str(pathlib.Path(__file__).parents[1])) / "memory.md"


async def memory(
    action: str = "read",
    content: str = "",
    section: str = "",
) -> str:
    """Read, write, append, clear, or update a section of workspace/memory.md.

    Args:
        action: One of ``"read"``, ``"write"``, ``"append"``, or ``"clear"``.
            - ``"read"``: Return memory contents. If ``section`` is given, return
              only that ``## <section>`` block.
            - ``"write"``: Overwrite entire file with ``content``. If ``section``
              is given, replace only that ``## <section>`` block (or append it).
            - ``"append"``: Append ``content`` to file. If ``section`` is given,
              append inside that ``## <section>`` block (or create it).
            - ``"clear"``: Clear the memory file entirely.
        content: Content to write or append (used for write/append actions).
        section: Optional ``##`` section heading (without ``##``), e.g. ``"Goals"``.

    Returns:
        Memory contents on read, confirmation message on write/append/clear,
        or an error message for unknown actions.
    """
    memory_path = _memory_path()

    # ------------------------------------------------------------------ read
    if action == "read":
        if not await memory_path.exists():
            return "(memory is empty)"
        text = (await memory_path.read_text(encoding="utf-8")).strip()
        if not text:
            return "(memory is empty)"
        if not section:
            return text
        # Extract section block
        lines = text.splitlines()
        in_section = False
        result: list[str] = []
        for line in lines:
            if line.startswith("## ") and section.lower() in line.lower():
                in_section = True
                result.append(line)
                continue
            if in_section:
                if line.startswith("## ") and section.lower() not in line.lower():
                    break
                result.append(line)
        return "\n".join(result) if result else f"[Section '{section}' not found in memory]"

    # ------------------------------------------------------------------ write
    if action == "write":
        await memory_path.parent.mkdir(parents=True, exist_ok=True)
        if not section:
            new_content = content
        else:
            existing = ""
            if await memory_path.exists():
                existing = await memory_path.read_text(encoding="utf-8")
            heading = f"## {section}"
            if heading not in existing:
                suffix = "\n\n" + heading + "\n\n" + content
                new_content = existing.rstrip() + suffix if existing.strip() else heading + "\n\n" + content
            else:
                lines = existing.splitlines(keepends=True)
                out: list[str] = []
                in_sec = False
                inserted = False
                for line in lines:
                    if line.rstrip() == heading:
                        in_sec = True
                        out.append(line)
                        out.append(content + "\n")
                        inserted = True
                        continue
                    if in_sec and inserted:
                        if line.startswith("## ") and line.rstrip() != heading:
                            in_sec = False
                            out.append(line)
                        continue
                    out.append(line)
                new_content = "".join(out)
        tmp = memory_path.parent / (memory_path.name + ".tmp")
        await tmp.write_text(new_content, encoding="utf-8")
        await tmp.rename(memory_path)
        return f"Memory {'section ' + repr(section) + ' ' if section else ''}saved."

    # ------------------------------------------------------------------ append
    if action == "append":
        await memory_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if await memory_path.exists():
            existing = (await memory_path.read_text(encoding="utf-8")).rstrip()
        if not section:
            separator = "\n\n" if existing else ""
            new_content = existing + separator + content
        else:
            heading = f"## {section}"
            if heading not in existing:
                suffix = "\n\n" + heading + "\n\n" + content
                new_content = existing + suffix if existing else heading + "\n\n" + content
            else:
                lines = existing.splitlines(keepends=True)
                out2: list[str] = []
                in_sec2 = False
                for line in lines:
                    out2.append(line)
                    if line.rstrip() == heading:
                        in_sec2 = True
                        continue
                    if in_sec2 and line.startswith("## ") and line.rstrip() != heading:
                        out2.insert(-1, content + "\n\n")
                        in_sec2 = False
                if in_sec2:
                    out2.append(content + "\n")
                new_content = "".join(out2)
        tmp = memory_path.parent / (memory_path.name + ".tmp")
        await tmp.write_text(new_content, encoding="utf-8")
        await tmp.rename(memory_path)
        return f"Memory {'section ' + repr(section) + ' ' if section else ''}appended."

    # ------------------------------------------------------------------ clear
    if action == "clear":
        if await memory_path.exists():
            tmp = memory_path.parent / (memory_path.name + ".tmp")
            await tmp.write_text("", encoding="utf-8")
            await tmp.rename(memory_path)
        return "Memory cleared."

    return f"Unknown action: {action!r}. Use 'read', 'write', 'append', or 'clear'."
