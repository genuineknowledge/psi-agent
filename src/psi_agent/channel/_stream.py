"""SSE stream processing for ChannelCore: parsing and interval buffering.

Two transport-agnostic units, separated from ``ChannelCore`` so each can be
unit-tested without HTTP/sockets:

- ``iter_sse_events`` — turns a raw byte-line stream into validated ``delta``
  dicts (handles ``data:`` framing, ``[DONE]``, heartbeats, error chunks), and with
  ``idle_timeout`` also reports upstream silence as ``IDLE``.
- ``StreamBuffer`` — merges a ``(kind, text)`` event stream into interval-sized
  blocks (flushed on kind switch, on the next ``append`` after the interval, on an
  idle report, or at stream end).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterable
from enum import Enum
from typing import Any

import anyio
from loguru import logger

from psi_agent.channel._errors import ChannelError
from psi_agent.protocol import FINISH_REASON_ERROR, SSE_DONE, parse_sse_data


class IdleMark(Enum):
    """The single fact "the upstream has been silent for a while".

    An ``Enum`` singleton rather than ``object()``: only a literal singleton (Enum
    member, ``None``) lets a type checker narrow ``x is IDLE`` so the other branch
    is a plain ``dict``, which keeps the consumer free of casts.
    """

    IDLE = "idle"


IDLE = IdleMark.IDLE


async def iter_sse_events(
    lines: AsyncIterable[bytes], idle_timeout: float = 0.0
) -> AsyncGenerator[dict[str, Any] | IdleMark]:
    """Parse a raw SSE byte-line stream into validated per-choice ``delta`` dicts.

    Skips blank/non-``data:`` lines, empty-payload ``data:`` frames, malformed
    JSON and zero-choice heartbeats; stops at ``[DONE]``; raises on multi-choice
    chunks and ``finish_reason=error``. Non-list ``choices`` and non-dict
    ``choice`` are skipped; a missing or ``null`` ``delta`` is coerced to ``{}``
    so the caller always receives a dict.

    **Idle reporting.** With ``idle_timeout > 0`` each read is wrapped in
    ``anyio.move_on_after`` and a bare ``IDLE`` is yielded whenever the upstream
    stays silent that long, letting the caller flush a buffered tail instead of
    waiting for a delta that may not come for another minute. ``idle_timeout=0``
    (the default) keeps the plain ``async for`` — byte-for-byte the old path.

    **``idle_timeout > 0`` requires ``lines`` to be a resumable reader.** The timeout
    cancels the pending read, so the source must survive that and continue on the
    next call. aiohttp's ``StreamReader`` does: it is a *class-based* async iterator,
    and a cancelled ``__anext__`` leaves it intact (verified against a stalling
    server). An async **generator** does **not** — cancelling its ``__anext__``
    finalizes it and silently truncates the stream, which is the same hazard as the
    trap in ``gateway.server._write_chat_sse_with_keepalive``. Production always
    passes ``resp.content`` (a ``StreamReader``); a generator source must keep the
    default ``idle_timeout=0``.

    Two structural constraints, both load-bearing: the timeout sits on the **raw
    byte read** and never wraps this generator's own ``__anext__``, and no ``yield``
    happens inside the cancel scope — a scope entered around a ``yield`` can be
    exited by a different task during ``aclose()``, which anyio rejects outright
    (``RuntimeError: Attempted to exit cancel scope in a different task``).
    """
    it = lines.__aiter__()
    while True:
        if idle_timeout > 0:
            with anyio.move_on_after(idle_timeout) as scope:
                try:
                    raw_line = await it.__anext__()
                except StopAsyncIteration:
                    break
            if scope.cancelled_caught:
                # Deliberately outside the scope above: yielding inside a cancel
                # scope would let a different task exit it during aclose().
                yield IDLE
                continue
        else:
            try:
                raw_line = await it.__anext__()
            except StopAsyncIteration:
                break

        line = raw_line.decode().strip()
        data_str = parse_sse_data(line)
        # Empty payload is a heartbeat frame some OpenAI-compatible services
        # send; skip it silently rather than let it hit json.loads and log a
        # warning on every beat (the old startswith("data: ") guard also
        # dropped these silently, so this preserves that logging behaviour).
        if not data_str:
            continue
        if data_str == SSE_DONE:
            logger.debug("SSE stream ended [DONE]")
            return

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            logger.warning(f"skip malformed SSE: {line[:1000]!r}")
            continue

        choices = data.get("choices", [])
        if not isinstance(choices, list):
            logger.warning(f"skip chunk with non-list choices: {type(choices).__name__}")
            continue
        if not choices:
            logger.debug("skip chunk with 0 choices (heartbeat)")
            continue
        if len(choices) != 1:
            logger.warning(f"Expected 1 choice, got {len(choices)}, raising error")
            raise ChannelError(f"Expected exactly 1 choice, got {len(choices)}")

        choice = choices[0]
        if not isinstance(choice, dict):
            logger.warning(f"skip non-dict choice: {type(choice).__name__}")
            continue

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}

        if choice.get("finish_reason") == FINISH_REASON_ERROR:
            msg = delta.get("content", "Session error")
            logger.warning(f"finish_reason=error: {msg!r}")
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

    **Idle drain.** Because the window is lazy, a buffered tail waits for the *next*
    delta to be emitted — so when the upstream model goes quiet mid-reply (observed:
    deepseek pausing 50-70s before ``[DONE]``), the last ~100 chars sit invisible in
    the buffer until the stream ends, and the user sees a reply cut off mid-sentence.
    ``drain_if_idle`` flushes that tail when ``iter_sse_events`` reports ``IDLE``,
    without waiting for a delta that may not come for another minute. Detecting the
    silence belongs to the reader, not here: this class still does no I/O and owns no
    timer, so it stays synchronous and unit-testable without an event loop.
    """

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._buf = ""
        self._kind: str | None = None
        self._timer_target: float | None = None

    def _label(self) -> str:
        """Human-readable chunk type for log messages."""
        if self._kind is None:
            return "TextChunk"
        if self._kind == "text":
            return "TextChunk"
        if self._kind.startswith("reasoning"):
            return "ReasoningChunk"
        return "TextChunk"

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

    def drain_if_idle(self) -> list[tuple[str, str]]:
        """Emit the buffered tail when the upstream has gone quiet mid-stream.

        Same output shape as ``append``, but triggered by the *absence* of a delta
        instead of the arrival of one, so a tail buffered right before a long
        upstream pause reaches the user then rather than at ``[DONE]``. Resets the
        window so the next ``append`` starts a fresh one; a no-op when nothing is
        buffered, which keeps repeated idle ticks from emitting empty blocks.
        """
        if self._buf and self._kind is not None:
            logger.debug(f"idle drain → {self._label()} ({len(self._buf)} chars)")
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
