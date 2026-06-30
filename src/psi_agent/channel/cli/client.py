from __future__ import annotations

from contextlib import aclosing

from loguru import logger
from rich.console import Console

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk

console = Console(highlight=False)


async def run_cli(*, session_socket: str, message: str) -> None:
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
        logger.error(f"CLI error: {e}")
        console.print(f"[red]Error: {e}[/red]")
        raise

    console.print()
