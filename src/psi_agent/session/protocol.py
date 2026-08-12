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

from psi_agent._router_status import RouterStatus

# Provenance for ``delta.reasoning`` / ``AgentChunk.reasoning`` (UI whitelist).
# Thinking + tool progress stay in one ``reasoning`` slot (Session↔AI shape
# isomorphism); ``kind`` discriminates render / filter without splitting the slot.
REASONING_KIND_THINKING = "thinking"
REASONING_KIND_TOOL_CALL = "tool_call"
REASONING_KIND_TOOL_RESULT = "tool_result"


@dataclass
class DeltaMessage:
    """One SSE delta fragment — OpenAI Chat Completion Chunk format.

    Channel-side only.  ``ChannelAdapter.to_chat_completion_chunk()`` maps an
    ``AgentChunk`` into a ``DeltaMessage``, then wraps it in a
    ``ChatCompletionChunk`` for SSE serialisation.

    The AI side uses ``AiDelta`` instead — ``DeltaMessage`` never appears in the
    agent loop.
    """

    content: str | None = None
    role: str | None = None
    reasoning: str | None = None
    kind: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    router_status: RouterStatus | None = None
    """Validated Router progress; serialized only in an independent delta."""

    def to_dict(self) -> dict[str, Any]:
        if self.router_status is not None and any(
            value is not None for value in (self.content, self.role, self.reasoning, self.kind, self.tool_calls)
        ):
            raise ValueError("router_status must use an independent DeltaMessage")
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
        if self.router_status is not None:
            d["router_status"] = self.router_status.to_dict()
        return d


@dataclass
class StreamChoice:
    """A single choice in a streaming Chat Completion Chunk.

    Channel-side only.  Holds one ``DeltaMessage`` and an optional
    ``finish_reason``.
    """

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
    """OpenAI-compatible streaming Chat Completion Chunk.

    Channel-side only.  ``ChannelAdapter`` constructs these from ``AgentChunk``
    and serialises them as SSE ``data:`` lines via ``to_sse()``.
    """

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

    @property
    def is_complete(self) -> bool:
        return self.status is AgentRunStatus.COMPLETED


@dataclass
class AgentChunk:
    """Semantic output of ``SessionAgent.run()`` — visible output or Router status.

    The agent loop yields these to ``ChannelAdapter``, which converts them to
    ``ChatCompletionChunk`` for SSE output.  Contains no protocol fields
    (no ``id``, ``choices``, ``finish_reason``, etc.).

    ``kind`` is provenance for ``reasoning`` only (``thinking`` / ``tool_call`` /
    ``tool_result``). Tool progress remains in the ``reasoning`` slot on purpose
    (compressed process stream for OpenAI-shaped Session↔AI reuse); UI filters
    by ``kind`` instead of splitting the wire field. ``router_status`` is an
    independent, ephemeral chunk and never shares a chunk with visible output.
    """

    content: str | None = None
    reasoning: str | None = None
    kind: str | None = None
    router_status: RouterStatus | None = None
    """Ephemeral Router progress; never accumulated into conversation history."""


@dataclass
class AiDelta:
    """Internal stream element from ``AiClient.stream()``.

    Consumed by ``SessionAgent.run()`` to drive the agent loop.  Contains
    SSE-level fields (``tool_calls`` as partial dicts, ``finish_reason``)
    that the agent loop accumulates and acts on.  ``compaction_needed``
    signals that the AI layer detected a token-threshold exceed.

    Optional ``kind`` is passed through when the upstream delta already tags
    reasoning provenance; otherwise Session defaults model ``reasoning`` to
    ``thinking``. Optional ``router_status`` is already validated by ``AiClient``
    and occupies its own upstream delta.

    Never exposed to the Channel side.
    """

    content: str | None = None
    reasoning: str | None = None
    kind: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    router_status: RouterStatus | None = None
    """Validated, independent Router progress received from the upstream SSE."""
    compaction_needed: bool = False
    prompt_tokens: int = 0
    """Upstream-reported prompt tokens carried by the compaction signal (0 = unknown)."""
    compaction_threshold: int = 0
    """The threshold the signal was raised against (0 = unknown)."""
