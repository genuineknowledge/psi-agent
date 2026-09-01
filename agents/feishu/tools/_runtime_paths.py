"""Step 3 — resolve user-workspace vs agent-package roots for tools.

Session binds ``get_workspace()`` / ``get_agent()`` per turn (see
``psi_agent.session.runtime_context``). Prefer those ContextVars over the
legacy ``WORKSPACE_DIR`` env and the tools-package parent fallback.

**Not AppData memory for files** — relative IO stays on workspace/agent. Todos /
history / Gateway ``state/`` live under AppData (Steps 4B-4D).
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio

try:
    from psi_agent.session.runtime_context import get_agent as _runtime_agent
    from psi_agent.session.runtime_context import get_workspace as _runtime_workspace
except ImportError:  # pragma: no cover — standalone import without editable install

    def _runtime_workspace() -> str:
        return ""

    def _runtime_agent() -> str:
        return ""


def package_fallback() -> str:
    """``agents/feishu`` when this file lives under ``tools/``."""
    return str(Path(__file__).resolve().parents[1])


def workspace_dir(explicit: str = "") -> str:
    """User workspace root (relative file IO / schedules / todos / flows).

    Priority: explicit arg → ContextVar ``get_workspace()`` → ``WORKSPACE_DIR``
    → package fallback.
    """
    for candidate in (explicit, _runtime_workspace(), os.environ.get("WORKSPACE_DIR", "")):
        text = (candidate or "").strip()
        if text:
            return text
    return package_fallback()


def agent_dir(explicit: str = "") -> str:
    """Agent package root (skills / SOUL / capability files).

    Priority: explicit arg → ContextVar ``get_agent()`` → ``workspace_dir()``
    (empty agent means same root as workspace — Session contract).
    """
    for candidate in (explicit, _runtime_agent()):
        text = (candidate or "").strip()
        if text:
            return text
    return workspace_dir()


def resolve_workspace(raw: str = "") -> anyio.Path:
    """``anyio.Path`` for the user workspace (empty *raw* uses ``workspace_dir``)."""
    return anyio.Path(workspace_dir(raw))


def resolve_agent(raw: str = "") -> anyio.Path:
    """``anyio.Path`` for the agent package root."""
    return anyio.Path(agent_dir(raw))


def resolve_under(root: str | anyio.Path | Path, path: str) -> anyio.Path:
    """Join *path* under *root* when relative; keep absolute paths as-is."""
    raw = (path or "").strip() or "."
    candidate = Path(raw)
    if candidate.is_absolute():
        return anyio.Path(str(candidate))
    return anyio.Path(str(root)) / raw


def resolve_user_path(path: str, *, workspace_raw: str = "") -> anyio.Path:
    """Resolve a tool file path against the user workspace."""
    return resolve_under(workspace_dir(workspace_raw), path)
