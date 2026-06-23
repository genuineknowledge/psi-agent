from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.scheduler import run_one_schedule
from psi_agent.session.server import serve_session


@dataclass
class Session:
    """Start a session backed by a workspace and AI."""

    workspace: str
    """Path to the workspace directory."""

    channel_socket: str
    """Path for the channel Unix domain socket."""

    ai_socket: str
    """Path to the AI Unix domain socket."""

    max_tool_rounds: int = 128
    """Maximum number of tool call rounds (prevents infinite loops)."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = Path(str(await anyio.Path(self.workspace).resolve()))
        logger.info(f"Loading workspace from {workspace_path}")

        agent = await SessionAgent.create(
            ai_socket=self.ai_socket,
            workspace_path=workspace_path,
            max_tool_rounds=self.max_tool_rounds,
        )

        lock = anyio.Lock()

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                partial(
                    serve_session,
                    channel_socket=self.channel_socket,
                    handler=agent.handle_chat_completions,
                    lock=lock,
                )
            )
            for schedule in agent.schedules:
                tg.start_soon(partial(run_one_schedule, schedule, agent, lock))
