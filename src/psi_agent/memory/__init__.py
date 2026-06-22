"""Fusion Memory integration for psi-agent."""

from __future__ import annotations

from typing import Any

from psi_agent.memory.client import FusionMemoryClient, FusionMemoryError
from psi_agent.memory.scope import build_memory_scope

__all__ = ["FusionMemoryClient", "FusionMemoryError", "SessionMemoryAdapter", "build_memory_scope"]


def __getattr__(name: str) -> Any:
    if name == "SessionMemoryAdapter":
        from psi_agent.memory.adapter import SessionMemoryAdapter

        return SessionMemoryAdapter
    raise AttributeError(name)
