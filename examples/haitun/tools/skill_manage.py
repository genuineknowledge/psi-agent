"""Manage workspace skills."""

from __future__ import annotations

import os
import pathlib
import re
from datetime import UTC, datetime

import anyio


def _skills_dir() -> anyio.Path:
    """Skills live on the agent package, not the user open-folder."""
    try:
        from _background_process_registry import resolve_agent

        return resolve_agent() / "skills"
    except ImportError:
        env = os.environ.get("PSI_AGENT_DIR", "").strip()
        if env:
            return anyio.Path(env) / "skills"
        return anyio.Path(str(pathlib.Path(__file__).resolve().parents[1])) / "skills"


def _validate_skill_name(skill_name: str) -> str | None:
    if not skill_name.strip():
        return "Invalid skill name: name cannot be empty."
    if "/" in skill_name or "\\" in skill_name:
        return f"Invalid skill name {skill_name!r}: must not contain path separators."
    if ".." in skill_name:
        return f"Invalid skill name {skill_name!r}: must not contain '..'."
    if "\x00" in skill_name:
        return f"Invalid skill name {skill_name!r}: must not contain null characters."
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill_name):
        return f"Invalid skill name {skill_name!r}: only letters, digits, hyphens, and underscores are allowed."
    return None


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---", 4)
    if end == -1:
        return {}, content

    frontmatter: dict[str, str] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("\"'")
    return frontmatter, content[end + 4 :].lstrip("\n")


async def _atomic_write(path: anyio.Path, content: str) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(content, encoding="utf-8")
    await tmp.rename(path)


async def skill_manage(
    action: str = "list",
    skill_name: str = "",
    content: str = "",
    category: str = "general",
    description: str = "",
) -> str:
    """Create, patch, view, or list workspace skills.

    Args:
        action: One of "list", "view", "create", or "patch".
        skill_name: Skill directory name for view/create/patch.
        content: Full SKILL.md body content for create/patch, excluding frontmatter.
        category: Skill category used when creating a skill.
        description: Short skill description used when creating a skill.

    Returns:
        A result message, list output, or SKILL.md content.
    """
    skills_dir = _skills_dir()
    action = action.strip().lower()

    if action == "list":
        if not await skills_dir.exists():
            return "No skills found."

        entries: list[str] = []
        async for skill_dir in skills_dir.iterdir():
            if not await skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_md = skill_dir / "SKILL.md"
            if not await skill_md.exists():
                continue

            raw = await skill_md.read_text(encoding="utf-8", errors="replace")
            frontmatter, _body = _parse_frontmatter(raw)
            name = frontmatter.get("name") or skill_dir.name
            desc = frontmatter.get("description") or "(no description)"
            cat = frontmatter.get("category") or "general"
            tag = " [agent]" if frontmatter.get("created_by") == "agent" else ""
            entries.append(f"- {name} ({cat}){tag}: {desc}")

        return "Skills:\n" + "\n".join(sorted(entries)) if entries else "No skills found."

    if action == "view":
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not await skill_md.exists():
            return f"[Error] Skill not found: {skill_name!r}"
        return await skill_md.read_text(encoding="utf-8", errors="replace")

    if action == "create":
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        skill_dir = skills_dir / skill_name
        if await skill_dir.exists():
            return f"[Error] Skill already exists: {skill_name!r}. Use action='patch' to update agent-created skills."

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        frontmatter = "\n".join(
            [
                "---",
                f"name: {skill_name}",
                f"description: {description or '(no description)'}",
                f"category: {category or 'general'}",
                "created_by: agent",
                f"created_at: {now}",
                "---",
            ]
        )
        await _atomic_write(skill_dir / "SKILL.md", frontmatter + "\n\n" + content.strip() + "\n")
        return f"Skill created: {skill_name!r}"

    if action == "patch":
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not await skill_md.exists():
            return f"[Error] Skill not found: {skill_name!r}"

        raw = await skill_md.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _parse_frontmatter(raw)
        if frontmatter.get("created_by") != "agent":
            return f"[Error] Skill {skill_name!r} is user-authored or unmanaged; patch is read-only."

        frontmatter["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = ["---", *(f"{key}: {value}" for key, value in frontmatter.items()), "---"]
        await _atomic_write(skill_md, "\n".join(lines) + "\n\n" + content.strip() + "\n")
        return f"Skill patched: {skill_name!r}"

    return "[Error] Unknown action. Use 'list', 'view', 'create', or 'patch'."
