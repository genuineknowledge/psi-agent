from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session._routing import build_effective_model_ai_sockets
from psi_agent.session.agent import SessionAgent
from psi_agent.session.server import serve_session


@dataclass
class Session:
    """CLI entry point and orchestrator for the Session layer."""

    ai_socket: str
    channel_socket: str
    model_names: list[str] = field(default_factory=list)
    model_ai_sockets: dict[str, str] = field(default_factory=dict)
    workspace: str = ""
    max_tool_rounds: int = 128
    session_id: str | None = None
    verbose: bool = False

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = Path.cwd() if self.workspace == "" else Path(str(await anyio.Path(self.workspace).resolve()))
        logger.info(f"Loading workspace from {workspace_path}")

        effective_model_ai_sockets = build_effective_model_ai_sockets(
            self.ai_socket,
            self.model_names,
            explicit_model_ai_sockets=self.model_ai_sockets,
        )
        if self.ai_socket.startswith(("http://", "https://")):
            unmapped_models = [
                model_name
                for model_name in self.model_names
                if model_name and model_name not in effective_model_ai_sockets
            ]
            if unmapped_models:
                logger.warning(
                    "Remote TCP ai_socket does not auto-expand model_names; "
                    "these models will fall back to the default ai_socket unless explicitly mapped: "
                    f"{unmapped_models!r}"
                )

        agent = await SessionAgent.create(
            ai_socket=self.ai_socket,
            model_ai_sockets=effective_model_ai_sockets,
            workspace_path=workspace_path,
            max_tool_rounds=self.max_tool_rounds,
            session_id=self.session_id,
        )

        async with anyio.create_task_group() as task_group:
            agent.start_all(task_group)
            task_group.start_soon(partial(serve_session, channel_socket=self.channel_socket, agent=agent))
