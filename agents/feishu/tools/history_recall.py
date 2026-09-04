"""Recall the original text behind an elision handle.

When a request outgrows its budget, ``request_assembly`` replaces a row's
content with ``[已省略 N 字符 (tool), 句柄 X]`` — deliberately non-destructive:
"the handle names where the original still lives, so elision is recoverable
rather than destructive". This tool is the retrieval half of that promise; it
reads the session's own history JSONL and hands the row back.

**Known limitation, by construction.** ``_handle_for`` mints two handle shapes.
A ``tool_call_id`` is durable — it was written into the history file. The
fallback shape ``role#ordinal`` (e.g. ``assistant#466000``) is
``id(source) % 1_000_000``, a *live memory address*: it is never persisted, and
after a restart it does not even name the same row. Those handles are refused
with ``handle_not_on_disk`` rather than resolved by guessing a row of that role
— a wrong row returned confidently is worse than no row.

Read-only throughout: nothing here opens a history file for writing.
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import os
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h
import anyio

from psi_agent._appdata import (
    appdata_history_path,
    legacy_history_path,
    resolve_appdata_root,
    resolve_history_read_path,
)
from psi_agent.session.history_display import (
    ELISION_HANDLE_PREFIX,
    ELISION_HANDLE_TEMPLATE,
    MAX_TOOL_RESULT_CHARS,
)

# Both derived from the template rather than retyped. ``history_display`` owns
# the format and already learned this lesson twice (``[SEND:]`` drifted into two
# disagreeing regexes; the display strip is likewise derived). A hand-copied
# ", 句柄 " here would fail *closed* — every pasted handle would stop parsing —
# but it would fail silently at the exact moment the format changed.
_HANDLE_SEPARATOR = ELISION_HANDLE_TEMPLATE.split("{handle}")[0].rsplit("}", 1)[-1]
_HANDLE_SUFFIX = ELISION_HANDLE_TEMPLATE.split("{handle}")[1]

# ``role#ordinal`` as ``_handle_for`` mints it: a role word, ``#``, and the
# zero-padded remainder of an ``id()``. Matched only so it can be *rejected*
# with an accurate reason.
_ROLE_ORDINAL_RE = re.compile(r"^[A-Za-z_]+#\d+$")

# A durable handle is a provider tool-call id. Everything a path needs — ``.``,
# ``/``, ``\``, ``:`` — is outside this set, which is what makes the charset the
# traversal guard rather than a cosmetic validation. Same alphabet
# ``Conversation.from_workspace`` allows for a session id.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class RecallRefusedError(Exception):
    """Refusal carrying the machine-readable ``code`` the caller reports.

    The code matters as much as the message: "you may not ask for this path"
    and "that handle is not in this history" must stay distinguishable, or
    deleting the traversal guard downgrades into an ordinary miss and looks
    identical from outside.
    """

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


def parse_handle(raw: str) -> str:
    """Extract the handle token from either a bare id or a whole rendered handle.

    The model sees ``[已省略 1234 字符 (read), 句柄 call_abc]`` in its transcript,
    so pasting that verbatim is the likely call — accepting only the bare token
    would make the tool fail for the most natural input.
    """
    text = raw.strip()
    if ELISION_HANDLE_PREFIX in text and _HANDLE_SEPARATOR in text:
        text = text.split(_HANDLE_SEPARATOR)[-1]
        if _HANDLE_SUFFIX and text.endswith(_HANDLE_SUFFIX):
            text = text[: -len(_HANDLE_SUFFIX)]
    return text.strip()


def _norm(path: str) -> str:
    """Case + separator normalisation for containment checks (see ``file_serving``)."""
    return os.path.normcase(os.path.normpath(path))


async def _ensure_within_histories(path: Path, roots: list[Path]) -> Path:
    """Resolve *path* and require it to sit inside one of the *roots*.

    ``resolve()`` before the containment test, not after: it is what expands
    ``..`` and symlinks, and skipping it is how ``histories/../decoy.jsonl``
    passes a string-prefix check — the reasoning ``session/file_serving.py``
    spells out for the outbound side.

    Underscore-prefixed deliberately: ``ToolRegistry`` registers *every* public
    async function in a tool file, and this one takes ``Path`` — an unsupported
    parameter type — so as a public name it logged a ``Skipping tool`` ERROR on
    every single registry load.
    """
    resolved = await anyio.Path(path).resolve()
    target = _norm(str(resolved))
    for root in roots:
        root_s = _norm(str(await anyio.Path(root).resolve()))
        if target == root_s or target.startswith(root_s + os.sep):
            return Path(resolved)
    raise RecallRefusedError("unsafe_history_path", f"history path outside this session's history roots: {str(path)!r}")


async def _locate_history(session_id: str, workspace: str) -> tuple[Path, list[Path]]:
    """The session's history file plus the roots it is allowed to live under."""
    appdata_root = await resolve_appdata_root()
    read_path = await resolve_history_read_path(
        appdata_root=appdata_root,
        workspace=workspace,
        session_id=session_id,
    )
    roots = [
        Path(str(appdata_history_path(appdata_root, session_id).parent)),
        Path(str(legacy_history_path(workspace, session_id).parent)),
    ]
    return Path(str(read_path)), roots


