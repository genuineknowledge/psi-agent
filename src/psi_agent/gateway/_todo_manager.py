"""Read session todo lists written by the workspace ``todo`` tool.

Path convention (Step 4B):
- **Write / prefer read**: ``{appdata}/todos/{session_id}.json``
- **Legacy dual-read**: ``{workspace}/.psi/todos/{session_id}.json`` if AppData
  file is missing (old sessions keep working until rewritten).

Sub-task segments (spa-v2「任务历史」):
- ``{appdata}/todos/{session_id}.segments.json`` — written by the todo tool;
  Gateway reads + optional label patch (P1).

Gateway only reads live todos; the agent tool owns list writes.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from loguru import logger

from psi_agent.gateway._defaults import (
    appdata_todo_segments_path,
    resolve_appdata_root,
    resolve_todo_read_path,
)

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
_MAX_LABEL_LEN = 48


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

    async def list_segments(
        self,
        session_id: str,
        *,
        appdata: str = "",
    ) -> list[dict[str, Any]]:
        """Return segment summaries newest-first (no full todos arrays)."""
        segments = await self._load_segments(session_id, appdata=appdata)
        out: list[dict[str, Any]] = []
        for seg in reversed(segments):
            items = self._normalize_todos(seg.get("todos"))
            out.append(
                {
                    "id": seg["id"],
                    "label": seg.get("label") or "",
                    "created_at": seg.get("created_at") or "",
                    "updated_at": seg.get("updated_at") or "",
                    "closed_at": seg.get("closed_at"),
                    "source": seg.get("source") or "",
                    "summary": self._summary(items),
                }
            )
        logger.debug(f"Todo segments for session {session_id!r}: count={len(out)}")
        return out

    async def get_segment(
        self,
        session_id: str,
        segment_id: str,
        *,
        appdata: str = "",
    ) -> dict[str, Any] | None:
        """Return one segment including ``todos[]``, or None if missing."""
        sid = segment_id.strip()
        if not sid:
            return None
        for seg in await self._load_segments(session_id, appdata=appdata):
            if seg.get("id") == sid:
                todos = self._normalize_todos(seg.get("todos"))
                return {
                    "id": sid,
                    "label": seg.get("label") or "",
                    "created_at": seg.get("created_at") or "",
                    "updated_at": seg.get("updated_at") or "",
                    "closed_at": seg.get("closed_at"),
                    "source": seg.get("source") or "",
                    "todos": todos,
                    "summary": self._summary(todos),
                }
        return None

    async def set_segment_label(
        self,
        session_id: str,
        segment_id: str,
        label: str,
        *,
        appdata: str = "",
    ) -> dict[str, Any] | None:
        """Patch a segment label (P1). Returns updated summary row or None."""
        sid = segment_id.strip()
        text = " ".join(label.split()).strip()
        if not sid or not text:
            return None
        if len(text) > _MAX_LABEL_LEN:
            text = text[: _MAX_LABEL_LEN - 1] + "…"
        appdata_root = appdata.strip() or await resolve_appdata_root()
        path = appdata_todo_segments_path(appdata_root, session_id)
        segments = await self._load_segments(session_id, appdata=appdata_root)
        found = False
        for index, seg in enumerate(segments):
            if seg.get("id") == sid:
                updated = dict(seg)
                updated["label"] = text
                segments[index] = updated
                found = True
                break
        if not found:
            return None
        payload = {"session_id": session_id, "segments": segments}
        try:
            await path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / f"{path.name}.tmp"
            await tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if await path.exists():
                await path.unlink()
            await tmp.rename(path)
        except OSError as e:
            logger.warning(f"Failed to write todo segments label at {path!r}: {e!r}")
            return None
        logger.info(f"Todo segment label updated session={session_id!r} segment={sid!r}")
        return await self.get_segment(session_id, sid, appdata=appdata_root)

    async def _load_segments(
        self,
        session_id: str,
        *,
        appdata: str = "",
    ) -> list[dict[str, Any]]:
        appdata_root = appdata.strip() or await resolve_appdata_root()
        path = appdata_todo_segments_path(appdata_root, session_id)
        try:
            raw = await path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning(f"Failed to read todo segments at {path!r}: {e!r}")
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Malformed todo segments JSON at {path!r}")
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
            closed_raw = entry.get("closed_at")
            closed_at = str(closed_raw).strip() if closed_raw not in (None, "") else None
            out.append(
                {
                    "id": seg_id,
                    "created_at": str(entry.get("created_at", "")).strip(),
                    "updated_at": str(entry.get("updated_at", "")).strip(),
                    "closed_at": closed_at,
                    "label": str(entry.get("label", "")).strip(),
                    "source": str(entry.get("source", "")).strip(),
                    "todos": entry.get("todos") if isinstance(entry.get("todos"), list) else [],
                }
            )
        return out

    @staticmethod
    def _normalize_todos(raw: Any) -> list[dict[str, str]]:
        if not isinstance(raw, list):
            return []
        items: list[dict[str, str]] = []
        for entry in raw:
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
