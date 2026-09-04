"""Chat-turn provenance via ``kind`` (OpenAI ``role`` stays wire-compatible).

Finalized protocol (2026-07-17):

- ``kind: "chat"`` — ordinary Channel / Web Console turns (display)
- ``kind: "schedule.silent"`` — schedule trigger input, or silent schedule result
  (never display; schedule *user* rows are always this)
- ``kind: "schedule.display"`` — schedule *assistant* result that should surface
- ``kind: "trigger.silent"`` / ``kind: "trigger.display"`` — event-trigger turns
  (same display rules as schedule.* )
- ``kind: "compacted"`` — compaction summary (system-side; not a chat bubble)

Legacy aliases still accepted when reading JSONL:

- ``chat_type: "common"`` → ``chat``
- ``chat_type: "schedule"`` → ``schedule.silent``
- roles ``user_schedule`` / ``assistant_schedule`` → schedule.silent

AI requests strip display-only keys and rewrite legacy roles via ``project_history_for_wire``.

``turn_context`` (2026-07-29) is the same idea applied to volatile text: stored
beside the user message it belongs to, folded into ``content`` only on the way
to the AI, never rendered as part of a chat bubble.
"""

from __future__ import annotations

import re
from typing import Any

from psi_agent._send_markers import iter_send_paths

KIND_CHAT = "chat"
KIND_SCHEDULE_SILENT = "schedule.silent"
KIND_SCHEDULE_DISPLAY = "schedule.display"
KIND_TRIGGER_SILENT = "trigger.silent"
KIND_TRIGGER_DISPLAY = "trigger.display"
KIND_COMPACTED = "compacted"

KIND_KEY = "kind"

# Legacy field from the preliminary design (session层设计.txt).
CHAT_TYPE_KEY = "chat_type"
CHAT_TYPE_COMMON = "common"
CHAT_TYPE_SCHEDULE = "schedule"

# Volatile per-turn context (wall-clock time, runtime info) carried alongside
# the user message it belongs to.  Folded into ``content`` only when the turn
# is sent to the AI, so history rows stay byte-identical once written — see
# ``project_history_for_wire``.
TURN_CONTEXT_KEY = "turn_context"

_DISPLAY_ONLY_KEYS = frozenset({KIND_KEY, CHAT_TYPE_KEY, TURN_CONTEXT_KEY})

MAX_TOOL_RESULT_CHARS = 20_000
"""Cap on a single tool result, applied both at the write site and on the wire.

One ``feishu_api`` call paginated a whole Feishu group's message history into a
single row: 2,343,193 characters, with a second call adding 725,043 — together
90.5% of that session's 3,389,280-character request, which the provider then
rejected outright (``maximum context length is 1048576 tokens ... requested
1563214``).  Compaction could not save it: the signal that triggers compaction
only arrives *after* a stream completes, and this request never got that far, so
every retry rebuilt the same oversized payload.  The session stayed wedged
across restarts because the history was already on disk.

20,000 is calibrated against what legitimate tools actually return in that same
session: the largest non-pathological results were 16,035 characters
(``search_content``) and 12,752 (``read``).  The cap therefore leaves normal use
untouched while keeping a runaway result from spending the whole budget.  A tool
that genuinely needs more should paginate — the truncation notice tells the model
so, which is why it carries the original length rather than silently cutting.
"""


_TRUNCATION_MARKER = "\n[... 工具结果截断: 原 "
"""Sentinel that makes truncation idempotent.

The cap is applied twice by design — once at the write site, once on the wire —
so a row written today is re-projected on every subsequent turn.  Without this
check the second pass would cut into the *first* pass's notice and append its
own, and the notice would decay a little further on every turn thereafter.
"""


