from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from psi_agent.session.protocol import ToolFunction
from psi_agent.session.tools import load_tools_from_workspace


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

    @classmethod
    async def load(cls, tools_dir: Path, session_id: str) -> ToolRegistry:
        """Full initial load — scan *tools_dir* and import everything."""
        tools, funcs, file_hashes = await load_tools_from_workspace(tools_dir, session_id)
        return cls(tools=tools, funcs=funcs, file_hashes=file_hashes, work_dir=tools_dir)

    async def refresh(self, session_id: str) -> dict[str, str]:
        """Incremental reload — only changed or new files.

        Returns a dict mapping tool name to ``'added'``, ``'updated'``,
        or ``'skipped'``.
        """
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh tools")
            return {}

        new_tools, new_funcs, new_hashes = await load_tools_from_workspace(self._work_dir, session_id)
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
