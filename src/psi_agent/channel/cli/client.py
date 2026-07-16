from __future__ import annotations

import sys
from contextlib import aclosing

import anyio
from loguru import logger
from rich.console import Console

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk


async def run_cli(*, session_socket: str, message: str) -> None:
    if message == "-":
        message = await anyio.to_thread.run_sync(sys.stdin.read, abandon_on_cancel=True)  # ty: ignore
    console = Console(highlight=False)
    logger.info(f"Connecting to session at {session_socket}")

    try:
        async with (
            ChannelCore(session_socket, interval=0.0) as core,
            aclosing(core.post([TextChunk(message)])) as stream,
        ):
            async for chunk in stream:
                if isinstance(chunk, ReasoningChunk):
                    console.print(chunk.text, end="", style="dim")
                elif isinstance(chunk, TextChunk):
                    console.print(chunk.text, end="")
    except Exception as e:
        logger.error(f"CLI error: {e!r}")
        console.print(f"[red]Error: {e}[/red]")
        raise

    console.print()
