from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.protocol import AgentChunk, AgentError, ChatCompletionChunk, DeltaMessage, StreamChoice

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


class ChannelAdapter:
    """Protocol adapter for the Channel side — parse request, convert AgentChunk -> SSE."""

    @staticmethod
    async def handle(request: web.Request, agent: SessionAgent, lock: anyio.Lock) -> web.StreamResponse:
        try:
            user_message, extra_params = await ChannelAdapter.parse_request(request)
        except ChannelAdapter._ParseError as e:
            return web.json_response(
                {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
                status=400,
            )

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        async with lock:
            await response.prepare(request)
            logger.info("Acquired session lock, processing request")
            try:
                async for chunk in agent.run(user_message, extra_params=extra_params):
                    await response.write(ChannelAdapter.to_chat_completion_chunk(chunk).to_sse().encode())
                    logger.debug(f"Chunk sent: content={chunk.content!r}, reasoning={chunk.reasoning!r}")
                await response.write(b"data: [DONE]\n\n")
            except AgentError as e:
                err_chunk = ChatCompletionChunk(
                    id="error",
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content=e.message),
                            finish_reason="error",
                        )
                    ],
                )
                await response.write(err_chunk.to_sse().encode())
                logger.warning(f"Agent error: {e.message}")
            except Exception as e:
                err_chunk = ChatCompletionChunk(
                    id="error",
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content=f"[Session Error: {e}]"),
                            finish_reason="error",
                        )
                    ],
                )
                await response.write(err_chunk.to_sse().encode())
                logger.error(f"Unexpected error in agent run: {e}")

        logger.debug("Session request completed")
        return response

    class _ParseError(Exception):
        pass

    @staticmethod
    async def parse_request(request: web.Request) -> tuple[dict, dict]:
        try:
            body: dict = await request.json()
        except Exception as e:
            raise ChannelAdapter._ParseError(str(e)) from e

        messages = body.pop("messages", [])
        if not messages:
            raise ChannelAdapter._ParseError("No messages in request")

        user_message = messages[-1]
        if user_message.get("role") != "user":
            user_message = {"role": "user", "content": str(user_message.get("content", ""))}

        return user_message, body

    @staticmethod
    def to_chat_completion_chunk(chunk: AgentChunk) -> ChatCompletionChunk:
        delta = DeltaMessage(content=chunk.content, reasoning=chunk.reasoning)
        return ChatCompletionChunk(choices=[StreamChoice(index=0, delta=delta)])
