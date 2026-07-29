"""Read session todo lists written by the workspace ``todo`` tool.

Path convention (Step 4B):
- **Write / prefer read**: ``{appdata}/todos/{session_id}.json``
- **Legacy dual-read**: ``{workspace}/.psi/todos/{session_id}.json`` if AppData
  file is missing (old sessions keep working until rewritten).

Gateway only reads; the agent tool owns writes.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from loguru import logger

from psi_agent.gateway._defaults import resolve_appdata_root, resolve_todo_read_path

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


class TodoManager:
    async def get(
        self,
        workspace: str,
        session_id: str,
        *,
        appdata: str = "",
    ) -> dict[str, Any]:
        """Return ``{todos, summary}`` for a session; empty list if missing/invalid."""
        appdata_root = appdata.strip() or await resolve_appdata_root()
        path = await resolve_todo_read_path(
            appdata_root=appdata_root,
            workspace=workspace,
            session_id=session_id,
        )
        items = await self._read_items(path)
        summary = self._summary(items)
        logger.debug(
            f"Todos for session {session_id!r} path={path!s}: total={summary['total']} "
            f"completed={summary['completed']} in_progress={summary['in_progress']}"
        )
        return {"todos": items, "summary": summary}

    @staticmethod
    async def _read_items(path: anyio.Path) -> list[dict[str, str]]:
        try:
            raw = await path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning(f"Failed to read todos at {path!r}: {e!r}")
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Malformed todos JSON at {path!r}")
            return []
        if not isinstance(data, dict):
            return []
        todos = data.get("todos")
        if not isinstance(todos, list):
            return []
        items: list[dict[str, str]] = []
        for entry in todos:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id", "")).strip()
            content = str(entry.get("content", "")).strip()
            status = str(entry.get("status", "")).strip().lower()
            if not item_id or not content or status not in _VALID_STATUSES:
                continue
            items.append({"id": item_id, "content": content, "status": status})
        return items

    @staticmethod
    def _summary(items: list[dict[str, str]]) -> dict[str, int]:
        return {
            "total": len(items),
            "pending": sum(1 for i in items if i["status"] == "pending"),
            "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
            "completed": sum(1 for i in items if i["status"] == "completed"),
            "cancelled": sum(1 for i in items if i["status"] == "cancelled"),
        }
