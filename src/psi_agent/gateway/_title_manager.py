from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.gateway._manager import _noop


class TitleManager:
    def __init__(self, _persist: Callable[[], Awaitable[None]] | None = None) -> None:
        self._titles: dict[str, str] = {}
        self._persist = _persist or _noop

    def get_all(self) -> dict[str, str]:
        return dict(self._titles)

    async def set(self, session_id: str, title: str) -> None:
        self._titles[session_id] = title
        await self._persist()

    async def delete(self, session_id: str) -> None:
        if session_id not in self._titles:
            return
        del self._titles[session_id]
        await self._persist()
        logger.debug(f"Title deleted for session {session_id!r}")

    async def generate(self, session_id: str, ai_socket: str, user_text: str, assistant_text: str) -> str | None:
        prompt = (
            f"Generate a short title (3-5 words, in the same language as the user) for this conversation:\n\n"
            f"User: {user_text}\n\n"
            f"Assistant: {assistant_text}\n\n"
            f"Reply with ONLY the title, no quotes or extra text."
        )
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        try:
            connector, endpoint = resolve_connector_and_endpoint(ai_socket)
            timeout = ClientTimeout(total=None)
            async with (
                ClientSession(connector=connector, timeout=timeout) as session,
                session.post(endpoint, json=body) as resp,
            ):
                if resp.status != 200:
                    logger.debug(f"Title AI returned {resp.status}")
                    return None
                title = ""
                buf = b""
                async for raw in resp.content:
                    buf += raw
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        line = line_bytes.decode().strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        logger.debug(f"Title SSE chunk: {data_str[:200]}")
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
                            title += content
                title = title.strip().strip("'\"")
                logger.info(f"Title generation result: {title!r}")
                if title:
                    self._titles[session_id] = title
                    await self._persist()
                    return title
                logger.warning(f"Title generation empty for session {session_id!r}")
                return None
        except Exception as e:
            logger.warning(f"Title generation failed for session {session_id!r}: {e!r}")
        return None
