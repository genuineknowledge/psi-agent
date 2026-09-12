"""Manage workspace skills."""

from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, datetime

import _content_layers as _layers
import _runtime_paths as _paths
import anyio

_KIND = "skills"


def _skills_dir() -> anyio.Path:
    # Skills live in the agent package (not the user workspace).
    #
    # 分层下 ``list`` / ``view`` / 写操作都走 ``_layers``(跨层查找 + 落最上层可写根),
    # 本函数只剩"agent 那层在哪"这一个用处(``test_runtime_paths`` 按它断言路径口径)。
    return _paths.resolve_agent() / "skills"


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


def _render(frontmatter: dict[str, str], body: str) -> str:
    """``---`` 头 + 正文。键序即 *frontmatter* 的插入序(派生三键因此排在原有键之后)。"""
    lines = ["---", *(f"{key}: {value}" for key, value in frontmatter.items()), "---"]
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


async def _index() -> dict[str, tuple[_layers.ContentLayer, anyio.Path]]:
    """``{skill 名: (生效的层, SKILL.md)}`` -- 跨层 nearest-wins, 墓碑不出现。

    与 ``system._build_skills_index`` 同一套合并语义(近者整体覆盖远者/墓碑等于不存在),
    但那一份服务提示词/这一份服务 ``list`` / ``view``。两份**必须给出同一个答案**: 工具
    ``list`` 说某 skill 在/而提示词索引里没有(或反过来), 模型就会去 patch 一个它读不到
    的东西。判据 3(墓碑)因此两侧各断言一次。
    """
    found: dict[str, tuple[_layers.ContentLayer, anyio.Path]] = {}
    # 由近及远, 第一次见到某个名字即为生效那份; 墓碑也算"见到"--它的作用正是让更远
    # 的那份不生效, 所以记下名字但不放进结果。
    #
    # 两个本地变量而非模块级: 墓碑集合若跨调用存活, 一次 delete 之后同一进程里所有后续
    # ``list`` 都会继续隐藏那个名字, **即使墓碑文件已被删掉**(Gateway 一个进程跑很多
    # Session, 会跨用户串味)。
    seen_tombstones: set[str] = set()
    for layer in _layers.layers_for(_KIND):
        try:
            if not await layer.path.is_dir():
                continue
            async for skill_dir in layer.path.iterdir():
                if not await skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                name = skill_dir.name
                if name in found or name in seen_tombstones:
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not await skill_md.exists():
                    continue
                raw = await skill_md.read_text(encoding="utf-8", errors="replace")
                frontmatter, _body = _parse_frontmatter(raw)
                if _layers.is_tombstone(frontmatter):
                    seen_tombstones.add(name)
                    continue
                found[name] = (layer, skill_md)
        except OSError:
            # 这一层读不了不能让整个 list 失败 -- 分层下"某一层不可读"是局部故障。
            continue
    return found


