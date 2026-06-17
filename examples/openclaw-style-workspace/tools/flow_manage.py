"""Flow management tool — create, patch, view, and list curated workspace flows."""

from __future__ import annotations

import os
import pathlib
import re
from datetime import UTC, datetime

import anyio


def _flows_dir() -> anyio.Path:
    ws = os.environ.get("WORKSPACE_DIR", "")
    if ws:
        return anyio.Path(ws) / "flows"
    return anyio.Path(str(pathlib.Path(__file__).parents[1])) / "flows"


def _validate_flow_name(flow_name: str) -> str | None:
    if not flow_name or not flow_name.strip():
        return "Invalid flow name: name cannot be empty."
    if "/" in flow_name or "\\" in flow_name:
        return f"Invalid flow name {flow_name!r}: must not contain '/' or '\\'."
    if ".." in flow_name:
        return f"Invalid flow name {flow_name!r}: must not contain '..'."
    if "\x00" in flow_name:
        return f"Invalid flow name {flow_name!r}: must not contain null characters."
    if not re.match(r"^[a-zA-Z0-9_\-]+$", flow_name):
        return (
            f"Invalid flow name {flow_name!r}: only letters, digits, hyphens, "
            "and underscores are allowed."
        )
    return None


async def _atomic_write(path: anyio.Path, content: str) -> None:
    tmp = path.parent / (path.name + ".tmp")
    await tmp.write_text(content, encoding="utf-8")
    await tmp.rename(path)


