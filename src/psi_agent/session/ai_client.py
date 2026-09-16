"""Left-side protocol adapter.  ``AiClient.stream()`` does HTTP→SSE
parsing→``AiDelta``.  Self-contained — depends only on the socket resolver
and protocol types.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.protocol import (
    FINISH_REASON_ERROR,
    SSE_DONE,
    parse_sse_data,
)
from psi_agent.session.protocol import AiDelta


class AiClient:
    """Protocol adapter for the AI backend — handles HTTP/SSE and yields AiDelta."""

    def __init__(self, ai_socket: str) -> None:
        self.ai_socket = ai_socket

    def _build_connector_and_endpoint(self) -> tuple[aiohttp.BaseConnector, str]:
        return resolve_connector_and_endpoint(self.ai_socket)

    @staticmethod
    def _as_int(value: object) -> int:
        """Coerce an untrusted SSE field to int; 0 when absent or malformed.

        ``bool`` is rejected explicitly: it is a subclass of ``int``, so a JSON
        ``true`` would otherwise silently become ``1`` token.
        """
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @classmethod
    def _usage_prompt_tokens(cls, data: dict) -> int:
        """Prompt tokens from a chunk's ``usage``, or 0 when absent.

        The AI layer forces ``stream_options.include_usage`` and forwards every
        upstream chunk verbatim, so this number is already on the wire — it was
        simply never parsed.  Reading it here rather than adding a second signal
        keeps one fact on one path.
        """
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return 0
        return cls._as_int(usage.get("prompt_tokens"))

    async def stream(self, request_body: dict) -> AsyncGenerator[AiDelta]:
        connector, endpoint = self._build_connector_and_endpoint()
        # Serialize once ourselves instead of passing ``json=``: aiohttp would
        # run the same ``json.dumps`` internally, so doing it here buys the exact
        # request byte count for free.  Bytes are half the latency model —
        # ``delay ≈ bytes / bandwidth`` — and without them a slow turn cannot be
        # attributed (bigger request vs. worse bandwidth).  Note ``prompt_budget``
        # counts *characters*, which at ~3.47 bytes/char for Chinese is not a
        # usable substitute.
        payload = json.dumps(request_body).encode()
        t0 = time.monotonic()
        ttft_logged = False
        async with (
            aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None)) as session,
            session.post(endpoint, data=payload, headers={"Content-Type": "application/json"}) as resp,
        ):
            # First hop only (this process → ``psi_agent.ai.server`` over the
            # local socket): steady 50-70ms, independent of context size.  It is
            # *not* the first token — reading this line as TTFB is what produced
            # the since-retracted "upstream TTFB 0.18s" claim.
            t_headers = time.monotonic() - t0
            logger.info(f"AI response status: {resp.status} (第一跳响应头 {t_headers:.3f}s, 非首字)")
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI error from {self.ai_socket!r}: {error_text[:1000]!r}")
                yield AiDelta(finish_reason=FINISH_REASON_ERROR, content=f"[AI Error: {resp.status}]")
                return

            logger.debug("Starting to consume SSE stream")
            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                data_str = parse_sse_data(line)
                # Empty payloads are heartbeats on some OpenAI-compatible
                # servers; skip them silently rather than letting them reach
                # ``json.loads`` and log a warning per beat.
                if not data_str or data_str == SSE_DONE:
                    continue

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse SSE data: {data_str[:1000]!r}")
                    continue

                usage_prompt_tokens = self._usage_prompt_tokens(data)

                choices_data = data.get("choices", [])
                if not isinstance(choices_data, list):
                    logger.warning(f"Expected choices as list, got {type(choices_data).__name__}")
                    continue
                if not choices_data and usage_prompt_tokens:
                    # OpenAI sends the final usage chunk with an *empty* choices
                    # array, so the `continue` below would drop the one number
                    # the budget calibrates against.  Surface it as a delta that
                    # carries nothing else.
                    yield AiDelta(usage_prompt_tokens=usage_prompt_tokens)
                    continue
                if len(choices_data) > 1:
                    logger.warning(f"Expected 1 choice, got {len(choices_data)}, yielding error")
                    yield AiDelta(
                        finish_reason=FINISH_REASON_ERROR,
                        content=f"[AI Error: expected 1 choice, got {len(choices_data)}]",
                    )
                    return
                if not choices_data:
                    continue

                c = choices_data[0]
                if not isinstance(c, dict):
                    logger.warning(f"Expected choice as dict, got {type(c).__name__}")
                    continue
                delta_data = c.get("delta")
                if not isinstance(delta_data, dict):
                    delta_data = {}
                # Time to first token, measured once per turn.  ``reasoning``
                # counts as a first token because the card renders thinking live —
                # what the user sees first is usually reasoning, so keying on
                # ``content`` alone would systematically overstate the wait.
                #
                # Scope: excludes queue wait (``t0`` is already past the queue;
                # enqueue→dequeue is a separate probe, not done here).  ``req_bytes``
                # is the body sent to ``ai.server``, which then injects
                # ``stream_options.include_usage`` before forwarding — so it runs a
                # few dozen bytes under the true on-wire size and should not be
                # read as an exact line count.
                #
                # INFO, deliberately: production runs at INFO, and a DEBUG probe
                # here emits nothing at all.
                if not ttft_logged:
                    reasoning_first = delta_data.get("reasoning")
                    if reasoning_first or delta_data.get("content"):
                        ttft_logged = True
                        logger.info(
                            f"AI first token: ttft={time.monotonic() - t0:.3f}s "
                            f"req_bytes={len(payload)} "
                            f"kind={'reasoning' if reasoning_first else 'content'}"
                        )
                compaction_signal = data.get("psi_compaction", {})
                compaction_needed = isinstance(compaction_signal, dict) and compaction_signal.get("needed", False)
                yield AiDelta(
                    content=delta_data.get("content"),
                    reasoning=delta_data.get("reasoning"),
                    kind=delta_data.get("kind") if isinstance(delta_data.get("kind"), str) else None,
                    tool_calls=delta_data.get("tool_calls"),
                    finish_reason=c.get("finish_reason"),
                    compaction_needed=compaction_needed,
                    prompt_tokens=self._as_int(compaction_signal.get("prompt_tokens"))
                    if isinstance(compaction_signal, dict)
                    else 0,
                    compaction_threshold=self._as_int(compaction_signal.get("threshold"))
                    if isinstance(compaction_signal, dict)
                    else 0,
                    usage_prompt_tokens=usage_prompt_tokens,
                )
            logger.debug("SSE stream consumed successfully")
