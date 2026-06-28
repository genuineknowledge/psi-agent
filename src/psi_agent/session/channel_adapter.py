from __future__ import annotations

from collections.abc import AsyncIterator

from aiohttp import web
from loguru import logger

from psi_agent.session.protocol import AgentChunk, AgentError, ChatCompletionChunk, DeltaMessage, StreamChoice


class ChannelAdapter:
    """Protocol adapter for the Channel side — stateless encode/decode.

    ``parse_request``: HTTP JSON body → ``(user_message, extra_params)``.
    ``write``: consumes an ``AgentChunk`` iterator and writes SSE to the response.
    """

    class ParseError(Exception):
        """Raised by ``parse_request()`` for malformed or empty requests."""

    @staticmethod
    async def parse_request(request: web.Request) -> tuple[dict, dict]:
        try:
            body: dict = await request.json()
        except Exception as e:
            raise ChannelAdapter.ParseError(str(e)) from e

        messages = body.pop("messages", [])
        if not messages:
            raise ChannelAdapter.ParseError("No messages in request")

        user_message = messages[-1]
        if user_message.get("role") != "user":
            user_message = {"role": "user", "content": str(user_message.get("content", ""))}

        return user_message, body

    @staticmethod
    async def write(response: web.StreamResponse, chunks: AsyncIterator[AgentChunk]) -> None:
        """Consume the agent's ``AgentChunk`` iterator and write SSE to *response*.

        Handles ``AgentError`` and unexpected exceptions by writing an error
        ``ChatCompletionChunk`` (with ``finish_reason="error"``) before returning.
        """
        try:
            async for chunk in chunks:
                await response.write(ChannelAdapter._to_sse(chunk))
            await response.write(b"data: [DONE]\n\n")
        except AgentError as e:
            await ChannelAdapter._write_error(response, e.message)
            logger.warning(f"Agent error: {e.message!r}")
        except Exception as e:
            await ChannelAdapter._write_error(response, f"[Session Error: {e}]")
            logger.error(f"Unexpected error in agent run: {e!r}")

    @staticmethod
    def _to_sse(chunk: AgentChunk) -> bytes:
        delta = DeltaMessage(content=chunk.content, reasoning=chunk.reasoning)
        cc = ChatCompletionChunk(choices=[StreamChoice(index=0, delta=delta)])
        return cc.to_sse().encode()

    @staticmethod
    async def _write_error(response: web.StreamResponse, message: str) -> None:
        err_chunk = ChatCompletionChunk(
            id="error",
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaMessage(content=message),
                    finish_reason="error",
                )
            ],
        )
        try:
            await response.write(err_chunk.to_sse().encode())
        except Exception:
            logger.warning("Failed to write error chunk to SSE stream")
