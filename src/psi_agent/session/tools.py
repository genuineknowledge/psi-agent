from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import anyio
from loguru import logger

from psi_agent.session.protocol import ToolFunction

ToolCallable = Callable[..., Awaitable[Any]]


def _load_tool_module(py_file: anyio.Path, module_name: str) -> object | None:
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(py_file))
        if spec is None or spec.loader is None:
            logger.warning(f"Could not load module spec for {py_file}")
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logger.error(f"Failed to load tool module {py_file}: {e}")
        return None


def _get_tool_callable(module: object, tool_name: str, py_file: anyio.Path) -> ToolCallable | None:
    candidates = (tool_name, "tool")
    for candidate in candidates:
        func = getattr(module, candidate, None)
        if func is None:
            continue
        if not inspect.iscoroutinefunction(func):
            logger.warning(f"Function '{candidate}' in {py_file} is not async, skipping")
            continue
        if candidate.startswith("_"):
            logger.debug(f"Skipping private function '{candidate}'")
            continue
        return cast(ToolCallable, func)

    logger.warning(f"Function '{tool_name}' or fallback 'tool' not found in {py_file}")
    return None


async def load_tool_callables_from_workspace(tools_dir: Path) -> dict[str, ToolCallable]:
    funcs: dict[str, ToolCallable] = {}
    tools_anyio = anyio.Path(str(tools_dir))

    if not await tools_anyio.is_dir():
        logger.warning(f"Tools directory not found: {tools_dir}")
        return funcs

    async for py_file in tools_anyio.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        tool_name = py_file.stem
        module = _load_tool_module(py_file, f"psi_tool_{tool_name}")
        if module is None:
            continue

        func = _get_tool_callable(module, tool_name, py_file)
        if func is None:
            continue

        funcs[tool_name] = func

    return funcs


async def load_tools_from_workspace(tools_dir: Path) -> dict[str, ToolFunction]:
    tools: dict[str, ToolFunction] = {}
    callables = await load_tool_callables_from_workspace(tools_dir)
    for tool_name, func in callables.items():
        tool_func = ToolFunction.from_callable(func)
        tool_func.name = tool_name
        tools[tool_name] = tool_func
        logger.info(f"Loaded tool: {tool_name} from {tools_dir / (tool_name + '.py')}")

    logger.info(f"Loaded {len(tools)} tool(s) from {tools_dir}")
    return tools
