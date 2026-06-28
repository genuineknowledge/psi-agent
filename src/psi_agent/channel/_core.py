from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp
from aiohttp import ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._markers import SendMarkerScanner, encode_input
from psi_agent.channel._types import Chunk, FileChunk, ReasoningChunk, TextChunk


@dataclass
class ChannelCore:
    session_socket: str
    interval: float = 1.0

    async def __aenter__(self) -> ChannelCore:
        connector, self._endpoint = resolve_connector_and_endpoint(self.session_socket)
        self._session = aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=None))
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._session.close()

    async def post(self, chunks: list[Chunk]) -> AsyncIterator[Chunk]:
        logger.debug(
            f"post: {len(chunks)} chunk(s) — "
            f"FileChunks={sum(1 for c in chunks if isinstance(c, FileChunk))} "
            f"TextChunks={sum(1 for c in chunks if isinstance(c, TextChunk))}"
        )

        content = encode_input(chunks)
        body = {"messages": [{"role": "user", "content": content}], "stream": True}

        buf = ""
        kind: str | None = None
        timer_target: float | None = None
        scanner = SendMarkerScanner()

        logger.debug(f"  POST {self._endpoint} content_len={len(content)}")
        async with self._session.post(self._endpoint, json=body) as resp:
            logger.debug(f"  HTTP {resp.status}")

            if resp.status != 200:
                msg = await resp.text()
                try:
                    error = json.loads(msg)
                    msg = error.get("error", {}).get("message", msg)
                except Exception:
                    pass
                logger.debug(f"  non-200 error: {msg}")
                raise Exception(msg)

            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    logger.debug("  SSE stream ended [DONE]")
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.debug(f"  skip malformed SSE: {line[:80]}")
                    continue

                choices = data.get("choices", [])
                if not choices:
                    logger.debug("  skip chunk with 0 choices (heartbeat)")
                    continue
                if len(choices) != 1:
                    raise Exception(f"Expected exactly 1 choice, got {len(choices)}")
                choice = choices[0]

                if choice.get("finish_reason") == "error":
                    delta = choice.get("delta", {})
                    msg = delta.get("content", "Session error")
                    logger.debug(f"  finish_reason=error: {msg}")
                    raise Exception(msg)

                delta = choice.get("delta", {})

                for incoming_kind, text in (
                    ("reasoning", delta.get("reasoning") or ""),
                    ("text", delta.get("content") or ""),
                ):
                    if not text:
                        continue

                    if kind is not None and incoming_kind != kind and buf:
                        if kind == "reasoning":
                            logger.debug(f"  type switch → flush ReasoningChunk ({len(buf)} chars)")
                            yield ReasoningChunk(buf)
                        else:
                            logger.debug(f"  type switch → flush TextChunk ({len(buf)} chars)")
                            yield TextChunk(buf)
                        buf = ""
                        timer_target = None

                    kind = incoming_kind
                    buf += text

                    if incoming_kind == "text":
                        logger.debug(f"  delta.content ({len(text)} chars): {text[:60]}")
                        for file_chunk in scanner.feed(text):
                            yield file_chunk
                    else:
                        logger.debug(f"  delta.reasoning ({len(text)} chars): {text[:60]}")

                    if timer_target is None:
                        timer_target = time.monotonic() + self.interval

                    if time.monotonic() >= timer_target:
                        if kind == "reasoning":
                            logger.debug(f"  timer expired → yield ReasoningChunk ({len(buf)} chars)")
                            yield ReasoningChunk(buf)
                        else:
                            logger.debug(f"  timer expired → yield TextChunk ({len(buf)} chars)")
                            yield TextChunk(buf)
                        buf = ""
                        timer_target = None

        if buf:
            if kind == "reasoning":
                logger.debug(f"  stream end flush → ReasoningChunk ({len(buf)} chars)")
                yield ReasoningChunk(buf)
            else:
                logger.debug(f"  stream end flush → TextChunk ({len(buf)} chars)")
                yield TextChunk(buf)
