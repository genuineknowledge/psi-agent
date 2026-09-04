from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import anyio
from aiohttp import ClientSession, ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.protocol import SSE_DONE, parse_sse_data
from psi_agent.runtime._manager import _noop


class SummaryManager:
    """Session task summaries for spa-v2 cards / 「任务摘要」 panel.

    Mirrors TitleManager: in-memory map + AppData state persist + one-shot AI
    generation over the Session's AI socket (not the chat Session loop).
    """

    def __init__(self, _persist: Callable[[], Awaitable[None]] | None = None) -> None:
        self._summaries: dict[str, str] = {}
        self._persist = _persist or _noop
        self._lock = anyio.Lock()

    def get_all(self) -> dict[str, str]:
        return dict(self._summaries)

    async def set(self, session_id: str, summary: str) -> None:
        async with self._lock:
            self._summaries[session_id] = summary
            await self._persist()

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            if session_id not in self._summaries:
                return
            del self._summaries[session_id]
            await self._persist()
            logger.debug(f"Summary deleted for session {session_id!r}")

    async def generate(
        self,
        session_id: str,
        ai_socket: str,
        user_text: str,
        assistant_text: str,
    ) -> str | None:
        prompt = (
            "Write a short task summary (NOT a title) for this conversation turn.\n"
            "Requirements: 1-2 sentences in the same language as the user; "
            "cover the goal and current progress; do not paste long excerpts; "
            "no Markdown markers (#, *, backticks); no quotes or labels.\n"
            "Reply with ONLY the summary text.\n\n"
            f"User: {user_text}\n\n"
            f"Assistant: {assistant_text}"
        )
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        try:
            connector, endpoint = resolve_connector_and_endpoint(ai_socket)
            timeout = ClientTimeout(total=None, connect=30.0)
            async with (
                ClientSession(connector=connector, timeout=timeout) as session,
                session.post(endpoint, json=body) as resp,
            ):
                if resp.status != 200:
                    logger.debug(f"Summary AI returned {resp.status}")
                    return None
                summary = ""
                buf = b""
                async for raw in resp.content:
                    buf += raw
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        line = line_bytes.decode().strip()
                        data_str = parse_sse_data(line)
                        # 空载荷是部分 OpenAI 兼容服务的心跳帧: 静默跳过, 不要让它走到
                        # json.loads (旧的 startswith("data: ") guard 也是静默丢弃的)。
                        if not data_str:
                            continue
                        if data_str == SSE_DONE:
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        logger.debug(f"Summary SSE chunk: {data_str[:200]}")
                        choices = chunk.get("choices", [])
                        if not isinstance(choices, list) or not choices:
                            continue
                        first = choices[0]
                        if not isinstance(first, dict):
                            continue
                        delta = first.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        content = delta.get("content") or ""
                        if content:
                            summary += content
                summary = " ".join(summary.strip().strip("'\"").split())
                logger.info(f"Summary generation result: {summary!r}")
                if summary:
                    async with self._lock:
                        self._summaries[session_id] = summary
                        await self._persist()
                    return summary
                logger.warning(f"Summary generation empty for session {session_id!r}")
                return None
        except Exception as e:
            logger.warning(f"Summary generation failed for session {session_id!r}: {e!r}")
        return None
