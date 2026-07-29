"""Build the system prompt for the tb2-specific agent workspace.

A lightweight workspace that exposes a curated set of domain skills on top of
basic file/shell tools. No flow, memory, curator, or scheduling components —
just skills plus the tools needed to act on them.
"""

from __future__ import annotations

import inspect
from typing import Any

import anyio

from psi_agent._yaml import parse_yaml_header


async def system_prompt_builder() -> str:
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"

    universal = ""
    skills: list[str] = []
    if await skills_dir.is_dir():
        skill_dirs = sorted([p async for p in skills_dir.iterdir()], key=lambda p: p.name)
        for skill_dir in skill_dirs:
            if not await skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not await skill_md.exists():
                continue
            header, body = parse_yaml_header(await skill_md.read_text(encoding="utf-8"))
            if not header or not header.get("name"):
                continue
            name = header["name"]
            description = header.get("description", "")
            # `_universal` is always-on working discipline; inline its full body.
            if name == "_universal":
                universal = body.strip()
                continue
            skills.append(f"- {name}: {description}")

    skills_text = "\n".join(skills) if skills else "(none)"
    universal_block = f"\n## Universal working discipline\n\n{universal}\n" if universal else ""

    return f"""You are a capable AI agent working in a skills-focused workspace.

You have a curated set of domain skills and basic tools (bash, read, write,
edit). For a task, consult the relevant skill for domain guidance, then use the
tools to carry it out. Read a skill's full SKILL.md before relying on it.

## Workspace
Location: {workspace_root}

## Tools
- bash: run shell commands
- read / write / edit: work with files

## Skills
Location: {skills_dir}

Available:
{skills_text}
{universal_block}"""


async def compact_history(history: list[dict[str, Any]], complete_fn) -> str:
    """Summarize older conversation turns via LLM, keeping recent turns verbatim.

    Returns the summary string with recent turns appended; the framework
    merges the whole result into the system prompt.
    """
    if len(history) <= 6:
        return ""

    recent_count = 4
    older = history[:-recent_count]
    recent = history[-recent_count:]

    parts: list[str] = []
    for msg in older:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
            parts.append(f"[{role}]: {content}")

    recent_text = ""
    recent_parts: list[str] = []
    for msg in recent:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
            recent_parts.append(f"[{role}]: {content}")
    if recent_parts:
        recent_text = "\n[Recent turns]\n" + "\n".join(recent_parts)

    if not parts:
        return recent_text

    summary_prompt = [
        {
            "role": "system",
            "content": (
                "Summarize the following conversation concisely. "
                "Preserve all key facts, decisions, task context, file paths, "
                "and information the user or assistant explicitly mentioned. "
                "Do not omit anything that could be needed later."
            ),
        },
        {"role": "user", "content": "Summarize:\n\n" + "\n".join(parts)},
    ]

    try:
        summary = await complete_fn(summary_prompt)
        return summary + "\n" + recent_text
    except Exception:
        return "\n".join(parts) + "\n" + recent_text