def truncate_tool_result(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Cap a tool result, keeping the head and stating what was dropped.

    The notice is part of the returned string rather than a separate field: the
    model reads only ``content``, and a silent cut would leave it believing it
    had the whole result — it would then answer from truncated data instead of
    narrowing the query.  Head-only (rather than head+tail) because tool output
    is overwhelmingly front-loaded: JSON opens with the fields that identify the
    payload, and a page of results is ordered.

    Idempotent: a string this function already produced is returned unchanged,
    so the write-site cap and the wire cap cannot compound.
    """
    if len(text) <= limit or _TRUNCATION_MARKER in text:
        return text
    return text[:limit] + f"{_TRUNCATION_MARKER}{len(text)} 字符, 保留前 {limit}。如需完整内容请分页或缩小查询范围]"


_WIRE_ROLES = frozenset({"system", "user", "assistant", "tool"})

_LEGACY_ROLE_TO_WIRE: dict[str, str] = {
    "user_schedule": "user",
    "assistant_schedule": "assistant",
}

_KNOWN_KINDS = frozenset(
    {
        KIND_CHAT,
        KIND_SCHEDULE_SILENT,
        KIND_SCHEDULE_DISPLAY,
        KIND_TRIGGER_SILENT,
        KIND_TRIGGER_DISPLAY,
        KIND_COMPACTED,
    }
)

# Presentation-only strip of wire transfer markers (Gateway history projection).
# Tolerates the space-padded variant ``[ SEND:path ]`` emitted by some models.
_TRANSFER_MARKER_RE = re.compile(r"\[\s*(?:SEND|RECV)\s*:\s*[^\]]*?\]", re.IGNORECASE)


ELISION_HANDLE_PREFIX = "[已省略"
"""Opening literal of an elision handle.  Also the idempotence sentinel.

Lives here rather than in ``request_assembly`` (which produces the handles)
because this module both defines the format and, below, strips it back out for
display — and ``request_assembly`` already imports from here, so this direction
keeps one authority instead of two literals facing each other across a layer
boundary.  Same arrangement as ``_TRUNCATION_MARKER``.

Elision is re-applied on every turn (that is what makes it stick), so a row's
content is fed back through the code path that produced it.  Without a sentinel
check the second pass would elide the handle itself, reporting an
ever-shrinking "original length" and losing the real one.
"""

ELISION_HANDLE_TEMPLATE = ELISION_HANDLE_PREFIX + " {chars} 字符{label}, 句柄 {handle}]"
"""Placeholder left where a row's content was — see ``request_assembly``.

Rationale for the format (why it is this terse, why the row is not simply
dropped) stays with the code that applies it; this is only where the literal
is defined so both sides agree on it.
"""

# Display-only strip of elision handles (Gateway history projection).
#
# Derived from ``ELISION_HANDLE_TEMPLATE`` rather than retyped: a hand-written
# second copy is how ``[SEND:]`` ended up with two regexes that disagreed (see
# ``psi_agent/_send_markers``) — there the Channel copy used ``(.+?)`` and the
# Gateway copy ``([^\]]*?)``, and only one of them filtered empty paths.  Here a
# drifted copy would fail *open*, i.e. leak the handle to the user again, which
# is exactly the bug this strip exists to fix.
#
# The body is ``[^\]\n]*`` for the same reason ``SEND_RE`` excludes both: a
# handle never spans lines, and allowing newlines would let one unclosed
# ``[已省略`` swallow through to a ``]`` several lines down, deleting real text
# the user was supposed to read.
#
# The leading ``[ \t]*`` is what keeps spacing right without a global whitespace
# collapse: a handle removed from mid-sentence would otherwise leave two spaces
# where one belongs.  Collapsing all runs of horizontal whitespace afterwards
# would fix that spacing and silently flatten the indentation of every fenced
# code block in the message — damage to text the user *is* supposed to read, in
# the name of tidying text they are not.
_ELISION_HANDLE_RE = re.compile(
    r"[ \t]*" + re.escape(ELISION_HANDLE_PREFIX) + r"[^\]\n]*\]",
)


def normalize_kind(raw: object) -> str:
    """Return a known ``kind``; unknown / empty → ``chat``."""
    if not isinstance(raw, str):
        return KIND_CHAT
    value = raw.strip().casefold()
    if value in _KNOWN_KINDS:
        return value
    if value == CHAT_TYPE_COMMON:
        return KIND_CHAT
    if value == CHAT_TYPE_SCHEDULE:
        return KIND_SCHEDULE_SILENT
    return KIND_CHAT


def wire_role(role: object) -> str | None:
    """Map a stored role to an OpenAI wire role, or ``None`` if unusable."""
    if not isinstance(role, str):
        return None
    if role in _WIRE_ROLES:
        return role
    mapped = _LEGACY_ROLE_TO_WIRE.get(role)
    if mapped is not None:
        return mapped
    if role.startswith("user_"):
        return "user"
    if role.startswith("assistant_"):
        return "assistant"
    return None


def message_kind(msg: dict[str, Any]) -> str:
    """Resolve provenance kind for a stored message."""
    role = msg.get("role")
    if isinstance(role, str) and (role in _LEGACY_ROLE_TO_WIRE or role.endswith("_schedule")):
        return KIND_SCHEDULE_SILENT
    if KIND_KEY in msg:
        return normalize_kind(msg.get(KIND_KEY))
    if CHAT_TYPE_KEY in msg:
        return normalize_kind(msg.get(CHAT_TYPE_KEY))
    return KIND_CHAT


def is_schedule_chat(msg: dict[str, Any]) -> bool:
    kind = message_kind(msg)
    return kind in {KIND_SCHEDULE_SILENT, KIND_SCHEDULE_DISPLAY}


def with_kind(msg: dict[str, Any], kind: str) -> dict[str, Any]:
    """Shallow copy with ``kind`` set (and legacy ``chat_type`` dropped)."""
    out = {k: v for k, v in msg.items() if k != CHAT_TYPE_KEY}
    out[KIND_KEY] = normalize_kind(kind)
    return out


def with_chat_type(msg: dict[str, Any], chat_type: str) -> dict[str, Any]:
    """Backward-compatible helper: map old ``chat_type`` names onto ``kind``."""
    return with_kind(msg, chat_type)


def project_history_for_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project history for the AI backend.

    Named for what it does rather than who calls it: the old name
    (``messages_for_ai``) claimed to be a projection while also enforcing
    ``truncate_tool_result`` and discarding every row between ``system`` and the
    last ``compacted``.  Those are still here — they are wire-shape obligations,
    not cost policy — but the *budget* now lives one layer out, in
    ``session/request_assembly.py``, which is the only caller that can see the
    tool schemas as well as the messages.

    - Strips display-only keys (``kind``, ``chat_type``, ``turn_context``) and
      fixes legacy roles.
    - Skips legacy assistant rows that have neither ``content`` nor
      ``tool_calls``; they are invalid OpenAI wire messages.
    - Folds ``turn_context`` into the message's ``content`` (see
      ``_fold_turn_context``) — the volatile block is stored out-of-band so
      that it lands at the request tail without ever rewriting a stored row.
    - If a ``compacted`` message exists: deletes all messages between
      the first ``system`` (index 0) and the last ``compacted`` (exclusive),
      merges the compaction summary into the system message, and drops the
      ``compacted`` message itself.
    """
    return [projected for projected, _ in project_history_with_sources(messages)]


def project_history_with_sources(
    messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """``project_history_for_wire``, but each row paired with the row it came from.

    Exists because the budget layer has to decide *which stored row* to elide
    and have that decision stick across turns.  It cannot pair projected rows
    with stored ones by position: the projection drops rows (invalid legacy
    assistants) and deletes whole spans (everything before the last
    ``compacted``), so the two lists routinely differ in length.  Inferring the
    pairing from a parallel walk would be a guess that silently mis-attributes
    the moment any row is dropped — the kind of near-miss that shows up as
    "elision hit the wrong message" long after the fact.

    So the pairing is emitted by the function that performs the projection,
    where it is known exactly, rather than reconstructed by the caller.  The
    source is ``None`` for the merged ``system`` row, which is synthesized from
    two stored rows (the prompt and the summary) and is never elidible anyway.
    """
    if not messages:
        return []

    compacted_idx: int | None = None
    compacted_content: str = ""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, dict) and msg.get("role") == "compacted":
            compacted_idx = i
            compacted_content = msg.get("content", "")
            break

    if compacted_idx is not None:
        system_idx: int | None = None
        for i, msg in enumerate(messages):
            if isinstance(msg, dict) and msg.get("role") == "system":
                system_idx = i
                break

        if system_idx is not None and system_idx < compacted_idx:
            after = messages[compacted_idx + 1 :]
            result: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

            system_msg = messages[system_idx]
            if isinstance(system_msg, dict):
                projected = {k: v for k, v in system_msg.items() if k not in _DISPLAY_ONLY_KEYS}
                projected["role"] = "system"
                projected["content"] = projected.get("content", "") + "\n\n[Compacted History]\n" + compacted_content
                # Source is ``None``: this row is the stored prompt *plus* the
                # summary, so no single stored row owns it.
                result.append((projected, None))

            for msg in after:
                if not isinstance(msg, dict):
                    continue
                role = wire_role(msg.get("role"))
                if role is None:
                    continue
                _append_for_ai(result, msg, role)
            return result

    out: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = wire_role(msg.get("role"))
        if role is None:
            continue
        _append_for_ai(out, msg, role)
    return out


def _append_for_ai(
    out: list[tuple[dict[str, Any], dict[str, Any] | None]],
    msg: dict[str, Any],
    role: str,
) -> None:
    """Append one valid wire message, skipping unusable legacy assistant rows."""
    projected = _project_for_ai(msg, role)
    if role == "assistant" and not projected.get("content") and not projected.get("tool_calls"):
        return
    out.append((projected, msg))


def _project_for_ai(msg: dict[str, Any], role: str) -> dict[str, Any]:
    """Strip display-only keys, pin ``role``, and fold in ``turn_context``."""
    projected = {k: v for k, v in msg.items() if k not in _DISPLAY_ONLY_KEYS}
    projected["role"] = role
    if role == "assistant":
        _rename_reasoning_for_wire(projected)
    if role == "tool":
        # Second line of defence behind the write-site cap in ``agent.py``: rows
        # written before that cap existed are already on disk (one live session
        # carried 3M characters in two rows), and any future write path that
        # skips the cap still cannot put an oversized row on the wire.
        content = projected.get("content")
        if isinstance(content, str):
            projected["content"] = truncate_tool_result(content)
    turn_context = msg.get(TURN_CONTEXT_KEY)
    if isinstance(turn_context, str) and turn_context.strip():
        projected["content"] = _fold_turn_context(projected.get("content"), turn_context)
    return projected


def _rename_reasoning_for_wire(projected: dict[str, Any]) -> None:
    """Send thinking back under the key providers actually read.

    We store it as ``reasoning`` (``agent.py``), but that name is ours alone:
    every provider any-llm knows reads ``reasoning_content`` / ``thinking`` /
    ``think`` / ``chain_of_thought`` (``any_llm.constants.REASONING_FIELD_NAMES``)
    and any-llm normalizes those names on the way *down* only — request messages
    are passed through untouched, so a key it does not know reaches the provider
    verbatim.  DeepSeek's thinking mode then rejects the request outright:
    ``The `reasoning_content` in the thinking mode must be passed back to the
    API.``  Measured against the live endpoint with one real wire shape, three
    times each, the key name being the only variable: ``reasoning`` → 3/3 HTTP
    400, ``reasoning_content`` → 3/3 OK, key absent → 3/3 HTTP 400.  Sending
    ``reasoning`` is therefore equivalent to sending nothing.

    Renamed here rather than at the storage site so existing history files need
    no migration.  An explicit ``reasoning_content`` already on the row wins —
    it is the provider-shaped value.
    """
    value = projected.pop("reasoning", None)
    if "reasoning_content" in projected or value is None:
        return
    if isinstance(value, dict):
        # any-llm's own ``Reasoning`` shape, should it ever be stored.
        value = value.get("content")
        if value is None:
            return
    if isinstance(value, str) and not value.strip():
        return
    projected["reasoning_content"] = value


def _fold_turn_context(content: Any, turn_context: str) -> Any:
    """Append the volatile block after ``content``.

    Placed *after* the message body rather than before it so that the stored
    text keeps the position it had when it was written — prefixing would shift
    every byte of the turn, which is exactly what storing the block
    out-of-band is meant to avoid.  Non-string content (multimodal
    block lists) is returned untouched: there is no single place to append to,
    and dropping the block is better than corrupting the blocks.
    """
    if not isinstance(content, str):
        return content
    if not content:
        return turn_context
    return content + "\n\n" + turn_context


def strip_transfer_markers(text: str) -> str:
    """Remove internal-only markers from display text (Gateway projection).

    Two kinds, both of which are addressed to a machine and meaningless to a
    reader:

    - ``[SEND:…]`` / ``[RECV:…]`` — transport instructions for the Channel.
    - ``[已省略 N 字符, 句柄 X]`` — elision handles. The model needs these (they
      say content was dropped and name where to fetch it); a user has no such
      affordance, so to them it reads as a bug. Measured in production: four
      assistant rows in one session carried a handle into the visible
      transcript. **Display side only** — the request keeps its handles, or
      elision would become silent deletion.

    Spacing around a removed handle is handled by the pattern itself rather than
    by collapsing whitespace afterwards — see ``_ELISION_HANDLE_RE``.
    """
    cleaned = _TRANSFER_MARKER_RE.sub("", text)
    cleaned = _ELISION_HANDLE_RE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract_send_paths(text: str) -> list[str]:
    """Return ``[SEND:…]`` paths in order (stripped); empty / whitespace skipped.

    Decoding lives in ``psi_agent._send_markers`` so the Channel transport and
    this Gateway projection cannot drift apart on what counts as a path.
    """
    if not isinstance(text, str) or not text:
        return []
    return [path for path, _ in iter_send_paths(text)]


def is_displayable_chat_message(msg: dict[str, Any]) -> bool:
    """Whether Gateway ``/history`` should expose this row as a chat bubble.

    Whitelist by provenance ``kind`` (not content blacklist):

    - ``chat`` user/assistant with non-empty content → yes
    - ``schedule.display`` / ``trigger.display`` assistant with non-empty content → yes
    - ``schedule.silent`` / ``trigger.silent`` / ``compacted`` / tools / system → no
    """
    kind = message_kind(msg)
    role = wire_role(msg.get("role"))
    if role not in ("user", "assistant"):
        return False
    text = msg.get("content", "")
    if not isinstance(text, str) or not text.strip():
        return False

    if kind == KIND_CHAT:
        # Legacy untagged heartbeat assistant replies (pre-kind JSONL).
        return text.strip() != "HEARTBEAT_OK"
    if kind in {KIND_SCHEDULE_DISPLAY, KIND_TRIGGER_DISPLAY}:
        return role == "assistant"
    return False
