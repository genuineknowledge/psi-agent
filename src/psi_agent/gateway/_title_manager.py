from __future__ import annotations

import json

from aiohttp import ClientSession, ClientTimeout
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint


class TitleManager:
    def __init__(self) -> None:
        self._titles: dict[str, str] = {}

    def get_all(self) -> dict[str, str]:
        return dict(self._titles)

    def set(self, session_id: str, title: str) -> None:
        self._titles[session_id] = title

    async def generate(self, session_id: str, ai_socket: str, user_text: str, assistant_text: str) -> str | None:
        prompt = (
            f"Generate a short title (3-5 words, in the same language as the user) for this conversation:\n\n"
            f"User: {user_text}\n\n"
            f"Assistant: {assistant_text[:1000]}\n\n"
            f"Reply with ONLY the title, no quotes or extra text."
        )
        body = {
            "model": "ignore",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        try:
            connector, endpoint = resolve_connector_and_endpoint(ai_socket)
            timeout = ClientTimeout(total=30)
            async with (
                ClientSession(connector=connector, timeout=timeout) as session,
                session.post(endpoint, json=body) as resp,
            ):
                if resp.status != 200:
                    logger.debug(f"Title AI returned {resp.status}")
                    return None
                title = ""
                async for raw in resp.content:
                    line = raw.decode().strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content") or ""
                        if content:
                            title += content
                    except json.JSONDecodeError:
                        continue
                title = title.strip().strip("'\"")
                logger.info(f"Title generation result: {title!r}")
                if title:
                    self._titles[session_id] = title
                    return title
                logger.warning(f"Title generation empty for session {session_id}")
                return None
        except Exception as e:
            logger.warning(f"Title generation failed for session {session_id}: {e}")
        return None
