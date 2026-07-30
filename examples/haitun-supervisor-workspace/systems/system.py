"""Stable system prompt for the isolated Haitun supervisor."""

from __future__ import annotations

import inspect
from typing import Any

import anyio


async def system_prompt_builder(_user_message: dict[str, Any] | None = None) -> str:
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    return await (current_file.parent.parent / "SOUL.md").read_text(encoding="utf-8")


async def system_prompt_rebuild_checker(_user_message: dict[str, Any] | None = None) -> bool:
    return False


RECENT_TURNS_KEPT_VERBATIM = 20
SUMMARY_MAX_CHARS = 8000


def _cap_summary(text: str) -> str:
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    return text[:SUMMARY_MAX_CHARS] + f"\n[... running summary truncated at {SUMMARY_MAX_CHARS} characters]"


async def compact_history(history: list[dict[str, Any]], complete_fn) -> str:
    """Update the running summary while retaining recent supervisor turns."""
    if len(history) <= RECENT_TURNS_KEPT_VERBATIM + 2:
        return ""

    older = history[:-RECENT_TURNS_KEPT_VERBATIM]
    recent = history[-RECENT_TURNS_KEPT_VERBATIM:]

    previous_summary = ""
    for message in reversed(older):
        if message.get("role") == "compacted":
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                previous_summary = content
            break

    older_parts: list[str] = []
    for message in older:
        role = message.get("role", "")
        content = message.get("content", "")
        if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
            older_parts.append(f"[{role}]: {content}")

    recent_parts: list[str] = []
    for message in recent:
        role = message.get("role", "")
        content = message.get("content", "")
        if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
            recent_parts.append(f"[{role}]: {content}")
    recent_text = "\n[Recent turns]\n" + "\n".join(recent_parts) if recent_parts else ""

    if not older_parts:
        if previous_summary:
            return _cap_summary(previous_summary) + "\n" + recent_text
        return recent_text

    if previous_summary:
        instruction = (
            "You are maintaining a running summary of a long conversation. "
            "Update the existing summary below so it also covers the new messages. "
            "Preserve all key facts, decisions, task context, file paths, and "
            "information either party explicitly mentioned — including everything "
            "already captured in the existing summary. Do not drop earlier context, "
            "and do not omit anything that could be needed later. "
            f"Keep the result under roughly {SUMMARY_MAX_CHARS // 2} characters."
        )
        user_content = f"<existing-summary>\n{previous_summary}\n</existing-summary>\n\nNew messages:\n\n" + "\n".join(
            older_parts
        )
    else:
        instruction = (
            "Summarize the following conversation concisely. "
            "Preserve all key facts, decisions, task context, file paths, "
            "and information the user or assistant explicitly mentioned. "
            "Do not omit anything that could be needed later."
        )
        user_content = "Summarize:\n\n" + "\n".join(older_parts)

    summary_prompt = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": user_content},
    ]
    try:
        summary = await complete_fn(summary_prompt)
    except Exception:
        fallback = "\n".join(older_parts) if not previous_summary else previous_summary + "\n" + "\n".join(older_parts)
        return _cap_summary(fallback) + "\n" + recent_text
    return _cap_summary(summary) + "\n" + recent_text
