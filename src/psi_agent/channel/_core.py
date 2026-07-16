from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass

import aiohttp
import anyio
from aiohttp import ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._errors import ChannelError
from psi_agent.channel._markers import SendMarkerScanner, encode_input
from psi_agent.channel._stream import StreamBuffer, iter_sse_events
from psi_agent.channel._types import FileChunk, InputChunk, OutputChunk, ReasoningChunk, TextChunk


@dataclass
class ChannelCore:
    session_socket: str
    interval: float = 1.0

    @staticmethod
    def _to_chunk(kind: str, text: str) -> OutputChunk:
        if kind == "reasoning":
            return ReasoningChunk(text)
        return TextChunk(text)

    async def __aenter__(self) -> ChannelCore:
        connector, self._endpoint = resolve_connector_and_endpoint(self.session_socket)
        self._session = aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=None))
        return self

    async def __aexit__(self, *args: object) -> None:
        with anyio.CancelScope(shield=True):
            await self._session.close()

    async def post(self, chunks: list[InputChunk]) -> AsyncGenerator[OutputChunk]:
        logger.debug(
            f"{len(chunks)} chunk(s) — "
            f"FileChunks={sum(1 for c in chunks if isinstance(c, FileChunk))} "
            f"TextChunks={sum(1 for c in chunks if isinstance(c, TextChunk))}"
        )

        content = encode_input(chunks)
        body = {"messages": [{"role": "user", "content": content}], "stream": True}
        # aclosing required: bare async-for does not aclose nested generators on break.
        async with aclosing(self.post_json(body)) as stream:
            async for chunk in stream:
                yield chunk

    async def post_json(self, body: dict) -> AsyncGenerator[OutputChunk]:
        """POST an already-built chat-completions body and yield output chunks.

        Used by normal ``post()`` and by Gateway feedback (``user_feedback`` role)
        which must not go through ``encode_input``.
        """
        buffer = StreamBuffer(self.interval)
        scanner = SendMarkerScanner()

        logger.debug(f"POST {self._endpoint} body_keys={sorted(body)}")
        async with self._session.post(self._endpoint, json=body) as resp:
            logger.info(f"HTTP {resp.status}")

            if resp.status != 200:
                msg = await resp.text()
                try:
                    error = json.loads(msg)
                    msg = error.get("error", {}).get("message", msg)
                except Exception:
                    pass
                logger.debug(f"non-200 error: {msg!r}")
                raise ChannelError(msg)

            async with aclosing(iter_sse_events(resp.content)) as events:
                logger.debug("Starting to consume SSE stream")
                async for delta in events:
                    for incoming_kind, text in (
                        ("reasoning", delta.get("reasoning") or ""),
                        ("text", delta.get("content") or ""),
                    ):
                        if not text:
                            continue

                        for k, t in buffer.switch(incoming_kind):
                            yield self._to_chunk(k, t)

                        if incoming_kind == "text":
                            logger.debug(f"delta.content ({len(text)} chars): {text[:1000]!r}")
                            for file_chunk in scanner.feed(text):
                                yield file_chunk
                        else:
                            logger.debug(f"delta.reasoning ({len(text)} chars): {text[:1000]!r}")

                        for k, t in buffer.append(text):
                            yield self._to_chunk(k, t)

        logger.debug("SSE stream consumed successfully")
        for k, t in buffer.flush():
            yield self._to_chunk(k, t)
