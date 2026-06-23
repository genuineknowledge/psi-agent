"""Configuration helpers for Fusion Memory integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite

MIN_MEMORY_TIMEOUT_SECONDS = 0.1
MAX_MEMORY_TIMEOUT_SECONDS = 2.0
MIN_MEMORY_RETRIEVAL_LIMIT = 1
MAX_MEMORY_RETRIEVAL_LIMIT = 50
MIN_MEMORY_INJECT_MAX_CHARS = 1
MAX_MEMORY_INJECT_MAX_CHARS = 50000


@dataclass(frozen=True)
class MemoryConfig:
    """Runtime configuration for optional Fusion Memory integration."""

    workspace: str
    memory_enabled: bool = False
    memory_base_url: str = "http://127.0.0.1:8765"
    memory_auto_read: bool = True
    memory_auto_write: bool = True
    memory_allow_cross_session: bool = True
    memory_inject_max_chars: int = 12000
    memory_retrieval_limit: int = 8
    memory_timeout_seconds: float = 1.0

    @classmethod
    def from_env(cls, workspace: str) -> "MemoryConfig":
        return cls(
            workspace=workspace,
            memory_enabled=_env_bool("PSI_MEMORY_ENABLED", False),
            memory_base_url=os.getenv("PSI_MEMORY_BASE_URL", "http://127.0.0.1:8765"),
            memory_auto_read=_env_bool("PSI_MEMORY_AUTO_READ", True),
            memory_auto_write=_env_bool("PSI_MEMORY_AUTO_WRITE", True),
            memory_allow_cross_session=_env_bool("PSI_MEMORY_ALLOW_CROSS_SESSION", True),
            memory_inject_max_chars=_env_int_clamped(
                "PSI_MEMORY_INJECT_MAX_CHARS",
                12000,
                min_value=MIN_MEMORY_INJECT_MAX_CHARS,
                max_value=MAX_MEMORY_INJECT_MAX_CHARS,
            ),
            memory_retrieval_limit=_env_int_clamped(
                "PSI_MEMORY_RETRIEVAL_LIMIT",
                8,
                min_value=MIN_MEMORY_RETRIEVAL_LIMIT,
                max_value=MAX_MEMORY_RETRIEVAL_LIMIT,
            ),
            memory_timeout_seconds=_env_float_clamped(
                "PSI_MEMORY_TIMEOUT_SECONDS",
                1.0,
                min_value=MIN_MEMORY_TIMEOUT_SECONDS,
                max_value=MAX_MEMORY_TIMEOUT_SECONDS,
            ),
        )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int_clamped(name: str, default: int, *, min_value: int, max_value: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    return min(max_value, max(min_value, parsed))


def _env_float_clamped(name: str, default: float, *, min_value: float, max_value: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    if not isfinite(parsed) or parsed <= 0:
        return default
    return min(max_value, max(min_value, parsed))
