"""Result types shared by every experimental Router strategy."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypeIs, cast

_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PROTECTED_OVERRIDE_KEYS = frozenset({"messages", "model", "routing", "stream"})


class RouterMode(StrEnum):
    """The strategy type served by an experimental Router."""

    ROUTING = "routing"
    AGGREGATION = "aggregation"
    FALLBACK = "fallback"


type RouterBackendType = Literal["ai", "router"]
type RouterUpstream = tuple[str, str] | tuple[str, str, RouterBackendType]
type RoutingScopeKey = tuple[str, tuple[str, ...]]


def is_candidate_id(value: object) -> TypeIs[str]:
    """Return whether a value is a stable public Router candidate ID."""

    return isinstance(value, str) and _CANDIDATE_ID.fullmatch(value) is not None


def normalize_request_overrides(*, value: object, label: str) -> dict[str, Any]:
    """Validate and detach one local set of public completion parameter overrides."""

    if not isinstance(value, dict) or any(not isinstance(key, str) or not key for key in value):
        raise ValueError(f"{label} must be an object with non-empty string keys")
    typed = cast(dict[str, Any], value)
    protected = _PROTECTED_OVERRIDE_KEYS & set(typed)
    if protected:
        names = ", ".join(sorted(protected))
        raise ValueError(f"{label} cannot override protected field(s): {names}")
    return deepcopy(typed)


@dataclass(frozen=True)
class RouterTarget:
    """One Router-visible candidate mapped to a private transport address."""

    candidate_id: str
    socket: str
    description: str
    backend_type: RouterBackendType = "ai"
    timeout: float | None = None
    request_overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str):
            raise ValueError("candidate_id must match the Router candidate ID format")
        if not isinstance(self.socket, str):
            raise ValueError("target socket must be a non-empty string")
        if not isinstance(self.description, str):
            raise ValueError("target description must be a non-empty string")
        candidate_id = self.candidate_id.strip()
        socket = self.socket.strip()
        description = self.description.strip()
        if not is_candidate_id(candidate_id):
            raise ValueError("candidate_id must match the Router candidate ID format")
        if not socket:
            raise ValueError("target socket must be a non-empty string")
        if not description:
            raise ValueError("target description must be a non-empty string")
        if not isinstance(self.backend_type, str) or self.backend_type not in {"ai", "router"}:
            raise ValueError("target backend_type must be 'ai' or 'router'")
        if self.timeout is not None and (
            not isinstance(self.timeout, int | float)
            or isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ValueError("target timeout must be a finite positive number or None")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "socket", socket)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "timeout", float(self.timeout) if self.timeout is not None else None)
        object.__setattr__(
            self,
            "request_overrides",
            normalize_request_overrides(
                value=self.request_overrides,
                label=f"request overrides for {candidate_id}",
            ),
        )


@dataclass(frozen=True)
class CompletionResult:
    """One fully accumulated upstream Chat Completions response."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""


@dataclass(frozen=True)
class BufferedCompletion:
    """One validated completion together with its original ordered SSE events."""

    events: tuple[dict[str, Any], ...]
    completion: CompletionResult


__all__ = [
    "BufferedCompletion",
    "CompletionResult",
    "RouterBackendType",
    "RouterMode",
    "RouterTarget",
    "RouterUpstream",
    "RoutingScopeKey",
    "is_candidate_id",
    "normalize_request_overrides",
]
