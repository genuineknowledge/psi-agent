"""Versioned Router progress events shared across component boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast
from uuid import UUID

type RouterStatusMode = Literal["routing", "aggregation", "fallback"]
type RouterStatusPhase = Literal[
    "selecting",
    "generating",
    "collecting",
    "synthesizing",
    "attempting",
    "switching",
    "replaying",
]

_PHASES_BY_MODE: dict[RouterStatusMode, frozenset[RouterStatusPhase]] = {
    "routing": frozenset({"selecting", "generating"}),
    "aggregation": frozenset({"collecting", "synthesizing"}),
    "fallback": frozenset({"attempting", "switching", "replaying"}),
}


def _non_negative_integer(*, value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _positive_integer(*, value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class RouterStatus:
    """One validated, UI-safe Router lifecycle snapshot."""

    trace_id: str
    mode: RouterStatusMode
    phase: RouterStatusPhase
    depth: int = 0
    completed: int | None = None
    total: int | None = None
    attempt: int | None = None
    degraded: bool = False
    version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.trace_id, str):
            raise ValueError("trace_id must be a UUID string")
        try:
            trace_id = str(UUID(self.trace_id.strip()))
        except (ValueError, AttributeError) as error:
            raise ValueError("trace_id must be a UUID string") from error
        if not isinstance(self.mode, str) or self.mode not in _PHASES_BY_MODE:
            raise ValueError("mode must be routing, aggregation, or fallback")
        if not isinstance(self.phase, str) or self.phase not in _PHASES_BY_MODE[self.mode]:
            raise ValueError(f"phase {self.phase!r} is not valid for mode {self.mode!r}")

        depth = _non_negative_integer(value=self.depth, label="depth")
        completed = self.completed
        total = self.total
        attempt = self.attempt
        if total is not None:
            total = _positive_integer(value=total, label="total")
        if completed is not None:
            completed = _non_negative_integer(value=completed, label="completed")
            if self.mode != "aggregation":
                raise ValueError("completed is only valid for aggregation")
            if total is None:
                raise ValueError("completed requires total")
            if completed > total:
                raise ValueError("completed cannot exceed total")
        if attempt is not None:
            attempt = _positive_integer(value=attempt, label="attempt")
            if self.mode != "fallback":
                raise ValueError("attempt is only valid for fallback")
            if total is None:
                raise ValueError("attempt requires total")
            if attempt > total:
                raise ValueError("attempt cannot exceed total")
        if total is not None and self.mode == "routing":
            raise ValueError("total is not valid for routing")
        if not isinstance(self.degraded, bool):
            raise ValueError("degraded must be a boolean")
        if self.degraded and self.mode != "aggregation":
            raise ValueError("degraded is only valid for aggregation")

        object.__setattr__(self, "trace_id", trace_id)
        object.__setattr__(self, "depth", depth)
        object.__setattr__(self, "completed", completed)
        object.__setattr__(self, "total", total)
        object.__setattr__(self, "attempt", attempt)

    def to_dict(self) -> dict[str, Any]:
        """Serialize without leaking candidate IDs, prompts, or transport details."""

        result: dict[str, Any] = {
            "version": self.version,
            "trace_id": self.trace_id,
            "mode": self.mode,
            "phase": self.phase,
            "depth": self.depth,
        }
        if self.completed is not None:
            result["completed"] = self.completed
        if self.total is not None:
            result["total"] = self.total
        if self.attempt is not None:
            result["attempt"] = self.attempt
        if self.degraded:
            result["degraded"] = True
        return result

    @classmethod
    def from_dict(cls, value: object) -> RouterStatus:
        """Parse version 1 while tolerating unknown additive fields."""

        if not isinstance(value, dict):
            raise ValueError("router_status must be an object")
        typed = cast(dict[str, Any], value)
        version = typed.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != 1:
            raise ValueError("router_status version must be 1")
        return cls(
            trace_id=cast(str, typed.get("trace_id")),
            mode=cast(RouterStatusMode, typed.get("mode")),
            phase=cast(RouterStatusPhase, typed.get("phase")),
            depth=typed.get("depth", 0),
            completed=typed.get("completed"),
            total=typed.get("total"),
            attempt=typed.get("attempt"),
            degraded=typed.get("degraded", False),
        )

    def to_event(self) -> dict[str, Any]:
        """Wrap this status in the framework's single-choice SSE shape."""

        return {
            "choices": [
                {
                    "index": 0,
                    "delta": {"router_status": self.to_dict()},
                    "finish_reason": None,
                }
            ]
        }


def router_status_from_event(event: object) -> RouterStatus | None:
    """Extract and validate Router status from one single-choice event."""

    if not isinstance(event, dict):
        raise ValueError("Router event must be an object with a single choice")
    choices = event.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("Router event must contain a single choice")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("Router event single choice must be an object")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        raise ValueError("Router event single choice delta must be an object")
    typed_delta = cast(dict[str, Any], delta)
    if "router_status" not in typed_delta:
        return None
    if set(typed_delta) != {"router_status"}:
        raise ValueError("router_status must use an independent delta")
    if choice.get("finish_reason") is not None:
        raise ValueError("router_status finish_reason must be null")
    return RouterStatus.from_dict(typed_delta["router_status"])


__all__ = [
    "RouterStatus",
    "RouterStatusMode",
    "RouterStatusPhase",
    "router_status_from_event",
]
