"""Immutable configuration and selection types for single-target routing."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import RouterTarget

RoutingTarget = RouterTarget


@dataclass(frozen=True)
class RoutingConfig:
    """Validated configuration for one routing service."""

    session_socket: str
    selector_socket: str
    targets: tuple[RoutingTarget, ...] | list[RoutingTarget]
    selector_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_selection_chars: int = 12_000

    def __post_init__(self) -> None:
        session_socket = self.session_socket.strip()
        selector_socket = self.selector_socket.strip()
        if not session_socket:
            raise ValueError("session_socket must be a non-empty string")
        if not selector_socket:
            raise ValueError("selector_socket must be a non-empty string")
        if selector_socket == session_socket:
            raise ValueError("selector_socket must not equal session_socket")
        if not isinstance(self.targets, list | tuple) or not self.targets:
            raise ValueError("targets must contain at least one RoutingTarget")

        targets = tuple(self.targets)
        if any(not isinstance(target, RoutingTarget) for target in targets):
            raise ValueError("targets must contain only RoutingTarget values")
        candidate_ids = [target.candidate_id for target in targets]
        sockets = [target.socket for target in targets]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("target candidate_id values must be unique")
        if len(sockets) != len(set(sockets)):
            raise ValueError("target socket values must be unique")
        if session_socket in sockets:
            raise ValueError("a target socket must not equal session_socket")

        for name in ("selector_timeout", "target_timeout"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number or None")
        if (
            not isinstance(self.max_selection_chars, int)
            or isinstance(self.max_selection_chars, bool)
            or self.max_selection_chars <= 0
        ):
            raise ValueError("max_selection_chars must be a positive integer")

        object.__setattr__(self, "session_socket", session_socket)
        object.__setattr__(self, "selector_socket", selector_socket)
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True)
class SelectionResult:
    """A validated selector decision and its private target mapping."""

    candidate_id: str
    target: RoutingTarget


__all__ = ["RoutingConfig", "RoutingTarget", "SelectionResult"]
