from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Upstream:
    socket: str
    description: str


@dataclass(frozen=True)
class RouteDecision:
    candidate: int
    reason: str


def _required_text(item: dict[str, Any], key: str, location: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def parse_upstreams(raw: list[str]) -> tuple[Upstream, ...]:
    if not raw:
        raise ValueError("--upstream must provide at least one JSON object")
    targets: list[Upstream] = []
    allowed = {"socket", "description"}
    for index, encoded in enumerate(raw):
        location = f"upstream[{index}]"
        try:
            value: Any = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{location} must be valid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be a JSON object")
        missing = allowed - set(value)
        if missing:
            raise ValueError(f"{location} has missing fields: {sorted(missing)!r}")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"{location} has unsupported fields: {sorted(unknown)!r}")
        target = Upstream(
            socket=_required_text(value, "socket", location),
            description=_required_text(value, "description", location),
        )
        targets.append(target)
    return tuple(targets)
