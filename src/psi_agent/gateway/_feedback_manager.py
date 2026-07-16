"""Submit Web Console like/dislike into Session history as ``user_feedback``."""

from __future__ import annotations

from contextlib import aclosing
from typing import Any

from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.session.history_display import ROLE_USER_FEEDBACK


class FeedbackManager:
    async def submit(self, channel_socket: str, kind: str) -> dict[str, Any]:
        """Record or clear feedback via the Session channel socket.

        ``kind`` is ``\"up\"``, ``\"down\"``, or ``\"\"`` (clear). Does not
        run the agent loop — Session appends/replaces a ``user_feedback``
        row and commits.
        """
        normalized = kind if kind in ("up", "down") else ""
        body = {
            "messages": [
                {
                    "role": ROLE_USER_FEEDBACK,
                    "content": "",
                    "feedback": normalized,
                }
            ],
            "stream": True,
        }
        logger.info(f"Feedback: posting kind={normalized!r} to {channel_socket!r}")
        async with (
            ChannelCore(session_socket=channel_socket, interval=0.0) as core,
            aclosing(core.post_json(body)) as stream,
        ):
            async for _chunk in stream:
                pass
        return {"ok": True, "kind": normalized}
