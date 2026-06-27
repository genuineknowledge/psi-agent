from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass

import anyio
from loguru import logger
from rich.console import Console

from psi_agent._logging import setup_logging
from psi_agent._socket import wait_for_socket
from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import TextChunk
from psi_agent.session import Session

console = Console(highlight=False)


def _temp_channel_socket() -> str:
    """Return a temporary channel socket path appropriate for the current platform."""
    if sys.platform == "win32":
        return f"\\\\.\\pipe\\psi-call-{uuid.uuid4().hex}"
    return f"/tmp/psi-call-{uuid.uuid4().hex}.sock"


async def call_agent(
    workspace: str,
    ai_socket: str,
    message: str,
    *,
    session_id: str | None = None,
    verbose: bool = False,
) -> str:
    """Start a session, send a message, and return the agent's response.

    Creates a temporary Session with its own channel socket, sends
    ``message`` to the agent defined by ``workspace``, and collects
    the full response as a string.

    The session is torn down automatically before returning.
    """
    channel_socket = _temp_channel_socket()

    session = Session(
        workspace=workspace,
        channel_socket=channel_socket,
        ai_socket=ai_socket,
        session_id=session_id,
        verbose=verbose,
    )

    result_parts: list[str] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(session.run)

        await wait_for_socket(channel_socket)

        try:
            async with ChannelCore(channel_socket, interval=0.0) as core:
                async for chunk in core.post([TextChunk(message)]):
                    if isinstance(chunk, TextChunk):
                        result_parts.append(chunk.text)
        finally:
            tg.cancel_scope.cancel()

    return "".join(result_parts)


@dataclass
class Call:
    """Start a session, send a message, print the response to stdout, then exit."""

    workspace: str
    """Path to the workspace directory."""

    ai_socket: str
    """AI backend address: Unix socket path, ``http://host:port``, or Named Pipe."""

    message: str
    """Message to send to the agent."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    session_id: str | None = None
    """Session history identifier.  ``None`` → auto-generate UUID."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        try:
            text = await call_agent(
                workspace=self.workspace,
                ai_socket=self.ai_socket,
                message=self.message,
                session_id=self.session_id,
                verbose=self.verbose,
            )
            console.print(text)
        except Exception as e:
            logger.error(f"Call error: {e}")
            console.print(f"\n[Error: {e}]")
            raise
            raise
