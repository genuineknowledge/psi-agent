"""SSE stream processing for ChannelCore: parsing and interval buffering.

Two transport-agnostic units, separated from ``ChannelCore`` so each can be
unit-tested without HTTP/sockets:

- ``iter_sse_events`` — turns a raw byte-line stream into validated ``delta``
  dicts (handles ``data:`` framing, ``[DONE]``, heartbeats, error chunks).
- ``StreamBuffer`` — merges a ``(kind, text)`` event stream into interval-sized
  blocks, flushing on type switch / timer expiry / stream end.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from loguru import logger

from psi_agent.channel._errors import ChannelError


async def iter_sse_events(lines: AsyncIterable[bytes]) -> AsyncIterator[dict[str, Any]]:
    """Parse a raw SSE byte-line stream into validated per-choice ``delta`` dicts.

    Skips blank/non-``data:`` lines, malformed JSON and zero-choice heartbeats;
    stops at ``[DONE]``; raises on multi-choice chunks and ``finish_reason=error``.
    """
    async for raw_line in lines:
        line = raw_line.decode().strip()
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            logger.debug("SSE stream ended [DONE]")
            return

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            logger.debug(f"skip malformed SSE: {line[:80]}")
            continue

        choices = data.get("choices", [])
        if not choices:
            logger.debug("skip chunk with 0 choices (heartbeat)")
            continue
        if len(choices) != 1:
            raise ChannelError(f"Expected exactly 1 choice, got {len(choices)}")
        choice = choices[0]

        if choice.get("finish_reason") == "error":
            delta = choice.get("delta", {})
            msg = delta.get("content", "Session error")
            logger.debug(f"finish_reason=error: {msg}")
            raise ChannelError(msg)

        yield choice.get("delta", {})


class StreamBuffer:
    """Merges a ``(kind, text)`` stream into interval-sized blocks.

    ``switch`` flushes the buffer when the content kind changes; ``append`` adds
    text and flushes once the interval window elapses; ``flush`` drains the tail.
    Each method returns the ``(kind, text)`` blocks that should be emitted now.
    """

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._buf = ""
        self._kind: str | None = None
        self._timer_target: float | None = None

    def _label(self) -> str:
        return "ReasoningChunk" if self._kind == "reasoning" else "TextChunk"

    def switch(self, incoming_kind: str) -> list[tuple[str | None, str]]:
        out: list[tuple[str | None, str]] = []
        if self._kind is not None and incoming_kind != self._kind and self._buf:
            logger.debug(f"type switch → flush {self._label()} ({len(self._buf)} chars)")
            out.append((self._kind, self._buf))
            self._buf = ""
            self._timer_target = None
        self._kind = incoming_kind
        return out

    def append(self, text: str) -> list[tuple[str | None, str]]:
        self._buf += text
        if self._timer_target is None:
            self._timer_target = time.monotonic() + self._interval
        if time.monotonic() >= self._timer_target:
            logger.debug(f"timer expired → yield {self._label()} ({len(self._buf)} chars)")
            out: list[tuple[str | None, str]] = [(self._kind, self._buf)]
            self._buf = ""
            self._timer_target = None
            return out
        return []

    def flush(self) -> list[tuple[str | None, str]]:
        if self._buf:
            logger.debug(f"stream end flush → {self._label()} ({len(self._buf)} chars)")
            out: list[tuple[str | None, str]] = [(self._kind, self._buf)]
            self._buf = ""
            return out
        return []
