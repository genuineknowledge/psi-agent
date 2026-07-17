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
from psi_agent.session.protocol import AiDelta


class AiClient:
    """Protocol adapter for the AI backend — handles HTTP/SSE and yields AiDelta."""

    def __init__(self, ai_socket: str) -> None:
        self.ai_socket = ai_socket

    def _build_connector_and_endpoint(self) -> tuple[aiohttp.BaseConnector, str]:
        return resolve_connector_and_endpoint(self.ai_socket)

    async def stream(self, request_body: dict) -> AsyncGenerator[AiDelta]:
        connector, endpoint = self._build_connector_and_endpoint()
        async with (
            aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=None, connect=30.0),
            ) as session,
            session.post(endpoint, json=request_body) as resp,
        ):
            logger.info(f"AI response status: {resp.status}")
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI error from {self.ai_socket!r}: {error_text[:1000]!r}")
                yield AiDelta(finish_reason="error", content=f"[AI Error: {resp.status}]")
                return

            logger.debug("Starting to consume SSE stream")
            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
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
                        finish_reason="error", content=f"[AI Error: expected 1 choice, got {len(choices_data)}]"
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
                yield AiDelta(
                    content=delta_data.get("content"),
                    reasoning=delta_data.get("reasoning"),
                    tool_calls=delta_data.get("tool_calls"),
                    finish_reason=c.get("finish_reason"),
                )
            logger.debug("SSE stream consumed successfully")
