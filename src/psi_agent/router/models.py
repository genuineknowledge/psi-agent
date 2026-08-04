"""Result types shared by every experimental Router strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RouterMode(StrEnum):
    """The strategy type served by an experimental Router."""

    ROUTING = "routing"
    AGGREGATION = "aggregation"


@dataclass(frozen=True)
class RouterTarget:
    """One Router-visible candidate mapped to a private transport address."""

    candidate_id: str
    socket: str
    description: str

    def __post_init__(self) -> None:
        candidate_id = self.candidate_id.strip()
        socket = self.socket.strip()
        description = self.description.strip()
        if not _CANDIDATE_ID.fullmatch(candidate_id):
            raise ValueError("candidate_id must match the Router candidate ID format")
        if not socket:
            raise ValueError("target socket must be a non-empty string")
        if not description:
            raise ValueError("target description must be a non-empty string")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "socket", socket)
        object.__setattr__(self, "description", description)


@dataclass(frozen=True)
class CompletionResult:
    """One fully accumulated upstream Chat Completions response."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""


__all__ = ["CompletionResult", "RouterMode", "RouterTarget"]