async def skill_manage(
    action: str = "list",
    skill_name: str = "",
    content: str = "",
    category: str = "general",
    description: str = "",
) -> str:
    """Create, patch, delete, view, or list agent-package skills.

    Before ``create``, always ``list`` (and ``view`` candidates): if a similar
    skill exists, ``patch`` it instead. See skills ``skill-authoring-when`` /
    ``skill-authoring-how``. ``patch`` is allowed when ``created_by: agent`` or
    ``agent_editable: true``.

    Under content layering (``PSI_CONTENT_ROOTS``) writes land in the **topmost
    writable layer**, never in a read-only one: patching a skill that lives in a
    read-only layer copies it up and edits the copy (the original is untouched
    and stays byte-identical), and ``delete`` writes a tombstone in the writable
    layer instead of removing the read-only file. Group chats cannot write at
    all - the refusal points at a private chat.

    Args:
        action: One of "list", "view", "create", "patch", or "delete".
        skill_name: Skill directory name for view/create/patch/delete.
        content: Full SKILL.md body content for create/patch, excluding frontmatter.
        category: Skill category used when creating a skill.
        description: Short skill description used when creating a skill.

    Returns:
        A result message, list output, or SKILL.md content.
    """
    action = action.strip().lower()

    if action == "list":
        entries: list[str] = []
        for name, (_layer, skill_md) in (await _index()).items():
            raw = await skill_md.read_text(encoding="utf-8", errors="replace")
            frontmatter, _body = _parse_frontmatter(raw)
            shown = frontmatter.get("name") or name
            desc = frontmatter.get("description") or "(no description)"
            cat = frontmatter.get("category") or "general"
            tags: list[str] = []
            if frontmatter.get("created_by") == "agent":
                tags.append("agent")
            if frontmatter.get("derived_from_layer"):
                # 派生件要看得出来: 否则"我改过的"与"官方发的"在 list 里同形, 而它们
                # 在上游整版替换时的命运完全不同。
                tags.append(f"derived from {frontmatter['derived_from_layer']}")
            tag = f" [{', '.join(tags)}]" if tags else ""
            entries.append(f"- {shown} ({cat}){tag}: {desc}")

        return "Skills:\n" + "\n".join(sorted(entries)) if entries else "No skills found."

    if action == "view":
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        located = await _index()
        if skill_name not in located:
            return f"[Error] Skill not found: {skill_name!r}"
        _layer, skill_md = located[skill_name]
        return await skill_md.read_text(encoding="utf-8", errors="replace")

    if action == "create":
        if refusal := _layers.group_write_refusal("skill"):
            return refusal
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        # 存在性按**跨层**判: 官方层已有同名 skill 时 create 会在可写层造出一个覆盖它的
        # 影子, 而用户以为自己新建了一个。指向 patch(即派生)才是那个意图对得上的动作。
        if skill_name in await _index():
            return f"[Error] Skill already exists: {skill_name!r}. Use action='patch' to update agent-created skills."

        target, why = await _layers.writable_layer(_KIND)
        if target is None:
            return f"[Error] No writable skills layer: {why}"

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
        await _atomic_write(target.path / skill_name / "SKILL.md", frontmatter + "\n\n" + content.strip() + "\n")
        return f"Skill created: {skill_name!r}"

    if action == "patch":
        if refusal := _layers.group_write_refusal("skill"):
            return refusal
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        located = await _index()
        if skill_name not in located:
            return f"[Error] Skill not found: {skill_name!r}"
        source_layer, skill_md = located[skill_name]

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

        target, why = await _layers.writable_layer(_KIND)
        if target is None:
            return f"[Error] No writable skills layer: {why}"

        # 来源版本必须在盖 ``updated_at`` **之前**取: 反过来的话 ``derived_from_version``
        # 记的是本次编辑的时间戳, 而它要答的是"我这份从上游哪一版分出去的"。两个时间戳
        # 形状相同, 于是写错了也看着像对的 -- 直到 /official 整版替换后无法对齐。
        source_version = _layers.source_version_of(frontmatter)
        frontmatter["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        target_md = target.path / skill_name / "SKILL.md"
        derived = not _layers.same_layer(source_layer, target)
        if derived:
            # 改只读层 = 派生。整体覆盖(不做字段级 merge: 那会产出谁都没写过的第三份
            # 内容), 原件一个字节都不碰, 溯源写进 frontmatter。
            frontmatter = _layers.derived_frontmatter(
                frontmatter,
                source_layer=source_layer.name,
                source_version=source_version,
            )
        await _atomic_write(target_md, _render(frontmatter, content))
        if derived:
            return (
                f"Skill patched: {skill_name!r} - derived into layer {target.name!r} "
                f"from read-only layer {source_layer.name!r} (original untouched)."
            )
        return f"Skill patched: {skill_name!r}"

    if action == "delete":
        if refusal := _layers.group_write_refusal("skill"):
            return refusal
        if err := _validate_skill_name(skill_name):
            return f"[Error] {err}"
        if skill_name not in await _index():
            return f"[Error] Skill not found: {skill_name!r}"

        target, why = await _layers.writable_layer(_KIND)
        if target is None:
            return f"[Error] No writable skills layer: {why}"

        # 真删还是写墓碑, 取决于**下面还有没有一份**, 而不是"生效那份在不在可写层"。
        # 后者会漏掉派生件这个形状: patch 官方 skill 派生出副本后, 生效那份就在可写层,
        # 于是真删副本 -- 而官方那份立刻复活(本卡实测到过), 删除静默失效。
        holders = await _layers.layers_containing(_KIND, skill_name, "SKILL.md")
        shadowed = [layer for layer in holders if not _layers.same_layer(layer, target)]
        if not shadowed:
            # 只有可写层有这一份 -- 真删。写墓碑盖自己会留下一个永远无法再 create 同名
            # skill 的残留标记。
            await (target.path / skill_name / "SKILL.md").unlink()
            with suppress(OSError):
                await (target.path / skill_name).rmdir()
            return f"Skill deleted: {skill_name!r}"

        # 下层那份不动, 可写层放墓碑让它按 nearest-wins 落选。
        source_layer = shadowed[0]
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        tombstone = "\n".join(
            [
                "---",
                f"name: {skill_name}",
                f"description: (deleted by agent; shadows layer {source_layer.name})",
                f"{_layers.TOMBSTONE_KEY}: true",
                f"derived_from_layer: {source_layer.name}",
                "created_by: agent",
                f"created_at: {now}",
                "---",
            ]
        )
        await _atomic_write(
            target.path / skill_name / "SKILL.md",
            tombstone + "\n\nStub marking this skill as removed for this user.\n",
        )
        return (
            f"Skill deleted: {skill_name!r} - tombstoned in layer {target.name!r}; "
            f"the copy in layer {source_layer.name!r} is untouched but no longer active."
        )

    return "[Error] Unknown action. Use 'list', 'view', 'create', 'patch', or 'delete'."
