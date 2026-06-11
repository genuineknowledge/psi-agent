from __future__ import annotations

import importlib.util
import inspect
import sys
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.session.agent import SessionAgent
from psi_agent.session.scheduler import Schedule, load_schedules_from_workspace
from psi_agent.session.server import serve_session
from psi_agent.session.tools import load_tool_callables_from_workspace, load_tools_from_workspace


def _load_system_prompt_builder(
    workspace_path: Path,
    *,
    model: str | None = None,
    tool_names: list[str] | None = None,
) -> Any:
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
        if func is not None:
            if not inspect.iscoroutinefunction(func):
                logger.warning(f"system_prompt_builder in {system_py} is not async")
                return None
            return func

        system_class = getattr(module, "System", None)
        if system_class is None:
            logger.warning(f"system_prompt_builder or System class not found in {system_py}")
            return None

        system = system_class(anyio.Path(str(workspace_path)))
        method = getattr(system, "build_system_prompt", None)
        if method is None or not inspect.iscoroutinefunction(method):
            logger.warning(f"System.build_system_prompt not found or not async in {system_py}")
            return None

        async def build_from_system_class() -> str:
            params = inspect.signature(method).parameters
            kwargs: dict[str, Any] = {}
            if "model" in params:
                kwargs["model"] = model
            if "tool_names" in params:
                kwargs["tool_names"] = tool_names or []
            return await method(**kwargs)

        return build_from_system_class
    except Exception as e:
        logger.error(f"Failed to load system_prompt_builder: {e}")
        return None


async def _run_one_schedule(schedule: Schedule, agent: SessionAgent, lock: anyio.Lock) -> None:
    logger.info(f"Schedule runner started: {schedule.name} ({schedule.cron})")
    while True:
        try:
            next_run = schedule.get_next_run()
            wait = max(0.0, next_run - time.time())
        except ValueError:
            logger.error(f"Invalid cron for schedule {schedule.name}, retrying in 60s")
            await anyio.sleep(60.0)
            continue

        await anyio.sleep(wait)
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


async def build_session_agent(
    *,
    workspace: str,
    ai_socket: str,
    model: str,
) -> SessionAgent:
    workspace_path = Path(str(await anyio.Path(workspace).resolve()))
    logger.info(f"Loading workspace from {workspace_path}")

    tools = await load_tools_from_workspace(workspace_path / "tools")
    system_prompt = await _build_system_prompt_from_workspace(
        workspace_path,
        model=model,
        tool_names=list(tools),
    )

    agent = SessionAgent(
        ai_socket=ai_socket,
        tools=tools,
        model=model,
        system_prompt=system_prompt,
    )

    await _register_tool_callables(workspace_path, agent)
    return agent


async def _build_system_prompt_from_workspace(
    workspace_path: Path,
    *,
    model: str | None = None,
    tool_names: list[str] | None = None,
) -> str | None:
    builder = _load_system_prompt_builder(workspace_path, model=model, tool_names=tool_names)
    if builder is None:
        return None
    try:
        sp = await builder()
        logger.info(f"System prompt loaded ({len(sp) if sp else 0} chars)")
        return sp
    except Exception as e:
        logger.error(f"Failed to build system prompt: {e}")
        return None


async def _register_tool_callables(workspace_path: Path, agent: SessionAgent) -> None:
    callables = await load_tool_callables_from_workspace(workspace_path / "tools")
    for name, func in callables.items():
        agent.register_tool_func(name, func)
        logger.info(f"Registered tool callable: {name}")


@dataclass
class Session:
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
        system_prompt = await _build_system_prompt_from_workspace(
            workspace_path,
            model=self.model,
            tool_names=list(tools),
        )

        agent = SessionAgent(
            ai_socket=self.ai_socket,
            tools=tools,
            model=self.model,
            system_prompt=system_prompt,
        )

        await _register_tool_callables(workspace_path, agent)

        lock = anyio.Lock()

        async with anyio.create_task_group() as tg:
            tg.start_soon(partial(serve_session, channel_socket=self.channel_socket, agent=agent, lock=lock))
            for schedule in schedules:
                tg.start_soon(partial(_run_one_schedule, schedule, agent, lock))
