from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.server import serve_session


@dataclass
class Session:
    """CLI entry point and orchestrator for the Session layer.

    Creates a ``SessionAgent`` from a workspace, then starts the
    ``serve_session`` server and all schedule runners in an anyio task
    group.  The dataclass form makes it directly usable as a
    ``tyro.cli`` subcommand.
    """

    ai_socket: str
    channel_socket: str
    workspace: str = ""
    max_tool_rounds: int = 128
    session_id: str | None = None
    verbose: bool = False

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = Path.cwd() if self.workspace == "" else Path(str(await anyio.Path(self.workspace).resolve()))
        logger.info(f"Loading workspace from {workspace_path}")

        agent = await SessionAgent.create(
            ai_socket=self.ai_socket,
            workspace_path=workspace_path,
            max_tool_rounds=self.max_tool_rounds,
            session_id=self.session_id,
        )

        async with anyio.create_task_group() as task_group:
            agent.set_after_turn_task_group(task_group)
            agent.start_all(task_group)
            task_group.start_soon(partial(serve_session, channel_socket=self.channel_socket, agent=agent))
