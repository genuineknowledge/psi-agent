from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.session.protocol import ToolFunction


async def load_tools_from_workspace(
    tools_dir: Path,
) -> tuple[dict[str, ToolFunction], dict[str, Callable[..., Any]], dict[str, str]]:
    """Discover and load all tools from a workspace's ``tools/`` directory.

    Returns two dicts sharing the same keys:
    - **tools** — ``ToolFunction`` instances (name + description + JSON Schema
      parameters).  These are serialized into every AI request so the LLM knows
      what tools are available and how to call them.
    - **callables** — the actual ``async def`` functions.  The caller registers
      them on the ``SessionAgent`` so tool calls can be executed.

    A single ``importlib`` pass serves both purposes — previously the metadata
    and the callables were loaded in two separate passes over the same files.

    Loading rules:
    1. Only ``.py`` files whose name does not start with ``_`` are scanned.
    2. Any ``async def`` function in the file that does not start with ``_``
       is treated as a tool.  The function name is the tool name.
    3. If ``ToolFunction.from_callable()`` raises ``TypeError`` (unsupported
       parameter type, variadic args, bad union, etc.) the function is skipped
       with a warning.  One broken tool does not block other tools in the same
       file.
    4. Duplicate tool names across different files are skipped with a warning
       (first one wins).

    Each module is registered in ``sys.modules`` under a ``psi_tool_`` prefix
    to avoid shadowing stdlib modules (e.g. a tool file named ``json.py``
    would otherwise replace the real ``json`` module).  If the import fails
    the name is cleaned up from ``sys.modules`` to keep it importable later.
    """
    tools: dict[str, ToolFunction] = {}
    callables: dict[str, Callable[..., Any]] = {}
    file_hashes: dict[str, str] = {}
    tools_anyio = anyio.Path(str(tools_dir))

    if not await tools_anyio.is_dir():
        logger.warning(f"Tools directory not found: {tools_dir}")
        return tools, callables, file_hashes

    async for py_file in tools_anyio.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        file_path_str = str(py_file)
        file_bytes = await py_file.read_bytes()
        file_hashes[file_path_str] = hashlib.sha256(file_bytes).hexdigest()

        module_name = f"psi_tool_{py_file.stem}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec is None or spec.loader is None:
                logger.warning(f"Could not load module spec for {py_file}")
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error(f"Failed to load tool module {py_file}: {e}")
            sys.modules.pop(module_name, None)
            continue

        attr_names = sorted(name for name in dir(module) if not name.startswith("_"))
        for name in attr_names:
            func = getattr(module, name, None)
            if not inspect.iscoroutinefunction(func):
                continue

            if name in tools:
                logger.warning(f"Duplicate tool name '{name}' in {py_file}, skipping")
                continue

            try:
                tool_func = ToolFunction.from_callable(func)
            except (TypeError, NameError, AttributeError, SyntaxError, ValueError) as e:
                logger.error(f"Skipping tool '{name}' in {py_file}: {e}")
                continue

            tools[name] = tool_func
            callables[name] = func
            logger.info(f"Loaded tool: {name} from {py_file}")

    logger.info(f"Loaded {len(tools)} tool(s) from {tools_dir}")
    return tools, callables, file_hashes
