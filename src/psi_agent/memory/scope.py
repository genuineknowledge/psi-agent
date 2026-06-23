"""Scope construction for Fusion Memory requests."""

from __future__ import annotations

import os
import pathlib
import uuid
from typing import Any

_PROCESS_SESSION_ID = f"psi-session-{uuid.uuid4().hex}"


def build_memory_scope(
    workspace: str | os.PathLike[str] | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build a Fusion Memory scope from environment and workspace defaults."""

    workspace_path = pathlib.Path(workspace or os.getenv("PSI_WORKSPACE_DIR") or os.getcwd())
    workspace_default = workspace_path.resolve().name or "workspace"
    user_default = os.getenv("USER") or os.getenv("USERNAME") or "user"

    scope = {
        "workspace_id": _optional_env("PSI_MEMORY_WORKSPACE_ID") or workspace_default,
        "user_id": _optional_env("PSI_MEMORY_USER_ID") or user_default,
        "agent_id": _optional_env("PSI_MEMORY_AGENT_ID") or "psi-agent",
        "run_id": _optional_env("PSI_MEMORY_RUN_ID"),
        "session_id": (
            _optional_env("PSI_MEMORY_SESSION_ID")
            or _optional_env("PSI_SESSION_ID")
            or session_id
            or _PROCESS_SESSION_ID
        ),
        "app_id": _optional_env("PSI_MEMORY_APP_ID") or "psi-agent",
    }
    return {key: value for key, value in scope.items() if value}


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None
