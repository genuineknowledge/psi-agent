from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionToolContext:
    session_id: str | None
    workspace_path: Path | None
    history_path: Path | None
    history_messages: list[dict[str, Any]]
    latest_user_message: dict[str, Any]
    ai_socket: str


_SESSION_TOOL_CONTEXT: ContextVar[SessionToolContext | None] = ContextVar(
    "SESSION_TOOL_CONTEXT",
    default=None,
)


def get_session_tool_context() -> SessionToolContext | None:
    return _SESSION_TOOL_CONTEXT.get()


@contextmanager
def push_session_tool_context(ctx: SessionToolContext) -> Iterator[None]:
    token = _SESSION_TOOL_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _SESSION_TOOL_CONTEXT.reset(token)
