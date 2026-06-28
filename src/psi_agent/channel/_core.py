from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp
from aiohttp import ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._markers import SendMarkerScanner, encode_input
from psi_agent.channel._stream import StreamBuffer, iter_sse_events
from psi_agent.channel._types import FileChunk, InputChunk, OutputChunk, ReasoningChunk, TextChunk


def _to_chunk(kind: str | None, text: str) -> OutputChunk:
    if kind == "reasoning":
        return ReasoningChunk(text)
    return TextChunk(text)


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

    async def post(self, chunks: list[InputChunk]) -> AsyncIterator[OutputChunk]:
        logger.debug(
            f"post: {len(chunks)} chunk(s) — "
            f"FileChunks={sum(1 for c in chunks if isinstance(c, FileChunk))} "
            f"TextChunks={sum(1 for c in chunks if isinstance(c, TextChunk))}"
        )

        content = encode_input(chunks)
        body = {"messages": [{"role": "user", "content": content}], "stream": True}

        buffer = StreamBuffer(self.interval)
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

            async for delta in iter_sse_events(resp.content):
                for incoming_kind, text in (
                    ("reasoning", delta.get("reasoning") or ""),
                    ("text", delta.get("content") or ""),
                ):
                    if not text:
                        continue

                    for k, t in buffer.switch(incoming_kind):
                        yield _to_chunk(k, t)

                    if incoming_kind == "text":
                        logger.debug(f"  delta.content ({len(text)} chars): {text[:60]}")
                        for file_chunk in scanner.feed(text):
                            yield file_chunk
                    else:
                        logger.debug(f"  delta.reasoning ({len(text)} chars): {text[:60]}")

                    for k, t in buffer.append(text):
                        yield _to_chunk(k, t)

        for k, t in buffer.flush():
            yield _to_chunk(k, t)
