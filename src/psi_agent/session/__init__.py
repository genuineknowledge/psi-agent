from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from hashlib import sha1
from pathlib import Path
from typing import Any

import anyio
from aiohttp import ClientTimeout
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.net import make_client_session
from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ToolFunction
from psi_agent.session.scheduler import Schedule, load_schedules_from_workspace
from psi_agent.session.server import serve_session
from psi_agent.session.tools import ToolCallable, load_tool_callables_from_workspace
from psi_agent.workspace import resolve_workspace_path

CompleteFn = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], Awaitable[dict[str, Any]]]


def _load_system_module(workspace_path: Path) -> object | None:
    system_py = workspace_path / "systems" / "system.py"
    if not system_py.exists():
        logger.warning(f"No system.py found at {system_py}")
        return None
    try:
        module_hash = sha1(str(system_py.resolve()).encode("utf-8")).hexdigest()[:12]
        module_name = f"psi_workspace_system_{module_hash}"
        spec = importlib.util.spec_from_file_location(module_name, str(system_py))
        if spec is None or spec.loader is None:
            logger.error(f"Failed to load {system_py}")
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logger.error(f"Failed to load workspace system module: {e}")
        return None


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
        module = _load_system_module(workspace_path)
        if module is None:
            return None
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


def _make_complete_fn(*, ai_socket: str, model: str) -> CompleteFn:
    async def complete_fn(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request_body["tools"] = tools

        client_session, endpoint = make_client_session(ai_socket, timeout=ClientTimeout(total=300))
        async with client_session as session, session.post(endpoint, json=request_body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"AI completion failed with status {resp.status}: {error_text[:500]}")

            role: str | None = None
            finish_reason: str | None = None
            content_parts: list[str] = []
            accumulated_tool_calls: dict[int, dict[str, Any]] = {}

            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse after-turn SSE data: {data_str[:100]}")
                    continue

                for choice in data.get("choices", []):
                    if choice.get("finish_reason") and not finish_reason:
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta", {})
                    if delta.get("role"):
                        role = delta["role"]
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                    for tool_call in delta.get("tool_calls") or []:
                        idx = tool_call.get("index", 0)
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": tool_call.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        acc = accumulated_tool_calls[idx]
                        if tool_call.get("id"):
                            acc["id"] = tool_call["id"]
                        func = tool_call.get("function", {})
                        if func.get("name"):
                            acc["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            acc["function"]["arguments"] += func["arguments"]

        message: dict[str, Any] = {
            "role": role or "assistant",
            "content": "".join(content_parts),
        }
        if accumulated_tool_calls:
            message["tool_calls"] = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

        return {
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ]
        }

    return complete_fn


def _load_after_turn_fn(
    workspace_path: Path,
    *,
    ai_socket: str,
    model: str,
    tool_executors: dict[str, ToolCallable],
) -> Any:
    module = _load_system_module(workspace_path)
    if module is None:
        return None

    system_class = getattr(module, "System", None)
    if system_class is None:
        logger.debug(f"System class not found in {workspace_path / 'systems' / 'system.py'}")
        return None

    try:
        system = system_class(anyio.Path(str(workspace_path)))
    except Exception as e:
        logger.error(f"Failed to instantiate System for after_turn: {e}")
        return None

    method = getattr(system, "after_turn", None)
    if method is None:
        logger.debug(f"System.after_turn not found in {workspace_path / 'systems' / 'system.py'}")
        return None
    if not inspect.iscoroutinefunction(method):
        logger.warning(f"System.after_turn in {workspace_path / 'systems' / 'system.py'} is not async")
        return None

    complete_fn = _make_complete_fn(ai_socket=ai_socket, model=model)
    params = inspect.signature(method).parameters
    accepts_kwargs = any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())

    async def after_turn(messages: list[dict], tool_call_count: int, called_tools: list[str]) -> None:
        kwargs: dict[str, Any] = {}
        if accepts_kwargs or "complete_fn" in params:
            kwargs["complete_fn"] = complete_fn
        if accepts_kwargs or "tool_executors" in params:
            kwargs["tool_executors"] = tool_executors
        await method(messages, tool_call_count, called_tools, **kwargs)

    return after_turn


async def _run_one_schedule(
    schedule: Schedule,
    agent: SessionAgent,
    lock: anyio.Lock,
    after_turn_task_group: Any | None = None,
) -> None:
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
                    agent.spawn_after_turn_task(after_turn_task_group)
                    logger.info(f"Schedule {schedule.name} response stored ({len(pending_chunks)} chunks)")
        except Exception as e:
            logger.error(f"Error processing schedule {schedule.name}: {e}")


async def build_session_agent(
    *,
    workspace: str,
    ai_socket: str,
    model: str,
) -> SessionAgent:
    workspace_path = resolve_workspace_path(workspace)
    logger.info(f"Loading workspace from {workspace_path}")

    tools, tool_callables = await _load_workspace_tools_and_callables(workspace_path)
    system_prompt = await _build_system_prompt_from_workspace(
        workspace_path,
        model=model,
        tool_names=list(tools),
    )
    after_turn_fn = _load_after_turn_fn(
        workspace_path,
        ai_socket=ai_socket,
        model=model,
        tool_executors=tool_callables,
    )

    agent = SessionAgent(
        ai_socket=ai_socket,
        tools=tools,
        model=model,
        system_prompt=system_prompt,
        after_turn_fn=after_turn_fn,
    )

    _register_tool_callables(agent, tool_callables)
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


async def _load_workspace_tools_and_callables(
    workspace_path: Path,
) -> tuple[dict[str, ToolFunction], dict[str, ToolCallable]]:
    tools_dir = workspace_path / "tools"
    callables = await load_tool_callables_from_workspace(tools_dir)
    tools: dict[str, ToolFunction] = {}
    for tool_name, func in callables.items():
        tool_func = ToolFunction.from_callable(func)
        tool_func.name = tool_name
        tools[tool_name] = tool_func
        logger.info(f"Loaded tool: {tool_name} from {tools_dir / (tool_name + '.py')}")

    logger.info(f"Loaded {len(tools)} tool(s) from {tools_dir}")
    return tools, callables


def _register_tool_callables(agent: SessionAgent, callables: dict[str, ToolCallable]) -> None:
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

        workspace_path = resolve_workspace_path(self.workspace)
        logger.info(f"Loading workspace from {workspace_path}")

        tools, tool_callables = await _load_workspace_tools_and_callables(workspace_path)
        schedules = await load_schedules_from_workspace(workspace_path / "schedules")
        system_prompt = await _build_system_prompt_from_workspace(
            workspace_path,
            model=self.model,
            tool_names=list(tools),
        )
        after_turn_fn = _load_after_turn_fn(
            workspace_path,
            ai_socket=self.ai_socket,
            model=self.model,
            tool_executors=tool_callables,
        )

        agent = SessionAgent(
            ai_socket=self.ai_socket,
            tools=tools,
            model=self.model,
            system_prompt=system_prompt,
            after_turn_fn=after_turn_fn,
        )

        _register_tool_callables(agent, tool_callables)

        lock = anyio.Lock()

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                partial(
                    serve_session,
                    channel_socket=self.channel_socket,
                    agent=agent,
                    lock=lock,
                    after_turn_task_group=tg,
                )
            )
            for schedule in schedules:
                tg.start_soon(partial(_run_one_schedule, schedule, agent, lock, tg))
