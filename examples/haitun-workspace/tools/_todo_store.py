"""Session-scoped todo list persistence under ``<workspace>/.psi/todos/``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import _background_process_registry as _bg
import anyio
from _session_helpers import current_session_id

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
MAX_TODO_ITEMS = 50
MAX_CONTENT_LEN = 500
MAX_ID_LEN = 64


def resolve_session_id() -> str:
    """Current session id from argv, else ``default`` for standalone tool calls."""
    sid = current_session_id().strip()
    return sid or "default"


def todo_path(workspace: anyio.Path, session_id: str) -> anyio.Path:
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
    path = todo_path(workspace, sid)
    items = await _read_file(path)
    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
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
    path = todo_path(workspace, sid)

    validated, err = _validate_items(todos)
    if validated is None:
        return {"ok": False, "message": err, "session_id": sid, "workspace": str(workspace)}

    if merge:
        existing = await _read_file(path)
        items = _merge_items(existing, validated)
        validated_merge, err = _validate_items(items)
        if validated_merge is None:
            return {"ok": False, "message": err, "session_id": sid, "workspace": str(workspace)}
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
        await _atomic_write(path, payload)
    except OSError as exc:
        return {
            "ok": False,
            "message": f"failed to write todos: {exc}",
            "session_id": sid,
            "workspace": str(workspace),
        }

    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "todos": items,
        "summary": _summary(items),
        "merge": merge,
    }
