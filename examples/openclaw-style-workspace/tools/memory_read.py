"""Memory read tool for the agent's persistent memory."""

from __future__ import annotations

import anyio


async def tool(section: str = "") -> str:
    """Read the agent's persistent memory from memory.md.

    Args:
        section: Optional section heading to read (e.g. "Goals"). If empty,
                 returns the full memory file.

    Returns:
        Memory contents, or a message if no memory exists yet.
    """
    workspace_dir = anyio.Path(__file__).parent.parent
    memory_path = workspace_dir / "memory.md"

    if not await memory_path.exists():
        return "[Memory is empty; no memory.md found]"

    content = await memory_path.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return "[Memory is empty]"

    if not section:
        return content

    # Extract the requested section
    lines = content.splitlines()
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

    if not result:
        return f"[Section '{section}' not found in memory]"
    return "\n".join(result)
