from __future__ import annotations

import inspect
from pathlib import Path

import anyio

from psi_agent._yaml import parse_yaml_header
from psi_agent.session.tool_registry import ToolRegistry


async def system_prompt_builder() -> str:
    """Build the system prompt for Fusion Memory tools."""
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"
    tools_dir = workspace_root / "tools"
    skills = await _load_workspace_skills(skills_dir)
    skills_text = "\n".join(skills) if skills else "(None)"
    tools = await _load_workspace_tools(tools_dir)
    tools_text = "\n".join(tools) if tools else "(None)"

    return (
        "You have access to durable Fusion Memory via three tools:\n"
        "- memory_add: store a stable user preference, project fact, or decision\n"
        "- memory_search: retrieve raw evidence by keyword\n"
        "- memory_answer_context: retrieve a query-grounded context pack\n\n"
        "Use memory_answer_context when answering questions about the user's history, preferences, or prior context. "
        "Use memory_search when you need raw supporting evidence. "
        "Use memory_add only for durable, reusable facts, not transient conversation.\n\n"
        "Before the first use of Fusion Memory, use the fusion-memory-setup skill to initialize, start, "
        "and check the Fusion Memory service.\n\n"
        "When the user asks what you can do, which tools you have, or how you are structured, "
        "answer from the Tools and Skills listed below.\n\n"
        f"## Tools\nLocation: {tools_dir}\n\nAvailable:\n{tools_text}\n\n"
        f"## Workspace Skills\nLocation: {skills_dir}\n\nAvailable:\n{skills_text}"
    )


async def _load_workspace_tools(tools_dir: anyio.Path) -> list[str]:
    # Enumerate tools through the same loader the session uses at runtime, so
    # the prompt always reflects the tools actually exposed to the model.
    registry = await ToolRegistry.load(Path(str(tools_dir)))
    return sorted(f"- {tool.name}: {tool.description}" for tool in registry.tools.values())


async def _load_workspace_skills(skills_dir: anyio.Path) -> list[str]:
    skills: list[str] = []
    if not await skills_dir.is_dir():
        return skills
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
    return skills
