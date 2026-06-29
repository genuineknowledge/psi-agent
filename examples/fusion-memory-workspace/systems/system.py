from __future__ import annotations

import inspect
from pathlib import Path

from psi_agent._yaml import parse_yaml_header


async def system_prompt_builder() -> str:
    """Build the system prompt for Fusion Memory tools."""
    current_file = Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    skills_dir = workspace_root / "skills"
    skills = _load_workspace_skills(skills_dir)
    skills_text = "\n".join(skills) if skills else "(None)"

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
        f"## Workspace Skills\nLocation: {skills_dir}\n\nAvailable:\n{skills_text}"
    )


def _load_workspace_skills(skills_dir: Path) -> list[str]:
    skills: list[str] = []
    if not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        header, _ = parse_yaml_header(skill_md.read_text())
        if header and header.get("name") and header.get("description"):
            skills.append(f"- {header['name']}: {header['description']}")
    return skills
