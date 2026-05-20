from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.scheduler import load_schedules_from_workspace
from psi_agent.session.server import serve_session
from psi_agent.session.tools import load_tools_from_workspace


def _load_system_prompt_builder(workspace_path: Path) -> Any:
    system_py = workspace_path / "systems" / "system.py"
    if not system_py.exists():
        logger.warning(f"No system.py found at {system_py}")
        return None
    try:
        spec = importlib.util.spec_from_file_location("psi_workspace_system", str(system_py))
        if spec is None or spec.loader is None:
            logger.error(f"Failed to load {system_py}")
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules["psi_workspace_system"] = module
        spec.loader.exec_module(module)
        func = getattr(module, "system_prompt_builder", None)
        if func is None or not inspect.iscoroutinefunction(func):
            logger.warning(f"system_prompt_builder not found or not async in {system_py}")
            return None
        return func
    except Exception as e:
        logger.error(f"Failed to load system_prompt_builder: {e}")
        return None


@dataclass
class SessionConfig:
    """Start a session backed by a workspace and AI."""

    workspace: str
    """Path to the workspace directory."""

    channel_socket: str
    """Path for the channel Unix domain socket."""

    ai_socket: str
    """Path to the AI Unix domain socket."""

    model: str = "gpt-4"
    """Model name to pass to the AI backend."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        workspace_path = Path(str(await anyio.Path(self.workspace).resolve()))
        logger.info(f"Loading workspace from {workspace_path}")

        tools = await load_tools_from_workspace(workspace_path / "tools")
        schedules = await load_schedules_from_workspace(workspace_path / "schedules")

        system_prompt = None
        builder = _load_system_prompt_builder(workspace_path)
        if builder:
            try:
                system_prompt = await builder()
                logger.info(f"System prompt loaded ({len(system_prompt) if system_prompt else 0} chars)")
            except Exception as e:
                logger.error(f"Failed to build system prompt: {e}")

        agent = SessionAgent(
            ai_socket=self.ai_socket,
            tools=tools,
            model=self.model,
            system_prompt=system_prompt,
        )

        # Register actual tool callables
        tools_anyio = anyio.Path(str(workspace_path / "tools"))
        if await tools_anyio.is_dir():
            async for py_file in tools_anyio.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                name = py_file.stem
                try:
                    spec = importlib.util.spec_from_file_location(f"psi_tool_{name}", str(py_file))
                    if spec is None or spec.loader is None:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[f"psi_tool_{name}"] = module
                    spec.loader.exec_module(module)
                    func = getattr(module, name, None)
                    if func and inspect.iscoroutinefunction(func):
                        agent.register_tool_func(name, func)
                        logger.info(f"Registered tool callable: {name}")
                except Exception as e:
                    logger.error(f"Failed to register tool {name}: {e}")

        lock = anyio.Lock()

        async def schedule_loop() -> None:
            logger.info(f"Schedule runner started with {len(schedules)} schedule(s)")
            while True:
                await anyio.sleep(30.0)
                for schedule in schedules:
                    try:
                        if schedule.should_run_now():
                            logger.info(f"Schedule triggered: {schedule.name}")
                            schedule.mark_run()
                            msg = schedule.to_user_message()

                            async with lock:
                                pending_chunks: list = []
                                async for chunk in agent.run(msg):
                                    pending_chunks.append(chunk)
                                agent.set_pending_schedule_chunks(pending_chunks)
                                logger.info(f"Schedule {schedule.name} response stored ({len(pending_chunks)} chunks)")
                    except Exception as e:
                        logger.error(f"Error processing schedule {schedule.name}: {e}")

        async with anyio.create_task_group() as tg:
            tg.start_soon(schedule_loop)
            tg.start_soon(partial(serve_session, channel_socket=self.channel_socket, agent=agent, lock=lock))
