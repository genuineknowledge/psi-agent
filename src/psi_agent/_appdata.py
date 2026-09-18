"""AppData memory-area path helpers.

Shared by Session, Gateway, and workspace tools. Lives outside
``psi_agent.gateway`` so Session can import it without a circular import
(``gateway`` → ``Session`` → ``conversation`` → here).

``resolve_appdata_root`` uses ``platformdirs.user_data_dir`` (never hardcoded
``%AppData%``). Step 4B relocates todos; Step 4C relocates history; Step 4D
relocates Gateway ``state/`` — all with legacy dual-read where noted.
"""

from __future__ import annotations

import os

import anyio
import platformdirs

# Directory name under the OS user-data root (not the Gateway --app-name label).
_APPDATA_APPNAME = "Haitun"
_APPDATA_ENV = "PSI_APPDATA"


async def resolve_appdata_root(explicit: str = "") -> str:
    """Absolute AppData (memory) root.

    Priority: *explicit* CLI → ``PSI_APPDATA`` env → ``platformdirs.user_data_dir``.
    """
    raw = explicit.strip() or os.environ.get(_APPDATA_ENV, "").strip()
    if raw:
        return str(await anyio.Path(raw).resolve())
    # Sync platformdirs call is path math only (no IO); fine inside async.
    return str(await anyio.Path(platformdirs.user_data_dir(appname=_APPDATA_APPNAME, appauthor=False)).resolve())


def legacy_todo_path(workspace: str, session_id: str) -> anyio.Path:
    """Pre-AppData path: ``{workspace}/.psi/todos/{session_id}.json``."""
    return anyio.Path(workspace) / ".psi" / "todos" / f"{session_id}.json"


def appdata_todo_path(appdata_root: str, session_id: str) -> anyio.Path:
    """AppData path (Step 4B): ``{appdata}/todos/{session_id}.json``."""
    return anyio.Path(appdata_root) / "todos" / f"{session_id}.json"


def appdata_todo_segments_path(appdata_root: str, session_id: str) -> anyio.Path:
    """AppData path: ``{appdata}/todos/{session_id}.segments.json`` (sub-task history)."""
    return anyio.Path(appdata_root) / "todos" / f"{session_id}.segments.json"


async def resolve_todo_read_path(
    *,
    appdata_root: str,
    workspace: str,
    session_id: str,
) -> anyio.Path:
    """Dual-read: prefer AppData file if present, else legacy workspace file."""
    primary = appdata_todo_path(appdata_root, session_id)
    if await primary.is_file():
        return primary
    legacy = legacy_todo_path(workspace, session_id)
    if await legacy.is_file():
        return legacy
    return primary


def legacy_history_path(workspace: str, session_id: str) -> anyio.Path:
    """Pre-AppData path: ``{workspace}/histories/{session_id}.jsonl``."""
    return anyio.Path(workspace) / "histories" / f"{session_id}.jsonl"


def appdata_history_path(appdata_root: str, session_id: str) -> anyio.Path:
    """AppData path (Step 4C): ``{appdata}/histories/{session_id}.jsonl``."""
    return anyio.Path(appdata_root) / "histories" / f"{session_id}.jsonl"


def appdata_uploads_path(appdata_root: str, session_id: str) -> anyio.Path:
    """AppData path: ``{appdata}/uploads/{session_id}.jsonl``.

    Ledger of **inbound** files the server itself wrote for this session (one JSON
    object per line, ``{"path": ...}``). ``ChatManager._save_upload`` appends to it at
    the moment the bytes land on disk; ``GET /feishu/sessions/{id}/files`` treats it as
    part of the deliverable whitelist.

    It exists because the whitelist must not be derivable from anything the user can
    write: the previous source was ``[RECV:…]`` markers regex-scraped out of the user's
    own message text, so a user could name any file on the server and the download
    route would serve it. A ledger written by the side that did the write is the only
    version of this list that cannot be forged from the chat box.
    """
    return anyio.Path(appdata_root) / "uploads" / f"{session_id}.jsonl"


async def resolve_history_read_path(
    *,
    appdata_root: str,
    workspace: str,
    session_id: str,
) -> anyio.Path:
    """Dual-read: prefer AppData history if present, else legacy workspace file."""
    primary = appdata_history_path(appdata_root, session_id)
    if await primary.is_file():
        return primary
    legacy = legacy_history_path(workspace, session_id)
    if await legacy.is_file():
        return legacy
    return primary


def legacy_state_dir() -> anyio.Path:
    """Pre-AppData Gateway state dir: ``./state`` relative to process cwd."""
    return anyio.Path("state")


def legacy_state_latest_path() -> anyio.Path:
    """Pre-AppData path: ``./state/latest.json`` (cwd-relative)."""
    return legacy_state_dir() / "latest.json"


def appdata_state_dir(appdata_root: str) -> anyio.Path:
    """AppData path (Step 4D): ``{appdata}/state/``."""
    return anyio.Path(appdata_root) / "state"


def appdata_state_latest_path(appdata_root: str) -> anyio.Path:
    """AppData path (Step 4D): ``{appdata}/state/latest.json``."""
    return appdata_state_dir(appdata_root) / "latest.json"


def appdata_ui_prefs_path(appdata_root: str) -> anyio.Path:
    """AppData path: ``{appdata}/ui-prefs.json``.

    Deliberately **not** under ``state/``: that dir holds ``latest.json`` (manager
    snapshots) plus a timestamped copy per startup. UI preferences are neither a
    manager snapshot nor worth versioning, so they sit at the AppData root.
    """
    return anyio.Path(appdata_root) / "ui-prefs.json"


async def resolve_state_read_path(*, appdata_root: str) -> anyio.Path:
    """Dual-read: prefer AppData ``state/latest.json``, else legacy cwd ``state/``."""
    primary = appdata_state_latest_path(appdata_root)
    if await primary.is_file():
        return primary
    legacy = legacy_state_latest_path()
    if await legacy.is_file():
        return legacy
    return primary
