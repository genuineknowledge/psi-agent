"""Immutable configuration for serial fallback routing."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import RouterTarget


@dataclass(frozen=True)
class FallbackConfig:
    """Validated configuration for one serial fallback service."""

    session_socket: str
    targets: tuple[RouterTarget, ...] | list[RouterTarget]
    target_timeout: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_socket, str):
            raise ValueError("session_socket must be a non-empty string")
        session_socket = self.session_socket.strip()
        if not session_socket:
            raise ValueError("session_socket must be a non-empty string")
        if not isinstance(self.targets, list | tuple) or not self.targets:
            raise ValueError("targets must contain at least one RouterTarget")

        targets = tuple(self.targets)
        if any(not isinstance(target, RouterTarget) for target in targets):
            raise ValueError("targets must contain only RouterTarget values")
        candidate_ids = [target.candidate_id for target in targets]
        sockets = [target.socket for target in targets]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("target candidate_id values must be unique")
        if len(sockets) != len(set(sockets)):
            raise ValueError("target socket values must be unique")
        if session_socket in sockets:
            raise ValueError("a target socket must not equal session_socket")
        if self.target_timeout is not None and (
            not isinstance(self.target_timeout, int | float)
            or isinstance(self.target_timeout, bool)
            or not math.isfinite(self.target_timeout)
            or self.target_timeout <= 0
        ):
            raise ValueError("target_timeout must be a finite positive number or None")

        object.__setattr__(self, "session_socket", session_socket)
        object.__setattr__(self, "targets", targets)


__all__ = ["FallbackConfig"]
