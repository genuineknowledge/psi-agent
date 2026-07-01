"""Build the system prompt for the bash-only agent workspace."""

from __future__ import annotations

import inspect
from pathlib import Path

import anyio

from psi_agent._yaml import parse_yaml_header
from psi_agent.session.tool_registry import ToolRegistry


async def system_prompt_builder() -> str:
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"
    tools_dir = workspace_root / "tools"

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

    # Enumerate tools through the same loader the session uses at runtime, so
    # the prompt always reflects the tools actually exposed to the model. This
    # lets the agent introspect and describe its own capabilities.
    registry = await ToolRegistry.load(Path(str(tools_dir)))
    tools = [f"- {tool.name}: {tool.description}" for tool in registry.tools.values()]
    tools_text = "\n".join(sorted(tools)) if tools else "(None)"

    return f"""You are a helpful AI assistant.

When the user asks what you can do, which tools you have, or how you are
structured, answer from the Tools and Skills listed below.

## Workspace
Location: {workspace_root}

## Tools
Location: {tools_dir}

Available:
{tools_text}

## Skills
Location: {skills_dir}

Available:
{skills_text}"""
