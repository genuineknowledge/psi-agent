"""Result types shared by every experimental Router strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CompletionResult:
    """One fully accumulated upstream Chat Completions response."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""


__all__ = ["CompletionResult"]
