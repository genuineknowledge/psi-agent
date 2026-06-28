"""Types shared across the session layer — data models and serialisation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


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
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.content is not None:
            d["content"] = self.content
        if self.role is not None:
            d["role"] = self.role
        if self.reasoning is not None:
            d["reasoning"] = self.reasoning
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
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


@dataclass
class AgentChunk:
    """Semantic output of ``SessionAgent.run()`` — content and/or reasoning.

    The agent loop yields these to ``ChannelAdapter``, which converts them to
    ``ChatCompletionChunk`` for SSE output.  Contains no protocol fields
    (no ``id``, ``choices``, ``finish_reason``, etc.).
    """

    content: str | None = None
    reasoning: str | None = None


@dataclass
class AiDelta:
    """Internal stream element from ``AiClient.stream()``.

    Consumed by ``SessionAgent.run()`` to drive the agent loop.  Contains
    SSE-level fields (``tool_calls`` as partial dicts, ``finish_reason``)
    that the agent loop accumulates and acts on.

    Never exposed to the Channel side.
    """

    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
