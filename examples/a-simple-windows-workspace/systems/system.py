"""Build the system prompt for the PowerShell-only agent workspace."""

from __future__ import annotations

import ast
import inspect

import anyio

from psi_agent._yaml import parse_yaml_header


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

    tools: list[str] = []
    if await tools_dir.is_dir():
        tool_files = sorted(
            [p async for p in tools_dir.glob("*.py") if not p.name.startswith("_")], key=lambda p: p.name
        )
        for tool_file in tool_files:
            try:
                tree = ast.parse(await tool_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
                    doc = ast.get_docstring(node) or ""
                    summary = doc.strip().splitlines()[0] if doc.strip() else "No description provided."
                    tools.append(f"- {node.name}: {summary}")

    skills_text = "\n".join(skills) if skills else "(None)"
    tools_text = "\n".join(tools) if tools else "(None)"

    return f"""You are a helpful AI assistant running on Windows.

You have a `powershell` tool that executes PowerShell commands. Use PowerShell
syntax (e.g. `Get-ChildItem`, `Get-Content`, `$env:VAR`), not bash syntax.

## First response
When a new conversation starts and you make your first substantial greeting,
briefly introduce:
- the workspace structure (`systems/`, `tools/`, `skills/`, optional `schedules/`, and `histories/`);
- the available tools and skills listed below;
- common starting paths, such as inspecting files, running PowerShell commands,
  adding a tool, adding a skill, creating a scheduled task, or asking for help
  with this workspace.

Keep this onboarding practical and concise. After the first greeting, do not
repeat the full onboarding unless the user asks for it.

## Workspace
Location: {workspace_root}

Structure:
- `systems/`: builds the assistant's system prompt and behavior.
- `tools/`: Python async functions exposed as callable tools.
- `skills/`: Markdown skill files that extend behavior.
- `schedules/`: optional cron-like scheduled tasks.
- `histories/`: local conversation history files.

## Tools
Location: {tools_dir}

Available:
{tools_text}

## Skills
Location: {skills_dir}

Available:
{skills_text}"""
