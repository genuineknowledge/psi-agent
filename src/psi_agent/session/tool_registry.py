"""Tool loading and incremental refresh from ``workspace/tools/``.

``ToolRegistry`` loads async Python functions from ``workspace/tools/``
via ``importlib``, converts signatures to JSON Schema via
``ToolFunction.from_callable()``, tracks SHA-256 file hashes for
incremental refresh, and provides ``get(name)`` for tool execution
lookup.
"""

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


class ToolRegistry:
    """Owns tool metadata and callables, loaded from ``workspace/tools/``.

    ``tools`` is public so that ``agent.run()`` can iterate it directly;
    ``get(name)`` returns the callable for tool execution.
    """

    def __init__(
        self,
        *,
        tools: dict[str, ToolFunction] | None = None,
        funcs: dict[str, Callable[..., Any]] | None = None,
        file_hashes: dict[str, str] | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self.tools: dict[str, ToolFunction] = dict(tools or {})
        self._funcs: dict[str, Callable[..., Any]] = dict(funcs or {})
        self._file_hashes: dict[str, str] = dict(file_hashes or {})
        self._work_dir = work_dir

    def get(self, name: str) -> Callable[..., Any] | None:
        """Return the callable for *name*, or None if not registered."""
        return self._funcs.get(name)

    # -- loading ---------------------------------------------------------------

    @classmethod
    async def load(cls, tools_dir: Path, session_id: str = "") -> ToolRegistry:
        """Full initial load — scan *tools_dir* and import everything."""
        tools, funcs, file_hashes = await cls._load_from_dir(tools_dir, session_id)
        return cls(tools=tools, funcs=funcs, file_hashes=file_hashes, work_dir=tools_dir)

    async def refresh(self, session_id: str) -> dict[str, str]:
        """Incremental reload — only changed or new files.

        Returns a dict mapping tool name to ``'added'``, ``'updated'``,
        or ``'skipped'``.
        """
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh tools")
            return {}

        new_tools, new_funcs, new_hashes = await self._load_from_dir(self._work_dir, session_id)
        result: dict[str, str] = {}
        for name, tf in new_tools.items():
            if name not in self.tools:
                self.tools[name] = tf
                self._funcs[name] = new_funcs[name]
                result[name] = "added"
            elif new_hashes.get(name) != self._file_hashes.get(name):
                self.tools[name] = tf
                self._funcs[name] = new_funcs[name]
                result[name] = "updated"
            else:
                result[name] = "skipped"
        self._file_hashes = new_hashes
        changed = {k: v for k, v in result.items() if v != "skipped"}
        logger.info(f"Tool refresh complete: {changed or 'no changes'}")
        return result

    # -- internals -------------------------------------------------------------

    @staticmethod
    async def _load_from_dir(
        tools_dir: Path, session_id: str
    ) -> tuple[dict[str, ToolFunction], dict[str, Callable[..., Any]], dict[str, str]]:
        """Scan and import all tool ``.py`` files.  Returns (tools, funcs, file_hashes)."""
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
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            file_hashes[file_path_str] = file_hash

            module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash[:12]}"

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
