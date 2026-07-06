"""Manage reusable Fusion Flow assets."""

from __future__ import annotations

import os
import pathlib
import re
from datetime import UTC, datetime

import anyio


def _flows_dir() -> anyio.Path:
    workspace_dir = os.environ.get("WORKSPACE_DIR", "")
    if workspace_dir:
        return anyio.Path(workspace_dir) / "flows"
    return anyio.Path(str(pathlib.Path(__file__).resolve().parents[1])) / "flows"


def _validate_flow_name(flow_name: str) -> str | None:
    if not flow_name.strip():
        return "Invalid flow name: name cannot be empty."
    if "/" in flow_name or "\\" in flow_name:
        return f"Invalid flow name {flow_name!r}: must not contain path separators."
    if ".." in flow_name:
        return f"Invalid flow name {flow_name!r}: must not contain '..'."
    if "\x00" in flow_name:
        return f"Invalid flow name {flow_name!r}: must not contain null characters."
    if not re.fullmatch(r"[A-Za-z0-9_-]+", flow_name):
        return f"Invalid flow name {flow_name!r}: only letters, digits, hyphens, and underscores are allowed."
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


async def _find_task_flow(flows_dir: anyio.Path, flow_name: str) -> anyio.Path | None:
    task_dir = flows_dir / flow_name
    preferred = task_dir / f"{flow_name}.flow.ts"
    if await preferred.exists():
        return preferred
    if not await task_dir.is_dir():
        return None
    async for candidate in task_dir.glob("*.flow.ts"):
        return candidate
    return None


def _format_flow_document(
    *,
    flow_name: str,
    description: str,
    category: str,
    body: str,
    flow_ts: str,
    source: str = "",
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "---",
        f"name: {flow_name}",
        f"description: {description or '(no description)'}",
        f"category: {category or 'general'}",
        "created_by: agent",
        f"created_at: {now}",
    ]
    if source:
        lines.append(f"source: {source}")
    lines.append("---")

    sections = ["\n".join(lines), body.strip()]
    if flow_ts.strip():
        sections.append("```typescript\n" + flow_ts.strip() + "\n```")
    return "\n\n".join(section for section in sections if section).rstrip() + "\n"


