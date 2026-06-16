from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from aiohttp import ClientTimeout

from psi_agent.errors import UserFacingError
from psi_agent.net import make_client_session


@dataclass(frozen=True)
class SessionStreamDelta:
    """One streamed delta from a psi-agent session response."""

    content: str = ""
    reasoning: str = ""


async def stream_session_reply(*, session_socket: str, message: str) -> AsyncIterator[SessionStreamDelta]:
    client_session, endpoint = make_client_session(session_socket, timeout=ClientTimeout(total=None))
    async with client_session as session:
        req_data = {
            "model": "psi-agent",
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        }

        async with session.post(endpoint, json=req_data) as resp:
            if resp.status != 200:
                raise UserFacingError(f"Session request failed: {_format_session_error(await resp.text())}")

            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                for choice in data.get("choices", []):
                    delta = choice.get("delta", {})
                    yield SessionStreamDelta(
                        content=str(delta.get("content") or ""),
                        reasoning=str(delta.get("reasoning_content") or ""),
                    )


async def collect_session_reply(*, session_socket: str, message: str) -> str:
    chunks: list[str] = []
    async for delta in stream_session_reply(session_socket=session_socket, message=message):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()


def _format_session_error(body: str) -> str:
    try:
        error = json.loads(body)
    except json.JSONDecodeError:
        return body

    if isinstance(error, dict):
        nested = error.get("error")
        if isinstance(nested, dict):
            message = nested.get("message")
            if message:
                return str(message)
    return body
