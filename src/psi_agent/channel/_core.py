from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass

import aiohttp
import anyio
from aiohttp import ClientTimeout
from loguru import logger

from psi_agent._router_status import RouterStatus
from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent._trace import TRACE_ID_HEADER, ensure_trace_id, normalize_trace_id
from psi_agent.channel._errors import ChannelError
from psi_agent.channel._markers import SendMarkerScanner, encode_input
from psi_agent.channel._stream import StreamBuffer, iter_sse_events
from psi_agent.channel._types import (
    FileChunk,
    InputChunk,
    OutputChunk,
    ReasoningChunk,
    RouterStatusChunk,
    TextChunk,
)

_CHAT_PATH = "/chat/completions"
_EVENTS_PATH = "/events"


@dataclass
class ChannelCore:
    session_socket: str
    interval: float = 1.0

    @staticmethod
    def _to_chunk(kind: str, text: str) -> OutputChunk:
        # Buffer keys: "text" | "reasoning" | "reasoning:<provenance>".
        if kind == "text" or not kind.startswith("reasoning"):
            return TextChunk(text)
        provenance = kind.split(":", 1)[1] if ":" in kind else None
        return ReasoningChunk(text=text, kind=provenance or None)

    @staticmethod
    def events_endpoint_from_chat(chat_endpoint: str) -> str:
        """Derive ``…/events`` from the chat-completions endpoint on the same socket."""
        if chat_endpoint.endswith(_CHAT_PATH):
            return chat_endpoint[: -len(_CHAT_PATH)] + _EVENTS_PATH
        return chat_endpoint.rstrip("/") + _EVENTS_PATH

    async def __aenter__(self) -> ChannelCore:
        connector, self._endpoint = resolve_connector_and_endpoint(self.session_socket)
        self._session = aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=None))
        return self

    async def __aexit__(self, *args: object) -> None:
        with anyio.CancelScope(shield=True):
            await self._session.close()

    async def post_event(self, envelope: dict[str, object]) -> dict[str, object]:
        """POST a Channel-built envelope to Session ``/events`` (unified forward).

        Returns the JSON body (``ok`` / ``matched`` / ``fired``). Raises
        ``ChannelError`` on non-2xx or invalid JSON.
        """
        url = self.events_endpoint_from_chat(self._endpoint)
        logger.debug(f"POST {url} event={envelope.get('event')!r}")
        async with self._session.post(url, json=envelope) as resp:
            text = await resp.text()
            if resp.status >= 400:
                logger.warning(f"POST /events HTTP {resp.status}: {text[:500]!r}")
                raise ChannelError(f"POST /events HTTP {resp.status}: {text[:500]}")
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError as e:
                raise ChannelError(f"POST /events invalid JSON: {e}") from e
            if not isinstance(data, dict):
                raise ChannelError("POST /events response must be a JSON object")
            logger.info(
                f"POST /events ok event={envelope.get('event')!r} "
                f"matched={data.get('matched')} fired={data.get('fired')!r}"
            )
            return data

    async def post(
        self,
        chunks: list[InputChunk],
        *,
        trace_id: str | None = None,
    ) -> AsyncGenerator[OutputChunk]:
        trace_id = ensure_trace_id(trace_id)
        logger.debug(
            f"{len(chunks)} chunk(s) — "
            f"FileChunks={sum(1 for c in chunks if isinstance(c, FileChunk))} "
            f"TextChunks={sum(1 for c in chunks if isinstance(c, TextChunk))}"
        )

        content = encode_input(chunks)
        body = {
            "messages": [{"role": "user", "content": content}],
            "stream": True,
            "routing": {"trace_id": trace_id},
        }

        buffer = StreamBuffer(self.interval)
        scanner = SendMarkerScanner()

        logger.debug(f"POST {self._endpoint} trace_id={trace_id} content_len={len(content)}")
        async with self._session.post(
            self._endpoint,
            json=body,
            headers={TRACE_ID_HEADER: trace_id},
        ) as resp:
            response_trace_id = resp.headers.get(TRACE_ID_HEADER)
            if response_trace_id is not None:
                try:
                    normalized_response_trace_id = normalize_trace_id(response_trace_id)
                except ValueError as error:
                    raise ChannelError("Session returned an invalid trace ID header") from error
                if normalized_response_trace_id != trace_id:
                    raise ChannelError("Session returned a mismatched trace ID header")
            logger.info(f"HTTP {resp.status} trace_id={trace_id}")

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
                logger.debug(f"Starting to consume SSE stream trace_id={trace_id}")
                async for delta in events:
                    router_status = delta.get("router_status")
                    if router_status is not None:
                        if not isinstance(router_status, RouterStatus):
                            raise ChannelError("Invalid router_status: expected validated RouterStatus")
                        if router_status.trace_id != trace_id:
                            raise ChannelError("Router status trace ID does not match the Channel request")
                        for k, t in buffer.flush():
                            yield self._to_chunk(k, t)
                        logger.debug(f"delta.router_status trace_id={trace_id}: {router_status.to_dict()!r}")
                        yield RouterStatusChunk(status=router_status)
                        continue

                    reasoning_text = delta.get("reasoning") or ""
                    content_text = delta.get("content") or ""
                    raw_kind = delta.get("kind")
                    reasoning_buf_kind = "reasoning"
                    if reasoning_text and isinstance(raw_kind, str) and raw_kind.strip():
                        reasoning_buf_kind = f"reasoning:{raw_kind.strip()}"

                    for incoming_kind, text in (
                        (reasoning_buf_kind, reasoning_text),
                        ("text", content_text),
                    ):
                        if not text:
                            continue

                        for k, t in buffer.switch(incoming_kind):
                            yield self._to_chunk(k, t)

                        if incoming_kind == "text":
                            logger.debug(f"delta.content trace_id={trace_id} ({len(text)} chars): {text[:1000]!r}")
                            for file_chunk in scanner.feed(text):
                                yield file_chunk
                        else:
                            logger.debug(
                                f"delta.reasoning trace_id={trace_id} kind={raw_kind!r} "
                                f"({len(text)} chars): {text[:1000]!r}"
                            )

                        for k, t in buffer.append(text):
                            yield self._to_chunk(k, t)

        logger.debug(f"SSE stream consumed successfully trace_id={trace_id}")
        for k, t in buffer.flush():
            yield self._to_chunk(k, t)
