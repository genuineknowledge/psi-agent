"""Session-scoped todo list persistence (AppData + legacy dual-read).

**Write** (Step 4B): ``{appdata}/todos/{session_id}.json``
**Read**: AppData file if present, else legacy ``{workspace}/.psi/todos/{session_id}.json``.

**Segments** (sub-task history): ``{appdata}/todos/{session_id}.segments.json``
- ``merge=false`` opens a new segment (closes the previous open one).
- ``merge=true`` updates the open segment snapshot only.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import _background_process_registry as _bg
import anyio
from _session_helpers import current_session_id

from psi_agent.gateway._defaults import (
    appdata_todo_path,
    appdata_todo_segments_path,
    resolve_appdata_root,
    resolve_todo_read_path,
)

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
MAX_TODO_ITEMS = 50
MAX_CONTENT_LEN = 500
MAX_ID_LEN = 64
MAX_SEGMENT_LABEL_LEN = 48

# Soft warn (C): self-referential checklist rows — still written, Agent should revise.
_SELF_REF_CONTENT = re.compile(
    r"(?:"
    r"更新\s*(?:todo|清单|任务列表|进度)|"
    r"勾选\s*(?:清单|todo|完成)|"
    r"回复\s*用户|"
    r"告知\s*用户|"
    r"同步\s*(?:侧栏|进度|UI|界面)|"
    r"结束\s*本轮|"
    r"维护\s*(?:todo|清单)|"
    r"更新清单状态|"
    r"update\s+(?:the\s+)?todo|"
    r"reply\s+to\s+(?:the\s+)?user|"
    r"mark\s+(?:as\s+)?complete.*(?:list|todo)|"
    r"sync\s+(?:the\s+)?(?:progress|sidebar|ui)"
    r")",
    re.IGNORECASE,
)


def resolve_session_id() -> str:
    """Current session id from argv, else ``default`` for standalone tool calls."""
    sid = current_session_id().strip()
    return sid or "default"


def todo_path(workspace: anyio.Path, session_id: str) -> anyio.Path:
    """Legacy path helper (tests / callers). Prefer ``appdata_todo_path`` for writes."""
    return workspace / ".psi" / "todos" / f"{session_id}.json"


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _validate_item(raw: Any, *, index: int) -> dict[str, str] | str:
    if not isinstance(raw, dict):
        return f"todos[{index}] must be an object"
    item_id = str(raw.get("id", "")).strip()
    if not item_id:
        return f"todos[{index}].id is required"
    if len(item_id) > MAX_ID_LEN:
        return f"todos[{index}].id exceeds {MAX_ID_LEN} characters"
    content = str(raw.get("content", "")).strip()
    if not content:
        return f"todos[{index}].content is required"
    if len(content) > MAX_CONTENT_LEN:
        return f"todos[{index}].content exceeds {MAX_CONTENT_LEN} characters"
    status = str(raw.get("status", "")).strip().lower()
    if status not in VALID_STATUSES:
        return f"todos[{index}].status must be one of: {', '.join(sorted(VALID_STATUSES))}"
    return {"id": item_id, "content": content, "status": status}


def _validate_items(items: list[Any]) -> tuple[list[dict[str, str]] | None, str]:
    if len(items) > MAX_TODO_ITEMS:
        return None, f"todo list cannot exceed {MAX_TODO_ITEMS} items"
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        validated = _validate_item(raw, index=index)
        if isinstance(validated, str):
            return None, validated
        if validated["id"] in seen:
            return None, f"duplicate todo id {validated['id']!r}"
        seen.add(validated["id"])
        out.append(validated)
    return out, ""


def _enforce_single_in_progress(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the last in_progress item; demote earlier ones to pending."""
    last_idx = -1
    for index, item in enumerate(items):
        if item["status"] == "in_progress":
            last_idx = index
    if last_idx < 0:
        return items
    adjusted: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if item["status"] == "in_progress" and index != last_idx:
            adjusted.append({**item, "status": "pending"})
        else:
            adjusted.append(dict(item))
    return adjusted


def _summary(items: list[dict[str, str]]) -> dict[str, int]:
    return {
        "total": len(items),
        "pending": sum(1 for i in items if i["status"] == "pending"),
        "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
        "completed": sum(1 for i in items if i["status"] == "completed"),
        "cancelled": sum(1 for i in items if i["status"] == "cancelled"),
    }


