"""Maintain ``history/meta.jsonl`` — one JSON object per session line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent._app_paths import history_meta_path


async def upsert_history_meta(
    *,
    session_id: str,
    workspace: str,
    agent: str,
    name: str = "",
    app_data_override: str | None = None,
) -> None:
    """Insert or replace the meta row for *session_id* in ``meta.jsonl``."""
    meta_path = history_meta_path(override=app_data_override)
    parent = anyio.Path(str(meta_path.parent))
    await parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    path = anyio.Path(str(meta_path))
    try:
        raw = await path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    except OSError as e:
        logger.warning(f"Failed to read history meta {meta_path}: {e!r}")
        raw = ""

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and str(obj.get("id", "")) != session_id:
            rows.append(obj)

    display_name = name.strip() or session_id
    rows.append(
        {
            "id": session_id,
            "name": display_name,
            "workspace": workspace,
            "agent": agent,
        }
    )

    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp_path = anyio.Path(str(meta_path) + ".tmp")
    await tmp_path.write_text(text, encoding="utf-8")
    await anyio.Path(str(meta_path)).parent.mkdir(parents=True, exist_ok=True)
    # Path.replace is sync; run via to_thread for cancel-safety consistency with Conversation.save
    await anyio.to_thread.run_sync(Path(str(tmp_path)).replace, Path(str(meta_path)))
    logger.debug(f"History meta upserted for session {session_id!r} -> {meta_path}")


async def remove_history_meta(
    *,
    session_id: str,
    app_data_override: str | None = None,
) -> None:
    """Drop the meta row for *session_id* if present."""
    meta_path = history_meta_path(override=app_data_override)
    path = anyio.Path(str(meta_path))
    try:
        raw = await path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as e:
        logger.warning(f"Failed to read history meta {meta_path}: {e!r}")
        return

    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and str(obj.get("id", "")) != session_id:
            rows.append(obj)

    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp_path = anyio.Path(str(meta_path) + ".tmp")
    await tmp_path.write_text(text, encoding="utf-8")
    await anyio.to_thread.run_sync(Path(str(tmp_path)).replace, Path(str(meta_path)))
    logger.debug(f"History meta removed for session {session_id!r}")