async def flow_manage(
    action: str = "list",
    flow_name: str = "",
    description: str = "",
    category: str = "general",
    body: str = "",
    flow_ts: str = "",
    target: str = "curated",
) -> str:
    """Create, patch, view, list, or promote reusable Fusion Flow assets.

    Args:
        action: One of "list", "view", "create", "patch", or "promote".
        flow_name: Flow name for view/create/patch/promote.
        description: One-line description for created or promoted flows.
        category: Category tag for created or promoted flows.
        body: FLOW.md body text, excluding frontmatter and TypeScript block.
        flow_ts: TypeScript flow content to store in FLOW.md.
        target: For list/view/create. Use "curated", "tasks", "adhoc", or "all".

    Returns:
        A result message, list output, or flow content.
    """
    flows_dir = _flows_dir()
    action = action.strip().lower()
    target = target.strip().lower() or "curated"

    if action == "list":
        lines: list[str] = []

        if target in {"curated", "all"}:
            curated_dir = flows_dir / "curated"
            curated_entries: list[str] = []
            if await curated_dir.exists():
                async for entry in curated_dir.iterdir():
                    flow_md = entry / "FLOW.md"
                    if not await entry.is_dir() or entry.name.startswith(".") or not await flow_md.exists():
                        continue
                    raw = await flow_md.read_text(encoding="utf-8", errors="replace")
                    frontmatter, _body = _parse_frontmatter(raw)
                    desc = frontmatter.get("description") or "(no description)"
                    tag = " [agent]" if frontmatter.get("created_by") == "agent" else ""
                    curated_entries.append(f"  - {entry.name}{tag}: {desc}")
            if curated_entries:
                lines.append("curated/")
                lines.extend(sorted(curated_entries))

        if target in {"tasks", "all"} and await flows_dir.exists():
            task_entries: list[str] = []
            async for entry in flows_dir.iterdir():
                if not await entry.is_dir() or entry.name.startswith(".") or entry.name in {"curated", "adhoc"}:
                    continue
                task_flow = await _find_task_flow(flows_dir, entry.name)
                if task_flow is not None:
                    task_entries.append(f"  - {entry.name}: {task_flow.name}")
            if task_entries:
                lines.append("tasks/")
                lines.extend(sorted(task_entries))

        if target in {"adhoc", "all"}:
            adhoc_dir = flows_dir / "adhoc"
            adhoc_entries: list[str] = []
            if await adhoc_dir.exists():
                async for entry in adhoc_dir.iterdir():
                    flow_file = entry / "flow.ts"
                    if await entry.is_dir() and not entry.name.startswith(".") and await flow_file.exists():
                        adhoc_entries.append(f"  - {entry.name}: flow.ts")
            if adhoc_entries:
                lines.append("adhoc/")
                lines.extend(sorted(adhoc_entries))

        return "\n".join(lines) if lines else "No flows found."

    if action == "view":
        if err := _validate_flow_name(flow_name):
            return f"[Error] {err}"

        if target in {"curated", "all"}:
            flow_md = flows_dir / "curated" / flow_name / "FLOW.md"
            if await flow_md.exists():
                return await flow_md.read_text(encoding="utf-8", errors="replace")

        if target in {"tasks", "all"}:
            task_flow = await _find_task_flow(flows_dir, flow_name)
            if task_flow is not None:
                return await task_flow.read_text(encoding="utf-8", errors="replace")

        if target in {"adhoc", "all"}:
            adhoc_flow = flows_dir / "adhoc" / flow_name / "flow.ts"
            if await adhoc_flow.exists():
                return await adhoc_flow.read_text(encoding="utf-8", errors="replace")

        return f"[Error] Flow not found: {flow_name!r}"

    if action == "create":
        if err := _validate_flow_name(flow_name):
            return f"[Error] {err}"
        if target not in {"curated", "adhoc"}:
            return "[Error] Create target must be 'curated' or 'adhoc'."

        if target == "adhoc":
            flow_path = flows_dir / "adhoc" / flow_name / "flow.ts"
            if await flow_path.exists():
                return f"[Error] Adhoc flow already exists: {flow_name!r}"
            await _atomic_write(flow_path, flow_ts.strip() + "\n")
            return f"Adhoc flow created: {flow_name!r}"

        flow_md = flows_dir / "curated" / flow_name / "FLOW.md"
        if await flow_md.exists():
            return f"[Error] Curated flow already exists: {flow_name!r}. Use action='patch' to update it."
        await _atomic_write(
            flow_md,
            _format_flow_document(
                flow_name=flow_name,
                description=description,
                category=category,
                body=body,
                flow_ts=flow_ts,
            ),
        )
        return f"Curated flow created: {flow_name!r}"

    if action == "patch":
        if err := _validate_flow_name(flow_name):
            return f"[Error] {err}"

        flow_md = flows_dir / "curated" / flow_name / "FLOW.md"
        if not await flow_md.exists():
            return f"[Error] Curated flow not found: {flow_name!r}"

        raw = await flow_md.read_text(encoding="utf-8", errors="replace")
        frontmatter, existing_body = _parse_frontmatter(raw)
        if frontmatter.get("created_by") != "agent":
            return f"[Error] Flow {flow_name!r} is user-authored or unmanaged; patch is read-only."

        frontmatter["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = ["---", *(f"{key}: {value}" for key, value in frontmatter.items()), "---"]
        next_body = body.strip() or existing_body.strip()
        if flow_ts.strip():
            next_body = re.sub(
                r"```(?:typescript|ts)\s*\n.*?```",
                "```typescript\n" + flow_ts.strip() + "\n```",
                next_body,
                flags=re.DOTALL,
            )
            if "```typescript" not in next_body and "```ts" not in next_body:
                next_body += "\n\n```typescript\n" + flow_ts.strip() + "\n```"

        await _atomic_write(flow_md, "\n".join(lines) + "\n\n" + next_body.strip() + "\n")
        return f"Curated flow patched: {flow_name!r}"

    if action == "promote":
        if err := _validate_flow_name(flow_name):
            return f"[Error] {err}"

        source_path = await _find_task_flow(flows_dir, flow_name)
        source_label = f"flows/{flow_name}"
        if source_path is None:
            adhoc_path = flows_dir / "adhoc" / flow_name / "flow.ts"
            if await adhoc_path.exists():
                source_path = adhoc_path
                source_label = f"flows/adhoc/{flow_name}/flow.ts"

        if source_path is None:
            return f"[Error] No task or adhoc flow found for: {flow_name!r}"

        flow_md = flows_dir / "curated" / flow_name / "FLOW.md"
        if await flow_md.exists():
            return f"[Error] Curated flow already exists: {flow_name!r}"

        source_ts = await source_path.read_text(encoding="utf-8", errors="replace")
        await _atomic_write(
            flow_md,
            _format_flow_document(
                flow_name=flow_name,
                description=description,
                category=category,
                body=body,
                flow_ts=source_ts,
                source=source_label,
            ),
        )
        return f"Flow promoted to curated: {flow_name!r}"

    return "[Error] Unknown action. Use 'list', 'view', 'create', 'patch', or 'promote'."
