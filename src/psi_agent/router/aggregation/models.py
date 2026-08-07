"""Immutable contracts and evidence compaction for broadcast aggregation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from ..models import RouterTarget, normalize_request_overrides

_TRUNCATION_MARKER = "…<truncated>…"


@dataclass(frozen=True)
class AggregationFeedback:
    """One candidate's complete response, retained as synthesis evidence."""

    candidate_id: str
    description: str
    status: Literal["success", "error"]
    finish_reason: str = ""
    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    error_type: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(deepcopy(self.tool_calls)))


@dataclass(frozen=True)
class AggregationConfig:
    """Validated configuration for one broadcast aggregation service."""

    session_socket: str
    aggregator_socket: str
    targets: tuple[RouterTarget, ...] | list[RouterTarget]
    aggregator_timeout: float | None = 30.0
    target_timeout: float | None = None
    max_context_chars: int = 12_000
    require_all_targets: bool = False
    aggregator_request_overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.session_socket, str):
            raise ValueError("session_socket must be a non-empty string")
        if not isinstance(self.aggregator_socket, str):
            raise ValueError("aggregator_socket must be a non-empty string")
        session_socket = self.session_socket.strip()
        aggregator_socket = self.aggregator_socket.strip()
        if not session_socket:
            raise ValueError("session_socket must be a non-empty string")
        if not aggregator_socket:
            raise ValueError("aggregator_socket must be a non-empty string")
        if aggregator_socket == session_socket:
            raise ValueError("aggregator_socket must not equal session_socket")
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
        if aggregator_socket in sockets:
            raise ValueError("aggregator_socket must not equal a target socket")

        for name in ("aggregator_timeout", "target_timeout"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number or None")
        if (
            not isinstance(self.max_context_chars, int)
            or isinstance(self.max_context_chars, bool)
            or self.max_context_chars <= 0
        ):
            raise ValueError("max_context_chars must be a positive integer")
        if not isinstance(self.require_all_targets, bool):
            raise ValueError("require_all_targets must be a boolean")

        object.__setattr__(self, "session_socket", session_socket)
        object.__setattr__(self, "aggregator_socket", aggregator_socket)
        object.__setattr__(self, "targets", targets)
        object.__setattr__(
            self,
            "aggregator_request_overrides",
            normalize_request_overrides(
                value=self.aggregator_request_overrides,
                label="aggregator_request_overrides",
            ),
        )


def compact_feedback(*, feedback: Sequence[AggregationFeedback], max_context_chars: int) -> list[dict[str, Any]]:
    """Copy feedback into deterministic, bounded evidence payloads."""

    payload = [
        {
            "candidate_id": item.candidate_id,
            "description": item.description,
            "status": item.status,
            "finish_reason": item.finish_reason,
            "content": item.content,
            "tool_calls": deepcopy(item.tool_calls),
            "error_type": item.error_type,
            "error": item.error,
        }
        for item in feedback
    ]
    dynamic_fields: list[tuple[dict[str, Any], str | None, str]] = []
    for item, item_payload in zip(feedback, payload, strict=True):
        if item.status != "success":
            continue
        if isinstance(item_payload["content"], str):
            dynamic_fields.append((item_payload, None, item_payload["content"]))
        tool_calls = item_payload["tool_calls"]
        if not isinstance(tool_calls, tuple | list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                dynamic_fields.append((function, "arguments", arguments))

    if sum(len(value) for _, _, value in dynamic_fields) <= max_context_chars:
        return payload

    base, remainder = divmod(max_context_chars, len(dynamic_fields))
    for index, (container, key, value) in enumerate(dynamic_fields):
        quota = base + (1 if index < remainder else 0)
        compacted = _compact_string(value=value, quota=quota)
        if key is None:
            container["content"] = compacted
        else:
            container[key] = compacted
    return payload


def _compact_string(*, value: str, quota: int) -> str:
    if len(value) <= quota:
        return value
    if quota == 0:
        return _TRUNCATION_MARKER
    leading = (quota + 1) // 2
    trailing = quota // 2
    return f"{value[:leading]}{_TRUNCATION_MARKER}{value[-trailing:] if trailing else ''}"


__all__ = ["AggregationConfig", "AggregationFeedback", "compact_feedback"]
