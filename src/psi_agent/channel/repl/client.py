from __future__ import annotations

from contextlib import aclosing

from aiohttp import ClientConnectorError
from loguru import logger
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.panel import Panel

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk


async def run_repl(*, session_socket: str) -> None:
    console = Console(highlight=False)
    logger.info(f"Connecting to session at {session_socket}")

    prompt_session = PromptSession(multiline=True)

    try:
        async with ChannelCore(session_socket, interval=0.0) as core:
            logger.info("Connected to session. Enter for newline, Alt+Enter to send (Ctrl+D to exit).")
            console.print(Panel.fit("psi-agent REPL — Enter newline, Alt+Enter send"))
            console.print("[dim]Ctrl+D to exit[/dim]\n")

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ", prompt_continuation=". ")
                except EOFError, KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                console.print()
                try:
                    async with aclosing(core.post([TextChunk(user_input)])) as stream:
                        async for chunk in stream:
                            if isinstance(chunk, ReasoningChunk):
                                console.print(chunk.text, end="", style="dim")
                            elif isinstance(chunk, TextChunk):
                                console.print(chunk.text, end="")
                except Exception as e:
                    logger.error(f"REPL error: {e}")
                    console.print(f"\n[red]Error: {e}[/red]")
                console.print("\n")

    except ClientConnectorError as e:
        console.print(f"[red]Connection error: {e}[/red]")
        raise
    except Exception as e:
        logger.exception("Unexpected REPL error")
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise
