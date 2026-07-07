from __future__ import annotations

import inspect

import anyio

from psi_agent._yaml import parse_yaml_header


async def system_prompt_builder() -> str:
    """Build the system prompt for Fusion Memory tools."""
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"
    skills = await _load_workspace_skills(skills_dir)
    skills_text = "\n".join(skills) if skills else "(None)"

    return (
        "You have access to durable Fusion Memory via three tools:\n"
        "- memory_add: store a stable user preference, project fact, or decision\n"
        "- memory_search: retrieve raw evidence by keyword\n"
        "- memory_answer_context: retrieve a query-grounded context pack\n\n"
        "Use memory_answer_context when answering questions about the user's history, preferences, or prior context. "
        "Use memory_search when you need raw supporting evidence. "
        "Use memory_add only for durable, reusable facts, not transient conversation.\n\n"
        "At the start of each new interactive session, before ordinary task work "
        "and before the first use of Fusion Memory, "
        "check whether Fusion Memory persistence is already enabled and usable. "
        "It is usable only when the Fusion Memory service is reachable "
        "and the current session passive sync process is running. "
        "If the service is reachable and passive sync is running, do not ask again. "
        "If either check fails or no prior user consent is known, "
        "ask the user whether to enable Fusion Memory persistent memory. "
        "Do not wait for the first memory tool call. "
        "Explain that without installing and enabling Fusion Memory, you cannot remember across sessions "
        "and can only use current-session context. "
        "If the user agrees, use the fusion-memory-setup skill to initialize, start, "
        "check the Fusion Memory service, and start and verify passive sync. "
        "On Windows, use finite Fusion Memory CLI commands such as fusion-memory start --json "
        "and fusion-memory sync-haitun-history --background --json; the CLI creates the "
        "hidden/no-window service and watcher internally, so do not use pwsh, powershell.exe, "
        "PowerShell jobs, or shell backgrounding to keep memory processes alive. "
        "If the user declines, continue without memory and do not call Fusion Memory tools.\n\n"
        f"## Workspace Skills\nLocation: {skills_dir}\n\nAvailable:\n{skills_text}"
    )


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
