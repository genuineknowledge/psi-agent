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
    "DEFAULT_MAX_TOOL_ROUNDS",
    "FINISH_REASON_COMPACTION_NEEDED",
    "FINISH_REASON_ERROR",
    "FINISH_REASON_STOP",
    "FINISH_REASON_TOOL_CALLS",
    "MAX_ROUNDS_NOTICE",
    "REASONING_KIND_THINKING",
    "REASONING_KIND_TOOL_CALL",
    "REASONING_KIND_TOOL_RESULT",
    "AgentChunk",
    "AgentError",
    "AgentRunResult",
    "AgentRunStatus",
    "AgentStopCause",
    "AiDelta",
    "ChatCompletionChunk",
    "DeltaMessage",
    "StreamChoice",
    "is_auxiliary_finish",
    "is_terminal_finish",
]


DEFAULT_MAX_TOOL_ROUNDS = 40
"""Default ceiling on agent-loop rounds per turn.

Raised from 20 to 40 on 2026-09-05.  20 replaced 128 after real-traffic
measurements (rounds per turn p50=3, p90=13, observed max=49): 128 sat so far
above the distribution that a runaway burned 128 model calls before stopping —
in practice there was no ceiling at all — while 20 sat above p90 and capped a
runaway at roughly a sixth of the old cost.

Both workspace tool surfaces have since grown past the traffic that number was
measured against (ToC desktop ≈90 and ToB feishu ≈190 exposed tools, with
tool-discovery chains, browser_* interaction tools and multi-step SOP skills),
and normal turns began reaching 20 in routine use.  A 1000-turn local sample
over the same span measured p50=0, p90=5, p95=10, p99=32, tail up to the old
128 cap — 1.9% of turns exceeded 20 rounds.

40 covers that measured non-runaway tail (≈3x the production p90 of 13 and
above the local p99 of 32) while remaining a real ceiling: a runaway now burns
about a third of the old 128-round cost, and the observed runaway shapes
(bash x128, a 49-round turn holding the turn lock) are still stopped well
short of where they used to land.

Hitting the limit therefore stays a visible, occasional event rather than
"never": the stop is reported explicitly to the user (``MAX_ROUNDS_NOTICE``)
instead of just to the log.  Callers that legitimately need more rounds should
pass ``max_tool_rounds`` explicitly (flows already do).

Single source of truth for all three entry points (``Session``,
``SessionAgent.__init__``, ``SessionAgent.create``) — they drifted as separate
literals before, so changing "the default" meant finding every copy.
"""

MAX_ROUNDS_NOTICE = (
    "\n\n[已达到单轮工具调用上限, 停在这里]"
    "我连续调用了 {rounds} 轮工具还没得出结论, 先停下来避免空转。"
    "可以让我接着查, 或者把问题拆小一点、说得更具体一些。"
)
"""User-facing text appended when the round limit stops a turn.

Written for the person in the chat, not for a log reader: the bare
``[Max tool rounds reached]`` this replaces was an untranslated developer token
that arrived glued to whatever interstitial narration the model had produced
("让我再查一下。[Max tool rounds reached]"), so a Feishu user saw a half-finished
reply with a bracketed English string and no way to tell a round-limit stop from
a crash.  It states what happened, why, and what to do next, and carries the
round count so the log line and the chat agree on the same number.

Leading blank line separates it from the model's own last words; the bracketed
prefix stays so operators grepping histories keep a stable marker.
"""


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
    usage_prompt_tokens: int = 0
    """Prompt tokens from the stream's own ``usage`` chunk (0 = not reported yet).

    Distinct from ``prompt_tokens`` on purpose: that one rides the compaction
    signal and therefore only appears once the threshold is already exceeded,
    which is far too late to calibrate anything.  This one arrives on every
    successful turn (the AI layer forces ``stream_options.include_usage``), and
    is what ``RequestAssembler.calibrate`` divides into the character count we
    measured for that same request.
    """
