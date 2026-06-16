from __future__ import annotations

import sys

from loguru import logger
from rich.console import Console

from psi_agent.channel.session_client import stream_session_reply
from psi_agent.errors import UserFacingError

console = Console(highlight=False)


async def run_cli(*, session_socket: str, message: str) -> None:
    logger.info(f"Connecting to session at {session_socket}")

    try:
        async for delta in stream_session_reply(session_socket=session_socket, message=message):
            if delta.reasoning:
                logger.debug(f"Reasoning: {delta.reasoning}")
                console.print(delta.reasoning, style="dim", end="")
            if delta.content:
                console.print(delta.content, end="")
        console.print()
    except UserFacingError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        logger.error(f"CLI error: {e}")
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
