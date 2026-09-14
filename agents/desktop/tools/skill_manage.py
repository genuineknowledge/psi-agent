"""Manage workspace skills."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import _runtime_paths as _paths
import anyio


def _skills_dir() -> anyio.Path:
    # User-created/derived skills land in the global personal layer
    # ~/.agent/skills: the official layer is replaced wholesale on upgrade, so
    # writing the official package gets wiped (requirement one). Keeps the same
    # personal layer as the index/read paths. No need to pre-create the dir:
    # create goes through _atomic_write's mkdir(parents=True).
    return _paths.global_skills_dir()


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
    # Windows: Path.rename fails when the destination already exists.
    if await path.exists():
        await path.unlink()
    await tmp.rename(path)


async def skill_manage(
    action: str = "list",
    skill_name: str = "",
    content: str = "",
    category: str = "general",
    description: str = "",
) -> str:
    """Create, patch, view, or list agent-package skills.

    Before ``create``, always ``list`` (and ``view`` candidates): if a similar
    skill exists, ``patch`` it instead. See skills ``skill-authoring-when`` /
    ``skill-authoring-how``. ``patch`` is allowed when ``created_by: agent`` or
    ``agent_editable: true``.

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
        # Layered merge: official (agent package skills/) + global personal
        # (~/.agent/skills), global wins on conflict (matches _build_skills_index).
        # Scanning only the global layer would make the pre-create dedup miss
        # 100+ official skills, so both layers must be scanned.
        merged: dict[str, anyio.Path] = {}
        for layer_dir in (_paths.resolve_agent() / "skills", skills_dir):
            if not await layer_dir.exists():
                continue
            async for skill_dir in layer_dir.iterdir():
                if not await skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if await skill_md.exists():
                    merged[skill_dir.name] = skill_md  # later global pass overrides earlier official

        if not merged:
            return "No skills found."

        entries: list[str] = []
        for dir_name, skill_md in merged.items():
            raw = await skill_md.read_text(encoding="utf-8", errors="replace")
            frontmatter, _body = _parse_frontmatter(raw)
            name = frontmatter.get("name") or dir_name
            desc = frontmatter.get("description") or "(no description)"
            cat = frontmatter.get("category") or "general"
            tag = " [agent]" if frontmatter.get("created_by") == "agent" else ""
            entries.append(f"- {name} ({cat}){tag}: {desc}")

        return "Skills:\n" + "\n".join(sorted(entries))

    if action == "view":
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        # Layered resolution (global -> official), same as read's skills/ branch.
        # Querying only the global layer would leave an official skill "listed
        # but not found on view", so go through resolve_skill_path.
        skill_md = await _paths.resolve_skill_path(f"skills/{skill_name}/SKILL.md")
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
        # Bundled base skills may set agent_editable: true so user rules merge via patch
        # instead of spawning parallel skills (see skill-authoring-when).
        editable = frontmatter.get("created_by") == "agent" or frontmatter.get("agent_editable", "").lower() in {
            "true",
            "1",
            "yes",
        }
        if not editable:
            return (
                f"[Error] Skill {skill_name!r} is not agent-editable; "
                "patch requires created_by=agent or agent_editable=true."
            )

        frontmatter["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = ["---", *(f"{key}: {value}" for key, value in frontmatter.items()), "---"]
        await _atomic_write(skill_md, "\n".join(lines) + "\n\n" + content.strip() + "\n")
        return f"Skill patched: {skill_name!r}"

    return "[Error] Unknown action. Use 'list', 'view', 'create', or 'patch'."
