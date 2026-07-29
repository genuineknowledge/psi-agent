"""Gateway path defaults (agent / workspace / AppData root).

What this module is for
-----------------------
Callers that create Sessions (spa v1/v2, Feishu, haitun ``sessions_create``, …)
need a shared answer to: "what is the default agent package?" and "what is the
default user workspace?". ``GET /defaults`` and ``SessionManager`` both use
these resolvers.

AppData path helpers live in ``psi_agent._appdata`` (Session-safe; no circular
import). This module re-exports them for existing Gateway / tool call sites.

Soft default (agent)
--------------------
If CLI ``--default-agent`` is empty:

1. Prefer ``cwd/examples/haitun-workspace`` when present (repo-local Gateway).
2. Else if *cwd itself* looks like a haitun agent package (``tools/`` + ``skills/``
   directories) — the Inno install layout, where ``{app}`` *is* the workspace —
   use cwd. This keeps ``psi-agent.exe gateway`` usable from the install dir
   even without the ``haitun.exe`` launcher flags.
3. Otherwise agent stays ``\"\"`` → Session single-root compat (agent ≡ workspace).

Soft default (workspace)
------------------------
If CLI ``--default-workspace`` is empty, announce ``{Desktop}/haitun交付``
(**path only** — do not mkdir here). Ordinary users get deliverables on the
Desktop without picking a folder; power users override via CLI / spa settings.
Intentional: mkdir only in ``SessionManager.create`` (start chat / new task),
so opening Haitun does not leave an empty Desktop folder. Not AppData.
"""

from __future__ import annotations

import anyio
import platformdirs

from psi_agent._appdata import (
    appdata_history_path,
    appdata_state_dir,
    appdata_state_latest_path,
    appdata_todo_path,
    legacy_history_path,
    legacy_state_latest_path,
    legacy_todo_path,
    resolve_appdata_root,
    resolve_history_read_path,
    resolve_state_read_path,
    resolve_todo_read_path,
)

# Soft default under the OS Desktop — layered for non-technical users.
DEFAULT_USER_WORKSPACE_NAME = "haitun交付"

__all__ = [
    "DEFAULT_USER_WORKSPACE_NAME",
    "appdata_history_path",
    "appdata_state_dir",
    "appdata_state_latest_path",
    "appdata_todo_path",
    "ensure_workspace_dir",
    "legacy_history_path",
    "legacy_state_latest_path",
    "legacy_todo_path",
    "resolve_appdata_root",
    "resolve_default_agent",
    "resolve_default_workspace",
    "resolve_history_read_path",
    "resolve_state_read_path",
    "resolve_todo_read_path",
]


async def resolve_default_workspace(explicit: str = "") -> str:
    """Absolute user workspace path (announce only — does not create).

    *explicit* non-empty → resolve that path.
    Empty → ``{Desktop}/haitun交付`` via ``platformdirs.user_desktop_dir``
    (never hand-written ``%USERPROFILE%``). Directory creation is deferred to
    ``ensure_workspace_dir`` at Session create time.
    """
    raw = explicit.strip()
    if raw:
        return str(await anyio.Path(raw).resolve())
    # Sync platformdirs call is path math only (no IO); fine inside async.
    desktop = anyio.Path(platformdirs.user_desktop_dir())
    ws = desktop / DEFAULT_USER_WORKSPACE_NAME
    return str(await ws.resolve())


async def ensure_workspace_dir(path: str) -> str:
    """Create *path* if missing; return absolute path.

    Call from Session spawn only (``SessionManager.create``), not from
    ``GET /defaults`` / Gateway boot — so the soft Desktop folder appears only
    when the user actually starts a conversation.
    """
    ws = anyio.Path(path.strip())
    await ws.mkdir(parents=True, exist_ok=True)
    return str(await ws.resolve())


async def resolve_default_agent(explicit: str = "") -> str:
    """Absolute agent package path, or ``\"\"`` for Session workspace fallback."""
    raw = explicit.strip()
    if raw:
        return str(await anyio.Path(raw).resolve())
    cwd = await anyio.Path.cwd()
    # Soft default for developers who start Gateway from the repo root.
    candidate = cwd / "examples" / "haitun-workspace"
    if await candidate.is_dir():
        return str(await candidate.resolve())
    # Soft default for Windows install layout: {app} IS haitun-workspace
    # (tools/ + skills/ at cwd; no examples/ nesting).
    if await (cwd / "tools").is_dir() and await (cwd / "skills").is_dir():
        return str(await cwd.resolve())
    return ""
