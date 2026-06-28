from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session._server import serve_session
from psi_agent.session.agent import SessionAgent


@dataclass
class Session:
    """Start a session backed by a workspace and AI."""

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
            agent._schedule_registry.start_all(task_group, agent)
            task_group.start_soon(partial(serve_session, channel_socket=self.channel_socket, agent=agent))