async def flow_manage(
    action: str = "list",
    flow_name: str = "",
    description: str = "",
    category: str = "general",
    body: str = "",
    flow_ts: str = "",
    target: str = "curated",
) -> str:
    """Manage workspace flows: create, patch, view, list, or promote flows.

    Args:
        action: One of ``"list"``, ``"view"``, ``"create"``, ``"patch"``, ``"promote"``.
            - ``"list"``: List all flows in adhoc/ and curated/.
            - ``"view"``: Show FLOW.md content for a curated flow, or flow.ts for an adhoc flow.
            - ``"create"``: Create a new flow in flows/curated/ with FLOW.md embedding flow.ts.
            - ``"patch"``: Update an existing curated flow's body and/or flow.ts.
            - ``"promote"``: Promote an adhoc flow to curated (creates FLOW.md from flow.ts).
        flow_name: The flow directory name (required for view/create/patch/promote).
        description: One-line description for the flow (used in frontmatter).
        category: Skill category tag (default: "general").
        body: Description body text for FLOW.md (markdown, excluding frontmatter and flow.ts block).
        flow_ts: TypeScript flow.ts content to embed in FLOW.md.
        target: For ``"list"`` — "all", "adhoc", or "curated" (default: "curated").

    Returns:
        Result message or content string.
    """
    flows = _flows_dir()

    # ------------------------------------------------------------------ list
    if action == "list":
        lines: list[str] = []
        for sub in (["adhoc", "curated"] if target == "all" else [target]):
            sub_dir = flows / sub
            if not await sub_dir.exists():
                continue
            entries: list[str] = []
            async for entry in sub_dir.iterdir():
                if await entry.is_dir() and not entry.name.startswith("."):
                    if sub == "curated":
                        flow_md = entry / "FLOW.md"
                        if await flow_md.exists():
                            raw = await flow_md.read_text(encoding="utf-8")
                            desc = ""
                            if raw.startswith("---"):
                                end = raw.find("\n---", 3)
                                if end != -1:
                                    for line in raw[3:end].splitlines():
                                        if line.startswith("description:"):
                                            desc = line.split(":", 1)[1].strip()
                            entries.append(f"  - {entry.name} (curated): {desc}")
                    else:
                        ts_file = entry / "flow.ts"
                        exists = await ts_file.exists()
                        entries.append(f"  - {entry.name} (adhoc){' [has flow.ts]' if exists else ''}")
            if entries:
                lines.append(f"{sub}/")
                lines.extend(entries)
        return "\n".join(lines) if lines else "No flows found."

    # ------------------------------------------------------------------ view
    if action == "view":
        err = _validate_flow_name(flow_name)
        if err:
            return f"[Error] {err}"
        # Try curated first, then adhoc
        flow_md = flows / "curated" / flow_name / "FLOW.md"
        if await flow_md.exists():
            return await flow_md.read_text(encoding="utf-8")
        ts_file = flows / "adhoc" / flow_name / "flow.ts"
        if await ts_file.exists():
            return await ts_file.read_text(encoding="utf-8")
        return f"[Error] Flow '{flow_name}' not found in curated/ or adhoc/."

    # ------------------------------------------------------------------ create
    if action == "create":
        err = _validate_flow_name(flow_name)
        if err:
            return f"[Error] {err}"
        dest = "adhoc" if target == "adhoc" else "curated"
        flow_dir = flows / dest / flow_name
        flow_md = flow_dir / "FLOW.md"
        if await flow_md.exists():
            return f"[Error] Flow '{flow_name}' already exists in {dest}/. Use action='patch' to update."
        await flow_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm = (
            f"---\nname: {flow_name}\ndescription: {description}\n"
            f"category: {category}\ncreated_by: agent\ncreated_at: {now}\n---"
        )
        ts_block = f"\n```typescript\n{flow_ts.strip()}\n```" if flow_ts.strip() else ""
        content = fm + ("\n\n" + body.strip() if body.strip() else "") + ts_block + "\n"
        await _atomic_write(flow_md, content)
        return f"Flow created: '{flow_name}' in flows/{dest}/"

    # ------------------------------------------------------------------ patch
    if action == "patch":
        err = _validate_flow_name(flow_name)
        if err:
            return f"[Error] {err}"
        flow_md = flows / "curated" / flow_name / "FLOW.md"
        if not await flow_md.exists():
            return f"[Error] Flow '{flow_name}' not found in curated/."
        raw = await flow_md.read_text(encoding="utf-8")
        # Update updated_at in frontmatter
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if "updated_at:" in raw:
            raw = re.sub(r"updated_at:.*", f"updated_at: {now}", raw)
        else:
            raw = raw.replace("\n---\n", f"\nupdated_at: {now}\n---\n", 1)
        # Replace body if provided
        if body.strip():
            # Replace content after frontmatter (keep frontmatter, replace rest)
            end = raw.find("\n---\n", 3)
            if end != -1:
                fm_part = raw[: end + 5]
                # Preserve existing flow.ts block if no new one provided
                existing_ts = ""
                if not flow_ts.strip():
                    ts_match = re.search(r"```(?:typescript|ts)\s*\n(.*?)```", raw, re.DOTALL)
                    if ts_match:
                        existing_ts = ts_match.group(1).strip()
                ts_block = f"\n```typescript\n{(flow_ts.strip() or existing_ts)}\n```" if (flow_ts.strip() or existing_ts) else ""
                raw = fm_part + body.strip() + ts_block + "\n"
        elif flow_ts.strip():
            # Only update flow.ts block
            ts_block = f"```typescript\n{flow_ts.strip()}\n```"
            if re.search(r"```(?:typescript|ts)", raw):
                raw = re.sub(r"```(?:typescript|ts)\s*\n.*?```", ts_block, raw, flags=re.DOTALL)
            else:
                raw = raw.rstrip() + "\n\n" + ts_block + "\n"
        await _atomic_write(flow_md, raw)
        return f"Flow patched: '{flow_name}'"

    # ------------------------------------------------------------------ promote
    if action == "promote":
        err = _validate_flow_name(flow_name)
        if err:
            return f"[Error] {err}"
        adhoc_dir = flows / "adhoc" / flow_name
        ts_file = adhoc_dir / "flow.ts"
        if not await ts_file.exists():
            return f"[Error] Adhoc flow '{flow_name}' not found or has no flow.ts."
        curated_dir = flows / "curated" / flow_name
        if await curated_dir.exists():
            return f"[Error] Curated flow '{flow_name}' already exists."
        # Move entire adhoc directory to curated (preserves flow.ts and any run artifacts)
        import shutil as _shutil
        _shutil.move(str(adhoc_dir), str(curated_dir))
        # Write FLOW.md metadata alongside the moved files
        flow_md = curated_dir / "FLOW.md"
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm = (
            f"---\nname: {flow_name}\ndescription: {description}\n"
            f"category: {category}\ncreated_by: agent\ncreated_at: {now}\n---"
        )
        content = fm + ("\n\n" + body.strip() if body.strip() else "") + "\n"
        await _atomic_write(flow_md, content)
        return f"Flow promoted: '{flow_name}' → flows/curated/ (moved from adhoc)"

    return f"Unknown action: {action!r}. Use 'list', 'view', 'create', 'patch', or 'promote'."
