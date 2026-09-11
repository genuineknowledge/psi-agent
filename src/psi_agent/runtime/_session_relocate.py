"""Copy AppData artifacts from one session id to another (relocate support).

Used by ``POST /sessions/{id}/relocate``: create a new Session with a new
workspace/agent, copy durable rows, then delete the old Session. History and
todos live under AppData keyed by session id, so a new id needs an explicit
byte copy — REST has no history-write API for the SPA.
"""

from __future__ import annotations

import anyio
from loguru import logger

from psi_agent._appdata import (
    appdata_history_path,
    appdata_todo_path,
    appdata_todo_segments_path,
    resolve_appdata_root,
    resolve_history_read_path,
    resolve_todo_read_path,
)


async def _copy_bytes(src: anyio.Path, dst: anyio.Path) -> bool:
    try:
        data = await src.read_bytes()
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning(f"Failed to read {src!s} for session relocate: {e!r}")
        return False
    try:
        await dst.parent.mkdir(parents=True, exist_ok=True)
        await dst.write_bytes(data)
    except OSError as e:
        logger.warning(f"Failed to write {dst!s} for session relocate: {e!r}")
        return False
    return True


async def copy_session_artifacts(
    *,
    old_session_id: str,
    new_session_id: str,
    old_workspace: str,
    appdata: str = "",
) -> None:
    """Copy history JSONL + todo files to ``new_session_id`` (best-effort per file)."""
    appdata_root = appdata.strip() or await resolve_appdata_root()
    hist_src = await resolve_history_read_path(
        appdata_root=appdata_root,
        workspace=old_workspace,
        session_id=old_session_id,
    )
    hist_dst = appdata_history_path(appdata_root, new_session_id)
    if await _copy_bytes(hist_src, hist_dst):
        logger.info(
            f"Relocate copied history {old_session_id!r} -> {new_session_id!r}"
        )

    todo_src = await resolve_todo_read_path(
        appdata_root=appdata_root,
        workspace=old_workspace,
        session_id=old_session_id,
    )
    todo_dst = appdata_todo_path(appdata_root, new_session_id)
    if await _copy_bytes(todo_src, todo_dst):
        logger.info(f"Relocate copied todos {old_session_id!r} -> {new_session_id!r}")

    seg_src = appdata_todo_segments_path(appdata_root, old_session_id)
    seg_dst = appdata_todo_segments_path(appdata_root, new_session_id)
    if await _copy_bytes(seg_src, seg_dst):
        logger.info(
            f"Relocate copied todo segments {old_session_id!r} -> {new_session_id!r}"
        )


async def delete_session_todo_files(
    session_id: str,
    *,
    appdata: str = "",
) -> None:
    """Best-effort remove AppData todo + segments for a deleted/relocated session."""
    appdata_root = appdata.strip() or await resolve_appdata_root()
    for path in (
        appdata_todo_path(appdata_root, session_id),
        appdata_todo_segments_path(appdata_root, session_id),
    ):
        try:
            await path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Failed to delete todo artifact {path!s}: {e!r}")
