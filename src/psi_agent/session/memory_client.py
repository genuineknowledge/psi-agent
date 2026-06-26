from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Any

import aiohttp


@dataclass(frozen=True)
class MemoryClientConfig:
    base_url: str
    timeout_seconds: float
    workspace_id: str
    user_id: str
    agent_id: str
    session_id: str | None


def build_memory_client_config(env: Mapping[str, str] | None = None) -> MemoryClientConfig:
    env_map = os.environ if env is None else env
    return MemoryClientConfig(
        base_url=(env_map.get("PSI_MEMORY_BASE_URL") or "http://127.0.0.1:8700").rstrip("/"),
        timeout_seconds=float(env_map.get("PSI_MEMORY_TIMEOUT_SECONDS") or "2.0"),
        workspace_id=env_map.get("PSI_MEMORY_WORKSPACE_ID") or "dolphin",
        user_id=env_map.get("PSI_MEMORY_USER_ID") or env_map.get("USER") or "user",
        agent_id=env_map.get("PSI_MEMORY_AGENT_ID") or "dolphin",
        session_id=env_map.get("PSI_MEMORY_SESSION_ID") or None,
    )


class FusionMemoryClient:
    def __init__(self, config: MemoryClientConfig) -> None:
        self._config = config

    async def ingest_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        turn_id: str | None,
        turn_index: int | None,
        ended_with_error: bool,
    ) -> None:
        payload = {
            "messages": messages,
            "scope": {
                "workspace_id": self._config.workspace_id,
                "user_id": self._config.user_id,
                "agent_id": self._config.agent_id,
                "session_id": self._config.session_id,
                "app_id": "dolphin",
            },
            "turn_id": turn_id,
            "turn_index": turn_index,
            "metadata": {"ended_with_error": ended_with_error},
        }
        timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self._config.base_url}/ingest-turn", json=payload) as response:
                response.raise_for_status()