def looks_self_referential(content: str) -> bool:
    """True when content is meta (maintain todo / reply to user), not a deliverable."""
    text = " ".join(content.split()).strip()
    if not text:
        return False
    return _SELF_REF_CONTENT.search(text) is not None


def self_ref_warnings(items: list[dict[str, str]]) -> list[str]:
    """Soft advisories for self-referential rows (does not block the write)."""
    out: list[str] = []
    for item in items:
        if not looks_self_referential(item["content"]):
            continue
        preview = item["content"]
        if len(preview) > 40:
            preview = preview[:39] + "…"
        out.append(
            f"todo id={item['id']!r} content looks self-referential ({preview!r}). "
            "Prefer real deliverables; update status with merge=true without a dedicated "
            "「更新清单/回复用户」step. See skills/task-planning/SKILL.md."
        )
    return out


def segment_label_from_todos(items: list[dict[str, str]]) -> str:
    """Short label for a sub-task segment (P0/P1 default before summary override)."""
    chosen = ""
    for item in items:
        if item.get("status") == "in_progress":
            chosen = str(item.get("content", "")).strip()
            break
    if not chosen and items:
        chosen = str(items[0].get("content", "")).strip()
    if not chosen:
        chosen = "子任务"
    if len(chosen) > MAX_SEGMENT_LABEL_LEN:
        return chosen[: MAX_SEGMENT_LABEL_LEN - 1] + "…"
    return chosen


def _copy_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"id": i["id"], "content": i["content"], "status": i["status"]} for i in items]


async def _read_segments_file(path: anyio.Path) -> list[dict[str, Any]]:
    if not await path.exists():
        return []
    try:
        raw = await path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError, json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    segments = data.get("segments")
    if not isinstance(segments, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in segments:
        if not isinstance(entry, dict):
            continue
        seg_id = str(entry.get("id", "")).strip()
        if not seg_id:
            continue
        todos_raw = entry.get("todos")
        todos: list[dict[str, str]] = []
        if isinstance(todos_raw, list):
            for index, todo_entry in enumerate(todos_raw):
                validated = _validate_item(todo_entry, index=index)
                if isinstance(validated, dict):
                    todos.append(validated)
        closed_raw = entry.get("closed_at")
        closed_at = str(closed_raw).strip() if closed_raw not in (None, "") else None
        label = str(entry.get("label", "")).strip() or segment_label_from_todos(todos)
        source = str(entry.get("source", "")).strip() or "todo.replace"
        out.append(
            {
                "id": seg_id,
                "created_at": str(entry.get("created_at", "")).strip(),
                "updated_at": str(entry.get("updated_at", "")).strip(),
                "closed_at": closed_at,
                "label": label,
                "source": source,
                "todos": todos,
            }
        )
    return out


async def _write_segments_file(
    path: anyio.Path,
    *,
    session_id: str,
    segments: list[dict[str, Any]],
) -> None:
    payload = {"session_id": session_id, "segments": segments}
    await _atomic_write(path, payload)


def _open_segment_index(segments: list[dict[str, Any]]) -> int:
    for index, seg in enumerate(segments):
        if not seg.get("closed_at"):
            return index
    return -1


async def sync_todo_segments(
    *,
    appdata_root: str,
    session_id: str,
    merge: bool,
    previous_items: list[dict[str, str]],
    new_items: list[dict[str, str]],
) -> str | None:
    """Update segments file after a successful live todo write. Returns open segment id."""
    path = appdata_todo_segments_path(appdata_root, session_id)
    segments = await _read_segments_file(path)
    now = _iso_now()
    open_idx = _open_segment_index(segments)

    if not merge:
        if open_idx >= 0:
            final_todos = previous_items if previous_items else list(segments[open_idx].get("todos") or [])
            closed = dict(segments[open_idx])
            closed["todos"] = _copy_items(final_todos)
            closed["updated_at"] = now
            closed["closed_at"] = now
            if not str(closed.get("label", "")).strip():
                closed["label"] = segment_label_from_todos(final_todos)
            segments[open_idx] = closed
        elif previous_items:
            segments.append(
                {
                    "id": uuid.uuid4().hex,
                    "created_at": now,
                    "updated_at": now,
                    "closed_at": now,
                    "label": segment_label_from_todos(previous_items),
                    "source": "todo.replace",
                    "todos": _copy_items(previous_items),
                }
            )
        new_id = uuid.uuid4().hex
        segments.append(
            {
                "id": new_id,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
                "label": segment_label_from_todos(new_items),
                "source": "todo.replace",
                "todos": _copy_items(new_items),
            }
        )
        await _write_segments_file(path, session_id=session_id, segments=segments)
        return new_id

    if open_idx < 0:
        new_id = uuid.uuid4().hex
        segments.append(
            {
                "id": new_id,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
                "label": segment_label_from_todos(new_items),
                "source": "todo.merge",
                "todos": _copy_items(new_items),
            }
        )
        await _write_segments_file(path, session_id=session_id, segments=segments)
        return new_id

    current = dict(segments[open_idx])
    current["todos"] = _copy_items(new_items)
    current["updated_at"] = now
    segments[open_idx] = current
    await _write_segments_file(path, session_id=session_id, segments=segments)
    return str(current["id"])


async def _read_file(path: anyio.Path) -> list[dict[str, str]]:
    if not await path.exists():
        return []
    try:
        raw = await path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError, json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    todos = data.get("todos")
    if not isinstance(todos, list):
        return []
    items: list[dict[str, str]] = []
    for index, entry in enumerate(todos):
        validated = _validate_item(entry, index=index)
        if isinstance(validated, dict):
            items.append(validated)
    return items


async def _atomic_write(path: anyio.Path, payload: dict[str, Any]) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if await path.exists():
        await path.unlink()
    await tmp.rename(path)


def _merge_items(
    existing: list[dict[str, str]],
    incoming: list[dict[str, str]],
) -> list[dict[str, str]]:
    by_id = {item["id"]: dict(item) for item in existing}
    order = [item["id"] for item in existing]
    for item in incoming:
        if item["id"] in by_id:
            by_id[item["id"]] = dict(item)
        else:
            by_id[item["id"]] = dict(item)
            order.append(item["id"])
    return [by_id[item_id] for item_id in order if item_id in by_id]


async def read_todos(*, workspace_raw: str = "", session_id: str = "") -> dict[str, Any]:
    workspace = _bg.resolve_workspace(workspace_raw)
    sid = session_id.strip() or resolve_session_id()
    appdata_root = await resolve_appdata_root()
    path = await resolve_todo_read_path(
        appdata_root=appdata_root,
        workspace=str(workspace),
        session_id=sid,
    )
    items = await _read_file(path)
    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "appdata": appdata_root,
        "path": str(path),
        "todos": items,
        "summary": _summary(items),
    }


