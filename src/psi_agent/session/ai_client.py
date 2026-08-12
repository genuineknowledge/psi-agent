"""Left-side protocol adapter.  ``AiClient.stream()`` does HTTP→SSE
parsing→``AiDelta``.  Self-contained — depends only on the socket resolver
and protocol types.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import aiohttp
from loguru import logger

from psi_agent._router_status import router_status_from_event
from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent._trace import TRACE_ID_HEADER, normalize_trace_id, resolve_trace_id
from psi_agent.protocol import FINISH_REASON_ERROR, SSE_DONE, parse_sse_data
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

    async def stream(self, request_body: dict) -> AsyncGenerator[AiDelta]:
        trace_id = resolve_trace_id(routing=request_body.get("routing"))
        connector, endpoint = self._build_connector_and_endpoint()
        async with (
            aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None)) as session,
            session.post(endpoint, json=request_body, headers={TRACE_ID_HEADER: trace_id}) as resp,
        ):
            response_trace_id = resp.headers.get(TRACE_ID_HEADER)
            if response_trace_id is not None:
                try:
                    normalized_response_trace_id = normalize_trace_id(response_trace_id)
                except ValueError:
                    logger.warning(f"AI returned invalid trace ID header for trace_id={trace_id}")
                    yield AiDelta(finish_reason="error", content="[AI Error: invalid trace ID header]")
                    return
                if normalized_response_trace_id != trace_id:
                    logger.warning(f"AI returned mismatched trace ID header for trace_id={trace_id}")
                    yield AiDelta(finish_reason="error", content="[AI Error: mismatched trace ID header]")
                    return
            logger.info(f"AI response trace_id={trace_id} status={resp.status}")
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI error from {self.ai_socket!r}: {error_text[:1000]!r}")
                yield AiDelta(finish_reason=FINISH_REASON_ERROR, content=f"[AI Error: {resp.status}]")
                return

            logger.debug(f"Starting to consume SSE stream trace_id={trace_id}")
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

                if not isinstance(data, dict):
                    logger.warning(f"Expected SSE data as object, got {type(data).__name__}")
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
                if "router_status" in delta_data:
                    try:
                        router_status = router_status_from_event(data)
                    except ValueError:
                        # The invalid payload and parser detail may carry private
                        # upstream metadata, so expose only a stable safe error.
                        logger.warning("Rejected invalid router_status event from upstream")
                        yield AiDelta(
                            finish_reason="error",
                            content="[AI Error: invalid router_status event]",
                        )
                        return
                    if router_status is None or router_status.trace_id != trace_id:
                        logger.warning(f"Rejected mismatched router_status trace_id for request {trace_id}")
                        yield AiDelta(
                            finish_reason="error",
                            content="[AI Error: mismatched router_status trace ID]",
                        )
                        return
                    yield AiDelta(router_status=router_status)
                    continue
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
                )
            logger.debug(f"SSE stream consumed successfully trace_id={trace_id}")
