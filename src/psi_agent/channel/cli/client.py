from __future__ import annotations

from collections.abc import Sequence
from contextlib import aclosing

from loguru import logger
from rich.console import Console

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk
from psi_agent.channel.route import select_model_for_message


async def run_cli(
    *,
    session_socket: str,
    message: str,
    models: Sequence[str] = (),
) -> None:
    console = Console(highlight=False)
    logger.info(f"Connecting to session at {session_socket}")

    selected_model = await select_model_for_message(
        message,
        models=models,
    )

    try:
        async with (
            ChannelCore(session_socket, interval=0.0) as core,
            aclosing(core.post([TextChunk(message)], model=selected_model)) as stream,
        ):
            async for chunk in stream:
                if isinstance(chunk, ReasoningChunk):
                    console.print(chunk.text, end="", style="dim")
                elif isinstance(chunk, TextChunk):
                    console.print(chunk.text, end="")
    except Exception as e:
        logger.error(f"CLI error: {e!r}")
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1) from e
