"""SSE stream processing for ChannelCore: parsing and interval buffering.

Two transport-agnostic units, separated from ``ChannelCore`` so each can be
unit-tested without HTTP/sockets:

- ``iter_sse_events`` — turns a raw byte-line stream into validated ``delta``
  dicts (handles ``data:`` framing, ``[DONE]``, heartbeats, error chunks).
- ``StreamBuffer`` — merges a ``(kind, text)`` event stream into interval-sized
  blocks (flushed on kind switch, on the next ``append`` after the interval, or at
  stream end).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from loguru import logger

from psi_agent.channel._errors import ChannelError


async def iter_sse_events(lines: AsyncIterable[bytes]) -> AsyncGenerator[dict[str, Any]]:
    """Parse a raw SSE byte-line stream into validated per-choice ``delta`` dicts.

    Skips blank/non-``data:`` lines, malformed JSON and zero-choice heartbeats;
    stops at ``[DONE]``; raises on multi-choice chunks and ``finish_reason=error``.
    Non-list ``choices`` and non-dict ``choice`` are skipped; a missing or ``null``
    ``delta`` is coerced to ``{}`` so the caller always receives a dict.
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
            logger.warning(f"skip malformed SSE: {line[:1000]}")
            continue

        choices = data.get("choices", [])
        if not isinstance(choices, list):
            logger.warning(f"skip chunk with non-list choices: {type(choices).__name__}")
            continue
        if not choices:
            logger.debug("skip chunk with 0 choices (heartbeat)")
            continue
        if len(choices) != 1:
            raise ChannelError(f"Expected exactly 1 choice, got {len(choices)}")

        choice = choices[0]
        if not isinstance(choice, dict):
            logger.warning(f"skip non-dict choice: {type(choice).__name__}")
            continue

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        if choice.get("finish_reason") == "error":
            msg = delta.get("content", "Session error")
            logger.debug(f"finish_reason=error: {msg}")
            raise ChannelError(msg)

        yield delta
    logger.debug("SSE stream ended (no [DONE] marker)")


class StreamBuffer:
    """Throttle a streamed ``(kind, text)`` sequence into interval-sized blocks.

    **Why it exists.** ``ChannelCore.post`` receives the AI reply as many tiny SSE
    deltas. Pushing every token straight to a chat UI would hit rate limits and
    flicker (Telegram ``edit_text``, Feishu card refresh). ``StreamBuffer``
    coalesces consecutive tokens of the *same kind* arriving within ``interval``
    seconds into one block, so the UI updates at most ~once per ``interval``.
    Terminal channels (CLI/REPL) pass ``interval=0`` to disable batching and emit
    every token immediately.

    **Input.** The caller drives the buffer per SSE delta: ``switch(kind)``
    declares the kind of the text about to arrive (``"reasoning"`` vs anything
    else, treated as content), then ``append(text)`` adds that text. ``flush()``
    is called once when the stream ends.

    **Output.** Every method returns ``list[tuple[str, str]]`` — the ``(kind,
    merged_text)`` blocks to emit *right now* (in practice 0 or 1). The kind is
    always a real ``str``: a block is only emitted after a ``switch`` has set it,
    so the public output never carries the ``None`` that the internal ``_kind``
    holds before the first ``switch``. The caller maps each block to a
    ``TextChunk`` / ``ReasoningChunk``. Returning "what to emit now" instead of
    being an async generator lets ``post`` interleave these blocks with the
    ``FileChunk``s from ``SendMarkerScanner`` in arrival order from a single loop;
    doing no I/O keeps it synchronous and unit-testable without an event loop.

    **Kind switching.** Changing kind flushes the previous kind's buffer first:
    ``reasoning`` and content are distinct output types that must never be merged,
    and flushing on the boundary also preserves arrival order.

    **Timing (deliberately simple).** The interval is a *lazy* window checked only
    inside ``append`` — there is no background timer. A block is emitted on the
    first ``append`` after the window elapses (or at the next ``switch`` / at
    ``flush``), not exactly at the window edge. This avoids an extra anyio task and
    its cancellation surface; ``flush()`` always drains the tail at stream end. One
    ``StreamBuffer`` is created per ``post()`` call, so state never crosses requests.
    """

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._buf = ""
        self._kind: str | None = None
        self._timer_target: float | None = None

    def _label(self) -> str:
        """Human-readable chunk type for log messages."""
        return "ReasoningChunk" if self._kind == "reasoning" else "TextChunk"

    def switch(self, incoming_kind: str) -> list[tuple[str, str]]:
        """Declare the kind of the next text, flushing the buffer if it changed.

        Returns the previous kind's ``(kind, text)`` block when switching
        reasoning↔content (so the two stay separate and ordered), else an empty list.
        """
        out: list[tuple[str, str]] = []
        if self._kind is not None and incoming_kind != self._kind and self._buf:
            logger.debug(f"type switch → flush {self._label()} ({len(self._buf)} chars)")
            out.append((self._kind, self._buf))
            self._buf = ""
            self._timer_target = None
        self._kind = incoming_kind
        return out

    def append(self, text: str) -> list[tuple[str, str]]:
        """Accumulate ``text`` for the current kind, emitting once the window passed.

        Returns the merged ``(kind, text)`` block when the ``interval`` window has
        elapsed (immediately when ``interval == 0``), else an empty list while it
        keeps buffering.
        """
        self._buf += text
        if self._timer_target is None:
            self._timer_target = time.monotonic() + self._interval
        if self._kind is not None and time.monotonic() >= self._timer_target:
            logger.debug(f"timer expired → yield {self._label()} ({len(self._buf)} chars)")
            out: list[tuple[str, str]] = [(self._kind, self._buf)]
            self._buf = ""
            self._timer_target = None
            return out
        return []

    def flush(self) -> list[tuple[str, str]]:
        """Emit any text still buffered — called once when the stream ends."""
        if self._buf and self._kind is not None:
            logger.debug(f"stream end flush → {self._label()} ({len(self._buf)} chars)")
            out: list[tuple[str, str]] = [(self._kind, self._buf)]
            self._buf = ""
            return out
        return []
