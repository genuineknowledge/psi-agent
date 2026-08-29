"""Left-side protocol adapter.  ``AiClient.stream()`` does HTTP→SSE
parsing→``AiDelta``.  Self-contained — depends only on the socket resolver
and protocol types.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.protocol import (
    FINISH_REASON_ERROR,
    FINISH_REASON_TOOL_CALLS,
    FINISH_REASON_USAGE,
    SSE_DONE,
    is_auxiliary_finish,
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
            return value if value >= 0 else 0
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _as_token_count(value: object) -> int | None:
        """Validate one usage count without turning unknown values into zero."""

        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    async def stream(self, request_body: dict) -> AsyncGenerator[AiDelta]:
        connector, endpoint = self._build_connector_and_endpoint()
        pending_tool_terminal: AiDelta | None = None
        async with (
            aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None)) as session,
            session.post(endpoint, json=request_body) as resp,
        ):
            logger.info(f"AI response status: {resp.status}")
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

                choices_data = data.get("choices", [])
                if not isinstance(choices_data, list):
                    logger.warning(f"Expected choices as list, got {type(choices_data).__name__}")
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
                compaction_signal = data.get("psi_compaction", {})
                compaction_needed = isinstance(compaction_signal, dict) and compaction_signal.get("needed", False)
                usage_signal = data.get("psi_usage", {})
                has_usage = isinstance(usage_signal, dict) and c.get("finish_reason") == FINISH_REASON_USAGE
                current_finish = c.get("finish_reason")
                parsed_delta = AiDelta(
                    content=delta_data.get("content"),
                    reasoning=delta_data.get("reasoning"),
                    kind=delta_data.get("kind") if isinstance(delta_data.get("kind"), str) else None,
                    tool_calls=delta_data.get("tool_calls"),
                    finish_reason=current_finish,
                    compaction_needed=compaction_needed,
                    prompt_tokens=self._as_int(compaction_signal.get("prompt_tokens"))
                    if isinstance(compaction_signal, dict)
                    else 0,
                    compaction_threshold=self._as_int(compaction_signal.get("threshold"))
                    if isinstance(compaction_signal, dict)
                    else 0,
                    input_tokens=self._as_token_count(usage_signal.get("prompt_tokens")) if has_usage else None,
                    output_tokens=self._as_token_count(usage_signal.get("completion_tokens")) if has_usage else None,
                )
                if pending_tool_terminal is not None and not is_auxiliary_finish(current_finish):
                    # Trailing auxiliary signals belong to the completed model
                    # call. Preserve the historical terminal boundary if a
                    # normal business frame follows instead.
                    yield pending_tool_terminal
                    return
                if current_finish == FINISH_REASON_TOOL_CALLS:
                    if pending_tool_terminal is None:
                        pending_tool_terminal = parsed_delta
                    else:
                        logger.warning("Ignoring duplicate tool_calls terminal finish in SSE stream")
                    continue
                yield parsed_delta
            if pending_tool_terminal is not None:
                yield pending_tool_terminal
            logger.debug("SSE stream consumed successfully")
