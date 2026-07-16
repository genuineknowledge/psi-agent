from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import aclosing

from aiohttp import ClientSession, ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent.channel._stream import iter_sse_events
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
                async with aclosing(iter_sse_events(resp.content)) as events:
                    async for delta in events:
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
