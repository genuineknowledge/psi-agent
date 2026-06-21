"""Workspace tool helpers backed by Fusion Memory HTTP."""

from __future__ import annotations

import os

from psi_agent.memory.client import FusionMemoryClient, friendly_memory_error
from psi_agent.memory.config import (
    MAX_MEMORY_INJECT_MAX_CHARS,
    MAX_MEMORY_TIMEOUT_SECONDS,
    MIN_MEMORY_INJECT_MAX_CHARS,
    MIN_MEMORY_TIMEOUT_SECONDS,
    _env_float_clamped,
    _env_int_clamped,
)
from psi_agent.memory.formatting import format_memory_context
from psi_agent.memory.scope import build_memory_scope


async def memory_action(
    *,
    action: str = "read",
    content: str = "",
    query: str = "",
    section: str = "",
    limit: int = 8,
) -> str:
    """Handle the Hermes-style memory(action=...) tool."""

    action = action.strip().lower()
    if action in {"read", "search"}:
        effective_query = query or content or section or "durable user preferences and stable project facts"
        return await memory_read(query=effective_query, limit=limit)
    if action in {"write", "append"}:
        text = _sectioned_content(content, section)
        return await memory_write(content=text, mode=action)
    if action == "clear":
        return await memory_clear()
    return f"Unknown action: {action!r}. Use read, search, write, append, or clear."


async def memory_read(query: str = "", limit: int = 8) -> str:
    """Read relevant persistent memory using a query."""

    effective_query = query.strip() or "durable user preferences and stable project facts"
    try:
        async with _client() as client:
            pack = await client.answer_context(
                effective_query,
                build_memory_scope(_workspace_dir()),
                budget={"limit": limit, "allow_cross_session": True},
            )
    except Exception as exc:
        return friendly_memory_error(exc)
    rendered = format_memory_context(pack, max_chars=_inject_max_chars())
    return rendered or "No relevant Fusion Memory entries found."


async def memory_write(content: str, mode: str = "write") -> str:
    """Write durable memory content."""

    if not content.strip():
        return "[Error] content must not be empty."
    try:
        async with _client() as client:
            result = await client.add(
                {"role": "user", "content": content},
                build_memory_scope(_workspace_dir()),
                metadata={"source": "psi-agent-tool", "mode": f"manual-{mode}"},
            )
    except Exception as exc:
        return friendly_memory_error(exc)
    accepted = result.get("accepted_fact_ids") or []
    spans = result.get("span_ids") or []
    return f"Fusion Memory saved. accepted_facts={len(accepted)}, spans={len(spans)}"


async def memory_clear(allow_cross_session: bool = True) -> str:
    """Clear persistent memory for the configured scope."""

    try:
        async with _client() as client:
            result = await client.clear(
                build_memory_scope(_workspace_dir()),
                allow_cross_session=allow_cross_session,
            )
    except Exception as exc:
        return friendly_memory_error(exc)
    deleted = result.get("deleted") or {}
    total = sum(value for value in deleted.values() if isinstance(value, int))
    return "Fusion Memory cleared for current scope. deleted_rows={}, audit_id={}".format(
        total,
        result.get("audit_id", ""),
    )


def _client() -> FusionMemoryClient:
    return FusionMemoryClient(_base_url(), timeout_seconds=_timeout_seconds())


def _base_url() -> str:
    return os.getenv("PSI_MEMORY_BASE_URL", "http://127.0.0.1:8765")


def _timeout_seconds() -> float:
    return _env_float_clamped(
        "PSI_MEMORY_TIMEOUT_SECONDS",
        1.0,
        min_value=MIN_MEMORY_TIMEOUT_SECONDS,
        max_value=MAX_MEMORY_TIMEOUT_SECONDS,
    )


def _inject_max_chars() -> int:
    return _env_int_clamped(
        "PSI_MEMORY_INJECT_MAX_CHARS",
        12000,
        min_value=MIN_MEMORY_INJECT_MAX_CHARS,
        max_value=MAX_MEMORY_INJECT_MAX_CHARS,
    )


def _workspace_dir() -> str:
    return os.getenv("PSI_WORKSPACE_DIR") or os.getenv("WORKSPACE_DIR") or os.getcwd()


def _sectioned_content(content: str, section: str) -> str:
    if not section.strip():
        return content
    return f"## {section.strip()}\n\n{content}"
