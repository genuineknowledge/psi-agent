"""Build the system prompt for the PowerShell-only agent workspace."""

from __future__ import annotations

import inspect

import anyio

from psi_agent._yaml import parse_yaml_header

# Context compaction is engine behavior, not product expression: this block was
# byte-for-byte identical in 11 of the 12 example workspaces, so a fix had to be
# made eleven times.  It now lives in the kernel.  Re-exported rather than
# imported inline because the hook lookup is `getattr(module, "compact_history")`
# -- the name has to be resolvable as a module attribute here.  A workspace that
# needs different behavior defines `compact_history` itself further down; the
# later binding wins.
from psi_agent.session._compaction import (  # noqa: F401
    RECENT_TURNS_KEPT_VERBATIM,
    SUMMARIZE_TASK,
    SUMMARY_MAX_CHARS,
    TRANSCRIPT_IS_DATA,
    _cap_summary,
    _escape_transcript,
    compact_history,
)


async def system_prompt_builder() -> str:
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"

    skills: list[str] = []
    if await skills_dir.is_dir():
        skill_dirs = sorted([p async for p in skills_dir.iterdir()], key=lambda p: p.name)
        for skill_dir in skill_dirs:
            if not await skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not await skill_md.exists():
                continue
            header, _ = parse_yaml_header(await skill_md.read_text(encoding="utf-8"))
            if header and header.get("name") and header.get("description"):
                skills.append(f"- {header['name']}: {header['description']}")

    skills_text = "\n".join(skills) if skills else "(None)"

    return f"""You are a helpful AI assistant running on Windows.

You have a `powershell` tool that executes PowerShell commands. Use PowerShell
syntax (e.g. `Get-ChildItem`, `Get-Content`, `$env:VAR`), not bash syntax.

## Workspace
Location: {workspace_root}

## Skills
Location: {skills_dir}

Available:
{skills_text}"""
