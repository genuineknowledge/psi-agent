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
import re
import sys
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

# ── ToolFunction — metadata + annotation parsing ─────────────────────────────


@dataclass
class ToolFunction:
    """OpenAI function-calling tool definition built from a Python function.

    ``name``, ``description``, and ``parameters`` are the three fields
    sent to the LLM so it knows what tools are available and how to call
    them.  ``from_callable()`` inspects a function's signature and
    docstring to produce the JSON Schema for ``parameters``.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    @classmethod
    def from_callable(cls, func: Any) -> ToolFunction:
        """Build a tool definition from an async Python function.

        Google-style docstrings are expected:
        - Everything before ``Args:`` is the tool description.
        - Each ``name: text`` line in ``Args:`` maps a parameter to its
          description.

        Type annotations are resolved via ``typing.get_type_hints()``
        because the project uses ``from __future__ import annotations``
        which stores all annotations as strings.
        """
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        description = cls._parse_description(doc)
        param_desc = cls._parse_param_descriptions(doc)

        type_hints = typing.get_type_hints(func)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                raise TypeError(
                    f"Variadic parameters (*args, **kwargs) are not supported in tools: "
                    f"'{param_name}' in '{func.__name__}'"
                )

            annotation = type_hints.get(param_name)

            is_type_optional = False
            if annotation is not None:
                origin = getattr(annotation, "__origin__", None)
                if origin is types.UnionType:
                    args = getattr(annotation, "__args__", ())
                    non_none = [a for a in args if a is not type(None)]
                    if len(non_none) == 1:
                        annotation = non_none[0]
                        is_type_optional = True
                    else:
                        raise TypeError(
                            f"Unsupported union type: {annotation!r}. Use X | None for a single optional type."
                        )

            if annotation is not None:
                _type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
                origin = getattr(annotation, "__origin__", None)
                if origin is not None:
                    if origin is not list:
                        raise TypeError(f"Unsupported generic type: {annotation!r}. Only list[X] is supported.")
                    args = getattr(annotation, "__args__", ())
                    item = args[0] if args else str
                    if getattr(item, "__origin__", None) is not None or item not in _type_map:
                        raise TypeError(f"Unsupported list item type: {item!r}. Supported: str, int, float, bool")
                    resolved = {"type": "array", "items": {"type": _type_map[item]}}
                elif annotation not in _type_map:
                    raise TypeError(
                        f"Unsupported parameter type: {annotation!r}. Supported: str, int, float, bool, list[X]"
                    )
                else:
                    resolved = {"type": _type_map[annotation]}
            else:
                resolved = {"type": "string"}

            properties[param_name] = resolved | {"description": param_desc.get(param_name, "")}

            if param.default is inspect.Parameter.empty and not is_type_optional:
                required.append(param_name)

        return cls(
            name=func.__name__,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )

    @staticmethod
    def _parse_description(doc: str) -> str:
        """Everything before the first ``Args:``, ``Returns:``, or ``Yields:``."""
        lines = doc.strip().split("\n")
        desc_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Args:") or stripped.startswith("Returns:") or stripped.startswith("Yields:"):
                break
            if stripped:
                desc_lines.append(stripped)
        return " ".join(desc_lines)

    @staticmethod
    def _parse_param_descriptions(doc: str) -> dict[str, str]:
        """Parse the ``Args:`` section of a Google-style docstring."""
        result: dict[str, str] = {}
        in_args = False
        current_param = ""
        current_desc: list[str] = []
        for line in doc.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Args:"):
                in_args = True
                continue
            if in_args:
                if stripped.startswith("Returns:") or stripped.startswith("Yields:"):
                    break
                m = re.match(r"^(\w+):\s*(.*)", stripped)
                if m:
                    if current_param:
                        result[current_param] = " ".join(current_desc)
                    current_param = m.group(1)
                    current_desc = [m.group(2)]
                elif stripped and current_param:
                    current_desc.append(stripped)
        if current_param:
            result[current_param] = " ".join(current_desc)
        return result


# ── ToolRegistry — loading, state, incremental refresh ───────────────────────


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

            module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"

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
