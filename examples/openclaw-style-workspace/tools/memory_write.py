"""Memory write tool — update the agent's persistent memory."""

from __future__ import annotations

import os

import anyio


async def tool(content: str, section: str = "", mode: str = "replace") -> str:
    """Write or update the agent's persistent memory in memory.md.

    Args:
        content: The content to write.
        section: Optional section heading (e.g. "Goals"). If provided, only
                 that section is updated; otherwise the whole file is replaced.
        mode: How to update: "replace" overwrites the section/file,
              "append" adds content at the end of the section/file.

    Returns:
        Success message or error message.
    """
    workspace_dir = anyio.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    memory_path = workspace_dir / "memory.md"

    if not section:
        # Whole-file write
        if mode == "append":
            existing = ""
            if await memory_path.exists():
                existing = await memory_path.read_text(encoding="utf-8", errors="replace")
            new_content = existing.rstrip() + "\n\n" + content if existing.strip() else content
        else:
            new_content = content
        await memory_path.write_text(new_content, encoding="utf-8")
        return f"[OK] Memory written ({len(new_content)} chars)"

    # Section-level update
    if await memory_path.exists():
        existing = await memory_path.read_text(encoding="utf-8", errors="replace")
    else:
        existing = ""

    heading = f"## {section}"
    if heading not in existing:
        # Append new section
        suffix = "\n\n" + heading + "\n\n" + content
        new_content = existing.rstrip() + suffix if existing.strip() else heading + "\n\n" + content
    else:
        lines = existing.splitlines(keepends=True)
        out: list[str] = []
        in_section = False
        inserted = False
        for line in lines:
            if line.rstrip() == heading:
                in_section = True
                out.append(line)
                if mode == "replace":
                    out.append(content + "\n")
                    inserted = True
                continue
            if in_section:
                if line.startswith("## ") and line.rstrip() != heading:
                    in_section = False
                    if mode == "append" and not inserted:
                        out.append(content + "\n\n")
                        inserted = True
                    out.append(line)
                    continue
                if mode == "replace" and inserted:
                    continue  # skip old section content
            out.append(line)

        if mode == "append" and in_section and not inserted:
            out.append(content + "\n")

        new_content = "".join(out)

    await memory_path.write_text(new_content, encoding="utf-8")
    return f"[OK] Memory section '{section}' updated"