async def write_todos(
    *,
    todos: list[Any],
    merge: bool = False,
    workspace_raw: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    workspace = _bg.resolve_workspace(workspace_raw)
    sid = session_id.strip() or resolve_session_id()
    appdata_root = await resolve_appdata_root()
    write_path = appdata_todo_path(appdata_root, sid)
    read_path = await resolve_todo_read_path(
        appdata_root=appdata_root,
        workspace=str(workspace),
        session_id=sid,
    )

    validated, err = _validate_items(todos)
    if validated is None:
        return {
            "ok": False,
            "message": err,
            "session_id": sid,
            "workspace": str(workspace),
            "appdata": appdata_root,
        }

    previous_items = await _read_file(read_path)
    if merge:
        items = _merge_items(previous_items, validated)
        validated_merge, err = _validate_items(items)
        if validated_merge is None:
            return {
                "ok": False,
                "message": err,
                "session_id": sid,
                "workspace": str(workspace),
                "appdata": appdata_root,
            }
        items = validated_merge
    else:
        items = validated

    items = _enforce_single_in_progress(items)
    payload = {
        "session_id": sid,
        "updated_at": _iso_now(),
        "todos": items,
    }
    try:
        await _atomic_write(write_path, payload)
    except OSError as exc:
        return {
            "ok": False,
            "message": f"failed to write todos: {exc}",
            "session_id": sid,
            "workspace": str(workspace),
            "appdata": appdata_root,
        }

    segment_id: str | None = None
    try:
        segment_id = await sync_todo_segments(
            appdata_root=appdata_root,
            session_id=sid,
            merge=merge,
            previous_items=previous_items,
            new_items=items,
        )
    except OSError:
        segment_id = None

    warnings = self_ref_warnings(items)
    result: dict[str, Any] = {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "appdata": appdata_root,
        "path": str(write_path),
        "todos": items,
        "summary": _summary(items),
        "merge": merge,
        "segment_id": segment_id,
    }
    # Soft only (C): still ok=true; Agent should revise content on next write.
    if warnings:
        result["warnings"] = warnings
    return result