async def _find_row(path: Path, handle: str) -> dict[str, object] | None:
    """First row whose ``tool_call_id`` is *handle*.

    Streams the file: sessions reach tens of megabytes, and reading one whole
    into memory to answer a budget-saving call would defeat the point.
    Unparsable lines are skipped for the same reason ``Conversation._load``
    skips them — an append interrupted by a crash leaves a torn final line.
    """
    async with await anyio.Path(path).open("r", encoding="utf-8", errors="replace") as handle_file:
        async for line in handle_file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError, TypeError:
                continue
            if isinstance(row, dict) and row.get("tool_call_id") == handle:
                return row
    return None


async def history_recall(handle: str, session_id: str = "", workspace: str = "") -> str:
    """Read back the original text of a row that was elided from your context.

    Call this when the transcript shows ``[已省略 N 字符…, 句柄 X]`` and you need
    what was actually there. Pass either the bare handle or the whole bracketed
    text. Read-only; the history file is never modified.

    Handles of the form ``role#ordinal`` (e.g. ``assistant#466000``) cannot be
    resolved: that number is a live memory address, not something stored in the
    history, so it does not survive a process restart. Those return
    ``handle_not_on_disk`` — the original text is not retrievable by handle;
    use ``sessions_history`` or ask the user instead.

    Args:
        handle: The handle from an elision placeholder, or the whole
            ``[已省略 …]`` text copied from the transcript.
        session_id: Session whose history to read. Empty = current session.
        workspace: Workspace root. Empty = current workspace.

    Returns:
        JSON. On success ``ok=true`` with ``content`` (capped at 20,000
        characters), ``chars`` (the row's true length), ``truncated``, ``role``
        and ``name``. On failure ``ok=false`` with ``error`` and ``reason``.
    """
    token = parse_handle(handle)
    sid = _h.resolve_session_id(session_id)
    ws = workspace.strip() or _h.current_workspace()

    try:
        if not token:
            raise RecallRefusedError("unsafe_handle", "handle is empty")
        if _ROLE_ORDINAL_RE.match(token):
            raise RecallRefusedError(
                "handle_not_on_disk",
                f"handle {token!r} is a role#ordinal handle, derived from an in-process memory address "
                "(id(row) % 1000000). It is never written to the history file and does not survive a "
                "restart, so the original cannot be recalled by handle. Use sessions_history to inspect "
                "the session, or ask the user.",
            )
        if not _SAFE_TOKEN_RE.match(token):
            raise RecallRefusedError(
                "unsafe_handle",
                f"handle {token!r} is not a tool-call id (letters, digits, '-', '_' only); refused without lookup",
            )
        if not sid:
            raise RecallRefusedError("unsafe_session_id", "no session id: pass session_id explicitly")
        if not _SAFE_TOKEN_RE.match(sid):
            raise RecallRefusedError("unsafe_session_id", f"session_id {sid!r} is not a plain session id; refused")
        if not ws:
            raise RecallRefusedError("no_history", "no workspace root for this session")

        path, roots = await _locate_history(sid, ws)
        path = await _ensure_within_histories(path, roots)
        if not await anyio.Path(path).is_file():
            raise RecallRefusedError("no_history", f"no history file for session {sid!r}")

        row = await _find_row(path, token)
        if row is None:
            raise RecallRefusedError(
                "handle_not_found",
                f"handle {token!r} is not in session {sid!r}'s history",
            )
    except RecallRefusedError as refused:
        return json.dumps(
            {"ok": False, "error": refused.code, "reason": refused.reason, "handle": token},
            ensure_ascii=False,
        )

    content = row.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    name = row.get("name")
    # Capped at the same constant the write site uses: recalling an unbounded row
    # would hand the whole saved budget straight back, and the row that made
    # that constant necessary was 2,343,193 characters.
    return json.dumps(
        {
            "ok": True,
            "handle": token,
            "session_id": sid,
            "role": str(row.get("role", "")),
            "name": name if isinstance(name, str) else "",
            "chars": len(text),
            "truncated": len(text) > MAX_TOOL_RESULT_CHARS,
            "content": text[:MAX_TOOL_RESULT_CHARS],
        },
        ensure_ascii=False,
    )
