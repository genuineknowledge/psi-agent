"""Types shared across the session layer — data models and serialisation.

The wire-format types and every shared protocol constant now live in
``psi_agent.protocol`` (the cross-component owner) and are re-exported here so
existing ``psi_agent.session.protocol`` imports keep working.  Prefer importing
shared names from ``psi_agent.protocol`` in new code; this module's own
contribution is the Session-only types below.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from psi_agent.protocol import (
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    REASONING_KIND_THINKING,
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
    ChatCompletionChunk,
    DeltaMessage,
    StreamChoice,
    is_auxiliary_finish,
    is_terminal_finish,
)

__all__ = [
    "FINISH_REASON_COMPACTION_NEEDED",
    "FINISH_REASON_ERROR",
    "FINISH_REASON_STOP",
    "FINISH_REASON_TOOL_CALLS",
    "REASONING_KIND_THINKING",
    "REASONING_KIND_TOOL_CALL",
    "REASONING_KIND_TOOL_RESULT",
    "AgentChunk",
    "AgentError",
    "AgentRunResult",
    "AgentRunStatus",
    "AgentStopCause",
    "AgentTokenUsage",
    "AiDelta",
    "ChatCompletionChunk",
    "DeltaMessage",
    "StreamChoice",
    "is_auxiliary_finish",
    "is_terminal_finish",
]


class AgentError(Exception):
    """Unrecoverable error from the agent loop.

    Raised by ``SessionAgent.run()`` when the AI backend returns a non-200
    status or a stream with ``finish_reason="error"``.

    Caught by ``ChannelAdapter.write()``, which serialises it as a
    ``ChatCompletionChunk`` with ``finish_reason="error"`` for the channel
    client.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class AgentRunStatus(StrEnum):
    """Whether a normally-returning run produced a *complete* answer.

    Only describes normal return.  Execution failure raises ``AgentError``
    instead and yields no result at all — the two are mutually exclusive.
    """

    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class AgentStopCause(StrEnum):
    """Why the agent runtime stopped, expressed in *runtime* terms.

    Distinct from ``model_finish_reason`` (the model's raw diagnostic string):
    several finish reasons — and the absence of one — collapse into a single
    runtime cause, and ``AGENT_TURN_LIMIT`` has no model-side equivalent at all.
    """

    MODEL_COMPLETED = "model_completed"
    """Model finished on its own with ``stop``."""
    MODEL_STOPPED = "model_stopped"
    """Model stopped for its own reason other than ``stop`` (e.g. ``length``)."""
    AGENT_TURN_LIMIT = "agent_turn_limit"
    """Agent loop hit ``max_tool_rounds``.  The limit counts *rounds*, and one
    round may carry several tool calls — hence "turn limit", not "tool limit"."""
    INVALID_MODEL_STREAM = "invalid_model_stream"
    """Stream ended without ever reporting a finish reason."""


@dataclass(frozen=True, slots=True)
class AgentTokenUsage:
    """Token usage accumulated across every model call in one agent run.

    Counts remain ``None`` unless every started model call reported usage.
    This prevents a partial provider response from being presented as an exact
    total.  ``model_calls`` is still useful when token counts are unavailable.
    """

    model_calls: int
    input_tokens: int | None
    output_tokens: int | None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    @property
    def complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Immutable terminal state of one fully-consumed ``SessionAgent`` run.

    Available as ``AgentRun.result`` once the chunk stream is exhausted; stays
    ``None`` while the run is in flight, and is never set when the run raises
    ``AgentError`` (failure is signalled by the exception, not by a result).
    """

    status: AgentRunStatus
    stop_cause: AgentStopCause
    model_finish_reason: str | None
    """The model's raw ``finish_reason``, kept verbatim for logs and triage —
    including reasons this code does not know about.  ``None`` when the stream
    never reported one."""
    model_turns: int
    """How many model requests this run issued (rounds of the agent loop)."""
    token_usage: AgentTokenUsage = AgentTokenUsage(
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
    )
    """Exact aggregate usage when every model call reported it."""

    @property
    def is_complete(self) -> bool:
        return self.status is AgentRunStatus.COMPLETED


@dataclass
class AgentChunk:
    """Semantic output of ``SessionAgent.run()`` — content and/or reasoning.

    The agent loop yields these to ``ChannelAdapter``, which converts them to
    ``ChatCompletionChunk`` for SSE output.  Contains no protocol fields
    (no ``id``, ``choices``, ``finish_reason``, etc.).

    ``kind`` is provenance for ``reasoning`` only (``thinking`` / ``tool_call`` /
    ``tool_result``). Tool progress remains in the ``reasoning`` slot on purpose
    (compressed process stream for OpenAI-shaped Session↔AI reuse); UI filters
    by ``kind`` instead of splitting the wire field.
    """

    content: str | None = None
    reasoning: str | None = None
    kind: str | None = None


@dataclass
class AiDelta:
    """Internal stream element from ``AiClient.stream()``.

    Consumed by ``SessionAgent.run()`` to drive the agent loop.  Contains
    SSE-level fields (``tool_calls`` as partial dicts, ``finish_reason``)
    that the agent loop accumulates and acts on.  ``compaction_needed``
    signals that the AI layer detected a token-threshold exceed.

    Optional ``kind`` is passed through when the upstream delta already tags
    reasoning provenance; otherwise Session defaults model ``reasoning`` to
    ``thinking``.

    Never exposed to the Channel side.
    """

    content: str | None = None
    reasoning: str | None = None
    kind: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    compaction_needed: bool = False
    prompt_tokens: int = 0
    """Upstream-reported prompt tokens carried by the compaction signal (0 = unknown)."""
    compaction_threshold: int = 0
    """The threshold the signal was raised against (0 = unknown)."""
    input_tokens: int | None = None
    """Prompt/input tokens from a normalized usage signal."""
    output_tokens: int | None = None
    """Completion/output tokens from a normalized usage signal."""
