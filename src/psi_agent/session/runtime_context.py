"""Per-task session / path identity for in-process **workspace tool** calls.

Gateway runs many Sessions in one process, so ``sys.argv`` is the gateway CLI
and cannot identify which Session is executing a tool.

**Scope (keep narrow — see ``session/AGENTS.md``):**

- **Writers**: only ``SessionAgent.run`` and ``SessionAgent.handle_event`` via
  ``runtime_scope``.
- **Readers**: workspace tools only (``get_session_id`` / ``get_workspace`` /
  ``get_agent``). Framework code must use explicit ``workspace_path`` /
  ``agent_path`` / ``Conversation.session_id``, not these getters.
- **Do not** stuff AppData roots, credentials, or Gateway config into
  ContextVars; prefer DI / dataclass fields.

``SessionAgent.run`` enters ``runtime_scope`` for the duration of a turn;
``SessionAgent.handle_event`` enters it while matching and dispatching triggers.
Anyio tasks started from either context inherit the values.

**Where the session-id ContextVar actually lives:** ``psi_agent._session_context``
— a zero-in-project-dependency leaf module, because ``_logging.py`` interpolates
the id into every log line and importing *this* module would drag in
``session/__init__.py``, which imports ``_logging`` right back. The three
session-id names below are re-exports, so every existing
``from psi_agent.session.runtime_context import get_session_id`` keeps working
and there is still exactly one ContextVar. ``workspace`` / ``agent`` stay here:
nothing outside this layer reads them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from posixpath import normpath

from psi_agent._session_context import (
    get_session_id,
    reset_session_id,
    session_id_scope,
    set_session_id,
)

_workspace: ContextVar[str] = ContextVar("psi_workspace", default="")
_agent: ContextVar[str] = ContextVar("psi_agent", default="")
_user_message: ContextVar[str] = ContextVar("psi_user_message", default="")
_workflow_touched: ContextVar[set[str] | None] = ContextVar("psi_workflow_touched", default=None)


def _normalize_tool_path(path: str) -> str:
    """Normalize separators and dot segments for same-turn path matching."""
    return normpath(path.strip().replace("\\", "/"))


def get_workspace() -> str:
    """Workspace path for the current turn, or ``\"\"``."""
    return _workspace.get()


def get_agent() -> str:
    """Agent package path for the current turn, or ``\"\"``."""
    return _agent.get()


def get_user_message() -> str:
    """Exact user text for the current turn, or an empty string."""
    return _user_message.get()


def mark_workflow_touched(path: str) -> None:
    """Mark a workflow path changed by a successful workspace tool call."""
    touched = _workflow_touched.get()
    if touched is not None and path.strip():
        touched.add(_normalize_tool_path(path))


def workflow_was_touched(path: str) -> bool:
    """Whether a path was changed during the current Session turn."""
    touched = _workflow_touched.get()
    return touched is not None and _normalize_tool_path(path) in touched


@contextmanager
def path_scope(*, workspace: str = "", agent: str = "") -> Iterator[None]:
    """Bind workspace + agent package paths for the current turn."""
    wt = _workspace.set(workspace.strip())
    at = _agent.set(agent.strip())
    try:
        yield
    finally:
        _workspace.reset(wt)
        _agent.reset(at)


@contextmanager
def runtime_scope(
    *,
    session_id: str,
    workspace: str = "",
    agent: str = "",
    user_message: str = "",
    workflow_touched: set[str] | None = None,
) -> Iterator[None]:
    """Bind session, path, and authoring metadata for one turn or event dispatch."""
    with session_id_scope(session_id), path_scope(workspace=workspace, agent=agent):
        message_token = _user_message.set(user_message)
        touched_token = _workflow_touched.set(workflow_touched)
        try:
            yield
        finally:
            _workflow_touched.reset(touched_token)
            _user_message.reset(message_token)


# Explicit so the re-exports from ``_session_context`` are part of this module's
# advertised surface rather than looking like unused imports.
__all__ = [
    "get_agent",
    "get_session_id",
    "get_user_message",
    "get_workspace",
    "mark_workflow_touched",
    "path_scope",
    "reset_session_id",
    "runtime_scope",
    "session_id_scope",
    "set_session_id",
    "workflow_was_touched",
]
