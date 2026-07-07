from __future__ import annotations

from collections.abc import Sequence
from contextlib import aclosing

from loguru import logger
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from rich.panel import Panel

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk
from psi_agent.channel.route import select_model_for_message


async def run_repl(
    *,
    session_socket: str,
    models: Sequence[str] = (),
) -> None:
    console = Console(highlight=False)
    logger.info(f"Connecting to session at {session_socket}")

    prompt_session = PromptSession(multiline=True)

    try:
        async with ChannelCore(session_socket, interval=0.0) as core:
            logger.info("Connected to session. Enter for newline, Alt+Enter to send (Ctrl+D to exit).")
            console.print(Panel.fit("psi-agent REPL - Enter newline, Alt+Enter send"))
            console.print("[dim]Ctrl+D to exit[/dim]\n")

            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ", prompt_continuation=". ")
                except (EOFError, KeyboardInterrupt):
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                selected_model = await select_model_for_message(
                    user_input,
                    models=models,
                )

                console.print()
                try:
                    async with aclosing(
                        core.post([TextChunk(user_input)], model=selected_model)
                    ) as stream:
                        async for chunk in stream:
                            if isinstance(chunk, ReasoningChunk):
                                console.print(chunk.text, end="", style="dim")
                            elif isinstance(chunk, TextChunk):
                                console.print(chunk.text, end="")
                except Exception as e:
                    logger.error(f"REPL error: {e!r}")
                    console.print(f"\n[red]Error: {e}[/red]")
                console.print("\n")
    except Exception as e:
        logger.exception("Unexpected REPL error")
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise SystemExit(1) from e
