"""Wire protocol shared by every psi-agent component.

The five components (AI / Session / Channel / Router / Gateway) all speak
OpenAI Chat Completions over SSE.  This module is the single owner of that
contract: the wire-format types, every custom ``finish_reason`` value, and the
behaviour rules those values carry.  It sits beside the components rather than
inside one because it describes the agreement *between* layers.

Adding a ``finish_reason`` value, or changing how one is classified, means
editing this file and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Provenance for ``delta.reasoning`` / ``AgentChunk.reasoning`` (UI whitelist).
# Thinking + tool progress stay in one ``reasoning`` slot (Session<->AI shape
# isomorphism); ``kind`` discriminates render / filter without splitting the slot.
REASONING_KIND_THINKING = "thinking"
REASONING_KIND_TOOL_CALL = "tool_call"
REASONING_KIND_TOOL_RESULT = "tool_result"

# ``stop`` / ``tool_calls`` are OpenAI standard.  ``error``, ``usage``, and
# ``compaction_needed`` are psi-agent extensions used only between our own
# layers -- never exposed to an external caller.
FINISH_REASON_STOP = "stop"
FINISH_REASON_TOOL_CALLS = "tool_calls"
FINISH_REASON_ERROR = "error"
FINISH_REASON_USAGE = "usage"
FINISH_REASON_COMPACTION_NEEDED = "compaction_needed"

SSE_DONE = "[DONE]"

# Auxiliary frames do not end the stream: they ride along *after* the model's
# real terminal frame and must never overwrite it.  Everything else -- including
# reasons we do not know about -- terminates.
AUXILIARY_FINISH_REASONS = frozenset(
    {
        FINISH_REASON_COMPACTION_NEEDED,
        FINISH_REASON_USAGE,
    }
)


def is_auxiliary_finish(value: str | None) -> bool:
    """Whether ``value`` is an auxiliary (non-terminating) finish reason."""
    return value in AUXILIARY_FINISH_REASONS


def is_terminal_finish(value: str | None) -> bool:
    """Whether ``value`` ends the stream.

    Unknown reasons count as terminal -- a reason we cannot classify is far
    more likely to be a real ending we have not met yet than a new auxiliary
    signal.  ``None`` is not terminal: the stream simply has not reported an
    end yet.
    """
    if value is None:
        return False
    return value not in AUXILIARY_FINISH_REASONS


@dataclass
class DeltaMessage:
    """One SSE delta fragment -- OpenAI Chat Completion Chunk format."""

    content: str | None = None
    role: str | None = None
    reasoning: str | None = None
    kind: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.content is not None:
            d["content"] = self.content
        if self.role is not None:
            d["role"] = self.role
        if self.reasoning is not None:
            d["reasoning"] = self.reasoning
        if self.kind is not None:
            d["kind"] = self.kind
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class StreamChoice:
    """A single choice in a streaming Chat Completion Chunk."""

    index: int = 0
    delta: DeltaMessage = field(default_factory=DeltaMessage)
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"index": self.index, "delta": self.delta.to_dict()}
        if self.finish_reason is not None:
            d["finish_reason"] = self.finish_reason
        return d


@dataclass
class ChatCompletionChunk:
    """OpenAI-compatible streaming Chat Completion Chunk."""

    id: str = "chatcmpl-unknown"
    object: str = "chat.completion.chunk"
    created: int = 0
    choices: list[StreamChoice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "choices": [c.to_dict() for c in self.choices],
        }

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


def make_error_chunk(message: str) -> dict[str, Any]:
    """Build the streaming-error chunk every component sends after HTTP 200.

    ``message`` is used verbatim: each producer owns its own prefix
    (``[Upstream Error]: `` for AI, ``[Router Error]: `` for Router, the raw
    ``AgentError`` message for Session), so this helper never prepends one.

    Session detects ``finish_reason="error"`` and skips writing the turn to
    conversation history.
    """
    return {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": message},
                "finish_reason": FINISH_REASON_ERROR,
            }
        ],
    }


def make_compaction_signal(*, prompt_tokens: int, threshold: int) -> dict[str, Any]:
    """Build the mid-stream compaction signal.

    Emitted by Session when ``prompt_tokens`` exceeds ``threshold``.
    Router and Gateway must pass it through unchanged.  Callers must not
    treat it as terminal -- when the stream resumes it may emit the same or
    a different compaction signal, or move to a real terminal frame.  This
    design gives Router and Session room to shrink the message list without
    worrying about being cut off mid-stream, and gives client ``retry`` logic
    a clear signal to try the same operation again rather than something new.

    Does not increment choice count or timestamp.  Multiple signals in a row
    are deduped by (prompt_tokens, threshold) pair; they indicate the same
    user request and must never be sent to the same caller twice.  But the
    signal is best-effort and must not cause hangs: if Router and Session
    cycle without solving the problem for >2s, the retry caller defaults
    into fail-open (repeated back-to-back compaction).
    """
    return {
        "id": "compaction",
        "choices": [{"index": 0, "delta": {}, "finish_reason": FINISH_REASON_COMPACTION_NEEDED}],
        "psi_compaction": {"needed": True, "prompt_tokens": prompt_tokens, "threshold": threshold},
    }


def make_usage_signal(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> dict[str, Any]:
    """Build the auxiliary token-usage frame emitted after one model call.

    OpenAI-compatible providers commonly report streaming usage in a chunk
    with zero choices.  psi-agent intentionally treats zero-choice chunks as
    heartbeats at component boundaries, so the AI layer normalizes provider
    usage into this single-choice internal frame.  Routers can then preserve
    usage without weakening the one-choice invariant.
    """

    return {
        "id": "usage",
        "choices": [{"index": 0, "delta": {}, "finish_reason": FINISH_REASON_USAGE}],
        "psi_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def parse_sse_data(line: str) -> str | None:
    """Extract the payload of an SSE ``data:`` line.

    Returns ``None`` for blank or non-``data:`` lines.  The single space after
    the colon is *optional* per the SSE spec, so both ``data: X`` and ``data:X``
    yield ``X`` -- four call sites used to require the space and silently
    dropped whole frames without it.

    ``SSE_DONE`` is returned verbatim: callers differ on how to react to it
    (``return`` / ``continue`` / ``break``).
    """
    if not line.startswith("data:"):
        return None
    return line[5:].lstrip()
