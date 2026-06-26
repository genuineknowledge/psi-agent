from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session._routing import build_effective_model_ai_sockets
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

    model_names: list[str] = field(default_factory=list)
    """Optional model names to auto-map to sibling ``<model>.sock`` files."""

    model_ai_sockets: dict[str, str] = field(default_factory=dict)
    """Optional explicit mapping from request model name to AI socket path."""

    max_tool_rounds: int = 128
    """Maximum number of tool call rounds (prevents infinite loops)."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    session_id: str | None = None
    """Session history identifier.  None → auto-generate UUID."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = Path(str(await anyio.Path(self.workspace).resolve()))
        logger.info(f"Loading workspace from {workspace_path}")

        effective_model_ai_sockets = build_effective_model_ai_sockets(
            self.ai_socket,
            self.model_names,
            explicit_model_ai_sockets=self.model_ai_sockets,
        )

        agent = await SessionAgent.create(
            ai_socket=self.ai_socket,
            model_ai_sockets=effective_model_ai_sockets,
            workspace_path=workspace_path,
            max_tool_rounds=self.max_tool_rounds,
            session_id=self.session_id,
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
