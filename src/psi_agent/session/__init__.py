from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from loguru import logger

from psi_agent._app_paths import (
    default_agent_path,
    default_workspace_path,
    history_dir as app_history_dir,
)
from psi_agent._history_meta import upsert_history_meta
from psi_agent._logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.server import serve_session


@dataclass
class Session:
    """CLI entry point and orchestrator for the Session layer."""

    ai_socket: str
    channel_socket: str
    workspace: str = ""
    """User workspace (open folder). Empty → ``default_workspace_path()``."""

    agent: str = ""
    """Agent package path. Empty → ``default_agent_path()`` (examples/haitun). Hook for future agent switch."""

    max_tool_rounds: int = 128
    session_id: str | None = None
    verbose: bool = False
    app_data_root: str = ""
    """Optional AppData root override (tests / portable). Empty → platformdirs."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = (
            default_workspace_path()
            if self.workspace == ""
            else Path(str(await anyio.Path(self.workspace).resolve()))
        )
        agent_path = (
            default_agent_path()
            if self.agent == ""
            else Path(str(await anyio.Path(self.agent).resolve()))
        )
        app_override = self.app_data_root.strip() or None
        hist_root = app_history_dir(override=app_override)

        logger.info(f"Loading agent from {agent_path}")
        logger.info(f"User workspace: {workspace_path}")
        logger.info(f"History dir: {hist_root}")

        agent = await SessionAgent.create(
            ai_socket=self.ai_socket,
            workspace_path=workspace_path,
            agent_path=agent_path,
            history_dir=hist_root,
            max_tool_rounds=self.max_tool_rounds,
            session_id=self.session_id,
        )

        await upsert_history_meta(
            session_id=agent._conversation.session_id,
            workspace=str(workspace_path),
            agent=str(agent_path),
            app_data_override=app_override,
        )

        async with anyio.create_task_group() as task_group:
            agent.start_all(task_group)
            task_group.start_soon(partial(serve_session, channel_socket=self.channel_socket, agent=agent))
