"""Per-task session / path identity for in-process tool calls.

Gateway runs many Sessions in one process, so ``sys.argv`` is the gateway CLI
and cannot identify which Session is executing a tool. Tools that need a
session id (e.g. AppData ``todo``) should call ``get_session_id()``.
Tools that need the open folder or agent package should call
``get_workspace()`` / ``get_agent()`` (or haitun ``resolve_*`` helpers).

``SessionAgent.run`` enters ``runtime_scope`` for the duration of a turn;
anyio tasks started from that context inherit the values.

**刻意为之**：不在此处写入进程全局 ``os.environ["WORKSPACE_DIR"]`` —
多 Session 并发会互相覆盖。子进程工具应把路径放进 *child* ``env=``。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_session_id: ContextVar[str] = ContextVar("psi_session_id", default="")
_workspace: ContextVar[str] = ContextVar("psi_workspace", default="")
_agent: ContextVar[str] = ContextVar("psi_agent", default="")


def get_session_id() -> str:
    """Return the Session id bound to the current async context, or ``\"\"``."""
    return _session_id.get()


def set_session_id(session_id: str) -> Token[str]:
    """Bind *session_id* for the current context; return a reset token."""
    return _session_id.set(session_id.strip())


def reset_session_id(token: Token[str]) -> None:
    """Restore the previous ContextVar value."""
    _session_id.reset(token)


def get_workspace() -> str:
    """User workspace (open folder) for the current turn, or ``\"\"``."""
    return _workspace.get()


def get_agent() -> str:
    """Agent package path for the current turn, or ``\"\"``."""
    return _agent.get()


@contextmanager
def session_id_scope(session_id: str) -> Iterator[None]:
    """Bind *session_id* until the ``with`` block exits (incl. across yields)."""
    token = set_session_id(session_id)
    try:
        yield
    finally:
        reset_session_id(token)


@contextmanager
def path_scope(*, workspace: str = "", agent: str = "") -> Iterator[None]:
    """Bind user workspace + agent package paths for the current turn."""
    wt = _workspace.set(workspace.strip())
    at = _agent.set(agent.strip())
    try:
        yield
    finally:
        _workspace.reset(wt)
        _agent.reset(at)


@contextmanager
def runtime_scope(*, session_id: str, workspace: str = "", agent: str = "") -> Iterator[None]:
    """Bind session id + workspace + agent for one agent turn."""
    with session_id_scope(session_id), path_scope(workspace=workspace, agent=agent):
        yield
