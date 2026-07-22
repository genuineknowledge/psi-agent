"""Per-task session identity for in-process tool calls.

Gateway runs many Sessions in one process, so ``sys.argv`` is the gateway CLI
and cannot identify which Session is executing a tool. Tools that need a
session id (e.g. workspace ``todo``) should call ``get_session_id()``.

``SessionAgent.run`` enters ``session_id_scope`` for the duration of a turn;
anyio tasks started from that context inherit the value.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_session_id: ContextVar[str] = ContextVar("psi_session_id", default="")


def get_session_id() -> str:
    """Return the Session id bound to the current async context, or ``\"\"``."""
    return _session_id.get()


def set_session_id(session_id: str) -> Token[str]:
    """Bind *session_id* for the current context; return a reset token."""
    return _session_id.set(session_id.strip())


def reset_session_id(token: Token[str]) -> None:
    """Restore the previous ContextVar value."""
    _session_id.reset(token)


@contextmanager
def session_id_scope(session_id: str) -> Iterator[None]:
    """Bind *session_id* until the ``with`` block exits (incl. across yields)."""
    token = set_session_id(session_id)
    try:
        yield
    finally:
        reset_session_id(token)
