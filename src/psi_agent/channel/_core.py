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
from psi_agent.channel._stream import IDLE, StreamBuffer, iter_sse_events
from psi_agent.channel._types import FileChunk, InputChunk, OutputChunk, ReasoningChunk, TextChunk

_CHAT_PATH = "/chat/completions"
_EVENTS_PATH = "/events"


@dataclass
class ChannelCore:
    session_socket: str
    interval: float = 1.0
    idle_drain: float = 5.0
    """Seconds of upstream silence after which a buffered tail is emitted (0 = off).

    ``StreamBuffer``'s window is lazy — checked only when the next delta arrives — so
    a model that goes quiet near the end of a reply (observed: deepseek pausing
    50-70s before ``[DONE]``) leaves the last chars invisible until stream end and
    the reply looks cut off mid-sentence. Ignored when ``interval == 0``: those
    callers emit every token as it arrives, so no tail can accumulate.
    """

    @staticmethod
    def _buffer_key(provenance: str, tool_name: str) -> str:
        """Build the ``StreamBuffer`` bucket key for one reasoning provenance.

        Paired with :meth:`_to_chunk` — the buffer holds a single string key per
        bucket, so the tool name has to travel *inside* that key and be split
        back out on the way to a ``ReasoningChunk``. Changing one side alone
        silently drops the name or mistakes it for the provenance.

        The tool name is part of the key, not just cargo, because
        ``StreamBuffer`` merges consecutive text sharing a key: tools run
        concurrently, so two ``tool_call`` deltas arrive back to back, and a
        key of ``reasoning:tool_call`` alone would fuse them into one block
        whose single ``tool_name`` could only name one of the two.
        """
        return f"reasoning:{provenance}" + (f"\x1f{tool_name}" if tool_name else "")

    @staticmethod
    def _to_chunk(kind: str, text: str) -> OutputChunk:
        # Buffer keys: "text" | "reasoning" | "reasoning:<provenance>"
        # | "reasoning:<provenance>\x1f<tool_name>".
        if kind == "text" or not kind.startswith("reasoning"):
            return TextChunk(text)
        provenance = kind.split(":", 1)[1] if ":" in kind else None
        tool_name = None
        if provenance and "\x1f" in provenance:
            provenance, tool_name = provenance.split("\x1f", 1)
        return ReasoningChunk(text=text, kind=provenance or None, tool_name=tool_name or None)

    @property
    def _byte_source(self) -> str:
        """出向文件的字节该从哪儿取; 本地 Session 返回 ``""``。

        只有 TCP 地址才填 —— 那是「Session 在另一个容器」的形态 (见生产
        ``PSI_FEISHU_EXTERNAL_SESSIONS``)。Unix socket / 命名管道意味着同机同文件系统,
        此时路径本就可读, 填地址只会让客户端多绕一趟 HTTP 去拿它已经能直接读的字节。
        """
        if self.session_socket.startswith(("http://", "https://")):
            return self.session_socket.rstrip("/")
        return ""

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

    async def post(self, chunks: list[InputChunk]) -> AsyncGenerator[OutputChunk]:
        logger.debug(
            f"{len(chunks)} chunk(s) — "
            f"FileChunks={sum(1 for c in chunks if isinstance(c, FileChunk))} "
            f"TextChunks={sum(1 for c in chunks if isinstance(c, TextChunk))}"
        )

        content = encode_input(chunks)
        body = {"messages": [{"role": "user", "content": content}], "stream": True}

        buffer = StreamBuffer(self.interval)
        scanner = SendMarkerScanner()

        logger.debug(f"POST {self._endpoint} content_len={len(content)}")
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

            # idle_drain 只对有缓冲的通道有意义: interval=0 时每个 token 直出, 缓冲里
            # 永远没有尾巴可排, 传超时进去只会白设一层 cancel scope。
            idle_timeout = self.idle_drain if self.interval > 0 else 0.0
            async with aclosing(iter_sse_events(resp.content, idle_timeout)) as events:
                logger.debug("Starting to consume SSE stream")
                async for delta in events:
                    if delta is IDLE:
                        for k, t in buffer.drain_if_idle():
                            yield self._to_chunk(k, t)
                        continue

                    reasoning_text = delta.get("reasoning") or ""
                    content_text = delta.get("content") or ""
                    raw_kind = delta.get("kind")
                    raw_tool = delta.get("tool_name")
                    reasoning_buf_kind = "reasoning"
                    if reasoning_text and isinstance(raw_kind, str) and raw_kind.strip():
                        tool_name = raw_tool.strip() if isinstance(raw_tool, str) else ""
                        reasoning_buf_kind = self._buffer_key(raw_kind.strip(), tool_name)

                    for incoming_kind, text in (
                        (reasoning_buf_kind, reasoning_text),
                        ("text", content_text),
                    ):
                        if not text:
                            continue

                        for k, t in buffer.switch(incoming_kind):
                            yield self._to_chunk(k, t)

                        if incoming_kind == "text":
                            logger.debug(f"delta.content ({len(text)} chars): {text[:1000]!r}")
                            for file_chunk in scanner.feed(text):
                                # 跨容器时补上取字节的地址; 本地留空 → 客户端照旧直接读路径。
                                # 填在这里而不是 scanner 里: scanner 是纯解码, 不该知道传输地址。
                                file_chunk.source = self._byte_source
                                yield file_chunk
                        else:
                            logger.debug(f"delta.reasoning kind={raw_kind!r} ({len(text)} chars): {text[:1000]!r}")

                        for k, t in buffer.append(text):
                            yield self._to_chunk(k, t)

        logger.debug("SSE stream consumed successfully")
        for k, t in buffer.flush():
            yield self._to_chunk(k, t)
