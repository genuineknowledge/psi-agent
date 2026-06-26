"""Skill management tool — create, patch, view, and list workspace skills."""

from __future__ import annotations

import os
import pathlib
import re
from datetime import UTC, datetime

import anyio


def _skills_dir() -> anyio.Path:
    """Resolve workspace/skills/ path.

    Checks WORKSPACE_DIR env var first, then falls back to two levels above
    this file (workspace root).

    Returns:
        anyio.Path pointing to workspace/skills/.
    """
    ws = os.environ.get("WORKSPACE_DIR", "")
    if ws:
        return anyio.Path(ws) / "skills"
    return anyio.Path(str(pathlib.Path(__file__).parents[1])) / "skills"


def _validate_skill_name(skill_name: str) -> str | None:
    """Validate a skill name for filesystem safety.

    Args:
        skill_name: The skill directory name to validate.

    Returns:
        Error message string if invalid, or None if valid.
    """
    if not skill_name or not skill_name.strip():
        return "Invalid skill name: name cannot be empty."
    if "/" in skill_name or "\\" in skill_name:
        return f"Invalid skill name {skill_name!r}: must not contain '/' or '\\'."
    if ".." in skill_name:
        return f"Invalid skill name {skill_name!r}: must not contain '..'."
    if "\x00" in skill_name:
        return f"Invalid skill name {skill_name!r}: must not contain null characters."
    if not re.match(r"^[a-zA-Z0-9_\-]+$", skill_name):
        return f"Invalid skill name {skill_name!r}: only letters, digits, hyphens, and underscores are allowed."
    return None


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from SKILL.md content.

    Args:
        content: Raw SKILL.md file content.

    Returns:
        Tuple of (frontmatter_dict, body_text).
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip("'\"")
    return fm, body


async def _atomic_write(path: anyio.Path, content: str) -> None:
    """Write content to path atomically using a temp file + rename.

    Args:
        path: Destination file path.
        content: Text content to write.
    """
    tmp = path.parent / (path.name + ".tmp")
    await tmp.write_text(content, encoding="utf-8")
    await tmp.rename(path)


async def tool(
    action: str = "list",
    skill_name: str = "",
    content: str = "",
    category: str = "general",
    description: str = "",
) -> str:
    """Manage workspace skills: create, patch, view, or list skills.

    Args:
        action: One of ``"create"``, ``"patch"``, ``"view"``, or ``"list"``.
            - ``"create"``: Create a new skill with SKILL.md.
            - ``"patch"``: Update an existing skill's body content.
            - ``"view"``: Return the full content of a skill's SKILL.md.
            - ``"list"``: List all skills with name, description, and category.
        skill_name: Skill directory name (required for create/patch/view).
            Must contain only letters, digits, hyphens, and underscores.
        content: Skill body content (used for create and patch).
        category: Skill category (used for create, defaults to "general").
        description: Short skill description (used for create).

    Returns:
        Success or error message string, or skill content/list on read operations.
    """
    skills_dir = _skills_dir()

    # --- list ---
    if action == "list":
        if not await skills_dir.exists():
            return "No skills found."
        entries: list[str] = []
        async for skill_path in skills_dir.iterdir():
            if not await skill_path.is_dir():
                continue
            if skill_path.name.startswith("."):
                continue
            skill_md = skill_path / "SKILL.md"
            if not await skill_md.exists():
                continue
            try:
                raw = await skill_md.read_text(encoding="utf-8")
                fm, _ = _parse_frontmatter(raw)
                sname = fm.get("name") or skill_path.name
                desc = fm.get("description") or "(no description)"
                cat = fm.get("category") or "general"
                created_by = fm.get("created_by", "")
                tag = " [agent]" if created_by == "agent" else ""
                entries.append(f"- {sname} ({cat}){tag}: {desc}")
            except Exception:
                entries.append(f"- {skill_path.name}: (unreadable)")
        if not entries:
            return "No skills found."
        return "Skills:\n" + "\n".join(sorted(entries))

    # --- view ---
    if action == "view":
        err = _validate_skill_name(skill_name)
        if err:
            return err
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not await skill_md.exists():
            return f"Skill not found: {skill_name!r}"
        return await skill_md.read_text(encoding="utf-8")

    # --- create ---
    if action == "create":
        err = _validate_skill_name(skill_name)
        if err:
            return err
        skill_dir = skills_dir / skill_name
        if await skill_dir.exists():
            return f"Skill already exists: {skill_name!r}. Use 'patch' to update it."
        await skill_dir.mkdir(parents=True, exist_ok=False)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm_lines = [
            "---",
            f"name: {skill_name}",
            f"description: {description or '(no description)'}",
            f"category: {category or 'general'}",
            "created_by: agent",
            f"created_at: {now}",
            "---",
        ]
        skill_md_content = "\n".join(fm_lines) + "\n\n" + (content or "")
        await _atomic_write(skill_dir / "SKILL.md", skill_md_content)
        return f"Skill created: {skill_name!r}"

    # --- patch ---
    if action == "patch":
        err = _validate_skill_name(skill_name)
        if err:
            return err
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not await skill_md.exists():
            return f"Skill not found: {skill_name!r}"
        raw = await skill_md.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(raw)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm["updated_at"] = now
        fm_lines = ["---"]
        for k, v in fm.items():
            fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        new_content = "\n".join(fm_lines) + "\n\n" + (content or "")
        await _atomic_write(skill_md, new_content)
        return f"Skill patched: {skill_name!r}"

    return f"Unknown action: {action!r}. Use 'create', 'patch', 'view', or 'list'."
