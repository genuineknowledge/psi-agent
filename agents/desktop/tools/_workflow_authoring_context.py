"""Turn-local prompt handoff used only by Workflow authoring capture."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_prompt: ContextVar[str] = ContextVar("haitun_workflow_authoring_prompt", default="")


def begin_turn(user_message: dict[str, Any] | None) -> None:
    """Bind the exact ordinary-chat prompt for this agent turn."""
    if not isinstance(user_message, dict):
        _prompt.set("")
        return
    kind = user_message.get("kind", "chat")
    content = user_message.get("content")
    if not isinstance(kind, str) or kind.strip().casefold() != "chat" or not isinstance(content, str):
        _prompt.set("")
        return
    _prompt.set(content)


def current_prompt() -> str:
    """Return the exact prompt bound for this turn, or an empty string."""
    return _prompt.get()


def end_turn() -> None:
    """Clear the prompt after a completed turn."""
    _prompt.set("")
