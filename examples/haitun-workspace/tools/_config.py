from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8700"
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class MemoryConfig:
    base_url: str
    timeout_seconds: float
    workspace_id: str
    user_id: str
    agent_id: str
    session_id: str | None
    app_id: str = "haitun"

    @property
    def allow_cross_session(self) -> bool:
        return self.session_id in {None, ""}

    @property
    def scope(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id or None,
            "app_id": self.app_id,
        }


def _clamp_timeout(raw: str | None) -> float:
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def build_memory_config(env: Mapping[str, str] | None = None) -> MemoryConfig:
    env = os.environ if env is None else env
    return MemoryConfig(
        base_url=(env.get("PSI_MEMORY_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        timeout_seconds=_clamp_timeout(env.get("PSI_MEMORY_TIMEOUT_SECONDS")),
        workspace_id=env.get("PSI_MEMORY_WORKSPACE_ID") or "haitun",
        user_id=env.get("PSI_MEMORY_USER_ID") or env.get("USER") or env.get("USERNAME") or "user",
        agent_id=env.get("PSI_MEMORY_AGENT_ID") or "haitun",
        session_id=env.get("PSI_MEMORY_SESSION_ID") or None,
    )


CONFIG = build_memory_config()
