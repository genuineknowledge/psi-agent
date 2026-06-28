from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.scheduler import run_one_schedule
from psi_agent.session.server import serve_session


@dataclass
class Session:
    """Start a session backed by a workspace and AI."""

    channel_socket: str
    ai_socket: str
    workspace: str = ""
    max_tool_rounds: int = 128
    verbose: bool = False
    session_id: str | None = None

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

        lock = anyio.Lock()

        async def channel_handler(request: web.Request) -> web.StreamResponse:
            return await ChannelAdapter.handle(request, agent, lock)

        async with anyio.create_task_group() as tg:
            tg.start_soon(partial(serve_session, channel_socket=self.channel_socket, handler=channel_handler))
            for schedule in agent.schedules:
                tg.start_soon(partial(run_one_schedule, schedule, agent, lock))
