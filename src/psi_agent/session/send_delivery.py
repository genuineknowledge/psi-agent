"""Fill missing ``[SEND:]`` markers after file-creating tools (刻意为之).

Prompt rules already require the model to emit ``[SEND:<abs-path>]`` in the
final reply whenever a turn wrote a user-facing file.  Models still often
``write`` then paste the body inline and say "已写好: …" with no marker —
Channel never uploads, spa-v2's treasure chest stays empty, and
``/history`` has no ``sends``.

This module is a **Session-only** safety net on the existing wire: it does
not invent a new finish_reason, REST field, or chunk type.  On
``finish_reason=stop`` the agent loop asks for a suffix of ordinary
``[SEND:]`` lines, appends them to the reply text, and yields them as a
normal ``AgentChunk(content=…)`` so Channel's existing marker scanner
fires.  History then carries the same markers Gateway already projects.

Detection is intentionally narrow (named create tools + ``[OK] … to <path>``
results).  ``edit`` / bare ``bash`` are out of scope — too many false
positives.  Paths already present in the reply (via ``extract_send_paths``)
are not duplicated.  Internal capability-package paths are skipped, matching
the prompt's "do not auto-send tools/skills/…" rule.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from psi_agent.session.history_display import extract_send_paths

# Tools whose successful result means "a user-facing file was created/overwritten
# this turn".  Keep the set small and name-exact — inventing coverage for every
# shell that touched disk is how false auto-sends start.
FILE_CREATE_TOOLS: frozenset[str] = frozenset(
    {
        "write",
        "write_excel",
        "write_word",
        "write_word_from_markdown",
    }
)

# Match desktop/haitun tool success strings, e.g.
# ``[OK] Written 12 bytes to C:\ws\a.md`` / ``[OK] Wrote 3 row(s) to reports/a.xlsx``.
# No ``\b`` after ``]`` — ``]`` and the following space are both non-word, so ``\b`` never fires.
_OK_TO_PATH = re.compile(r"^\[OK\].*\sto\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)


# Path segments that mean "agent package / private runtime", not a user deliverable.
_BLOCKED_SEGMENTS: frozenset[str] = frozenset(
    {
        "tools",
        "schedules",
        "skills",
        "systems",
        "histories",
        "channel_events",
        "triggers",
    }
)


def path_from_ok_tool_result(content: str) -> str | None:
    """Pull the path from a successful file-tool result, or ``None``."""
    text = content.strip()
    if not text.startswith("[OK]"):
        return None
    match = _OK_TO_PATH.match(text)
    if match is None:
        return None
    path = match.group(1).strip().strip("\"'")
    return path or None


def is_blocked_auto_send_path(path: str) -> bool:
    """True when *path* looks like capability-package / history internals."""
    parts = {p.casefold() for p in path.replace("\\", "/").split("/") if p and p != "."}
    return bool(parts & _BLOCKED_SEGMENTS)


def created_file_paths_from_turn(messages: Sequence[dict[str, Any]]) -> list[str]:
    """Collect successful create-tool paths from this turn's history slice.

    Order follows tool-result order; duplicates keep the first occurrence.
    """
    found: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        name = msg.get("name")
        if not isinstance(name, str) or name not in FILE_CREATE_TOOLS:
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        path = path_from_ok_tool_result(content)
        if path is None or is_blocked_auto_send_path(path):
            continue
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    return found


def missing_send_paths(messages: Sequence[dict[str, Any]], reply_content: str) -> list[str]:
    """Paths written this turn that the final reply has not marked with ``[SEND:]``."""
    already = {p.casefold() for p in extract_send_paths(reply_content)}
    return [p for p in created_file_paths_from_turn(messages) if p.casefold() not in already]


def send_marker_suffix(paths: Sequence[str]) -> str:
    """Format paths as reply-tail ``[SEND:]`` lines (leading newline when non-empty)."""
    lines = [f"[SEND:{p}]" for p in paths if p.strip()]
    if not lines:
        return ""
    return "\n" + "\n".join(lines)
