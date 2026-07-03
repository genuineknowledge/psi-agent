"""Tool loading and incremental refresh from ``workspace/tools/``.

``ToolRegistry`` loads async Python functions from ``workspace/tools/``
via ``compile`` + ``exec`` (not ``importlib``, to avoid bytecode-cache
staleness on refresh), converts signatures to JSON Schema via
``ToolFunction.from_callable()``, tracks SHA-256 file hashes for
incremental refresh, and provides ``get(name)`` for tool execution
lookup.

Tools are stored per-file internally via ``FileEntry``, which carries
the hash, tool metadata, and callables for a single ``.py`` file.
The public ``tools`` dict and ``get()`` remain flat for backward
compatibility.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import sys
import types
import typing
from collections.abc import Callable
from contextlib import suppress
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


# ── FileEntry — per-file storage unit ─────────────────────────────────────────


@dataclass
class FileEntry:
    """Per-file tool storage — hash, metadata, callables, and import status.

    ``fresh`` is ``True`` when the file was actually imported during
    this refresh round; ``False`` when the entry was copied from a
    previous state (hash matched, file skipped).
    """

    file_hash: str
    tools: dict[str, ToolFunction]
    funcs: dict[str, Callable[..., Any]]
    fresh: bool = False


# ── ToolRegistry — loading, state, incremental refresh ───────────────────────


class ToolRegistry:
    """Owns tool metadata and callables per file, loaded from ``workspace/tools/``.

    ``tools`` (property) and ``get(name)`` provide the flat public
    interface for backward compatibility.  Internally tools are stored
    as ``{file_path: FileEntry}`` via ``_files``.
    """

    def __init__(
        self,
        *,
        files: dict[str, FileEntry] | None = None,
        work_dir: Path | None = None,
        session_id: str = "",
    ) -> None:
        self._files: dict[str, FileEntry] = dict(files or {})
        self._work_dir = work_dir
        self._session_id = session_id

    @property
    def tools(self) -> dict[str, ToolFunction]:
        """Flat dict of all tool metadata (name → ToolFunction)."""
        result: dict[str, ToolFunction] = {}
        for entry in self._files.values():
            result.update(entry.tools)
        return result

    def get(self, name: str) -> Callable[..., Any] | None:
        """Return the callable for *name*, or None if not registered."""
        for entry in self._files.values():
            func = entry.funcs.get(name)
            if func is not None:
                return func
        return None

    # -- loading ---------------------------------------------------------------

    @classmethod
    async def load(cls, tools_dir: Path, session_id: str = "") -> ToolRegistry:
        """Full initial load — scan *tools_dir* and import everything."""
        files = await cls._load_from_dir(tools_dir, session_id)
        return cls(files=files, work_dir=tools_dir, session_id=session_id)

    async def refresh(self) -> dict[str, str]:
        """Incremental reload — adds, updates, removes tools.

        Returns a dict mapping tool name to ``'added'``, ``'updated'``,
        ``'removed'``, or ``'skipped'``.
        """
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh tools")
            return {}

        logger.debug("Starting tool refresh")
        new_files = await self._load_from_dir(self._work_dir, self._session_id, self._files)
        result: dict[str, str] = {}

        # removed — files in old but not on disk any more
        for path in list(self._files):
            if path not in new_files:
                for name in self._files[path].tools:
                    result[name] = "removed"
                del self._files[path]

        # added / updated / skipped — per file
        for path, new_entry in new_files.items():
            old_entry = self._files.get(path)
            if old_entry is None:
                for name in new_entry.tools:
                    result[name] = "added"
                self._files[path] = new_entry
            elif not new_entry.fresh:
                for name in old_entry.tools:
                    result[name] = "skipped"
            else:
                for name in old_entry.tools:
                    if name not in new_entry.tools:
                        result[name] = "removed"
                for name in new_entry.tools:
                    if name not in old_entry.tools:
                        result[name] = "added"
                    else:
                        result[name] = "updated"
                self._files[path] = new_entry

        logger.info(f"Tool refresh complete: {result or 'no changes'}")
        return result

    # -- internals -------------------------------------------------------------

    @staticmethod
    async def _load_from_dir(
        tools_dir: Path,
        session_id: str,
        old_files: dict[str, FileEntry] | None = None,
    ) -> dict[str, FileEntry]:
        """Scan and import all tool ``.py`` files.

        If *old_files* is provided, files whose hash matches the stored
        value are preserved (copied from *old_files* with ``fresh=False``)
        instead of re-imported.

        Returns ``{file_path: FileEntry}`` for all current ``.py`` files.
        """
        files: dict[str, FileEntry] = {}
        registered_modules: list[str] = []
        tools_anyio = anyio.Path(str(tools_dir))

        if not await tools_anyio.is_dir():
            logger.warning(f"Tools directory not found: {tools_dir!r}")
            return files

        try:
            async for py_file in tools_anyio.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue

                module_name = None
                try:
                    file_bytes = await py_file.read_bytes()
                    file_hash = hashlib.sha256(file_bytes).hexdigest()
                    str_path = str(py_file)

                    if old_files is not None and str_path in old_files and old_files[str_path].file_hash == file_hash:
                        logger.debug(f"Skipping unchanged file: {py_file!r}")
                        old = old_files[str_path]
                        files[str_path] = FileEntry(
                            file_hash=old.file_hash, tools=old.tools, funcs=old.funcs, fresh=False
                        )
                        continue

                    module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"

                    source = await py_file.read_text(encoding="utf-8")
                    compiled = compile(source, str_path, "exec")

                    module = types.ModuleType(module_name)
                    module.__file__ = str_path
                    sys.modules[module_name] = module
                    registered_modules.append(module_name)

                    exec(compiled, module.__dict__)

                    attr_names = sorted(name for name in dir(module) if not name.startswith("_"))
                    tools: dict[str, ToolFunction] = {}
                    funcs: dict[str, Callable[..., Any]] = {}

                    for name in attr_names:
                        func = getattr(module, name, None)
                        if not inspect.iscoroutinefunction(func):
                            continue

                        try:
                            tool_func = ToolFunction.from_callable(func)
                        except Exception as e:
                            logger.error(f"Skipping tool {name!r} in {py_file!r}: {e!r}")
                            continue

                        tools[name] = tool_func
                        funcs[name] = func
                        logger.debug(f"Loaded tool: {name!r} from {py_file!r}")

                    files[str_path] = FileEntry(
                        file_hash=file_hash,
                        tools=tools,
                        funcs=funcs,
                        fresh=True,
                    )
                except Exception as e:
                    if module_name is not None:
                        sys.modules.pop(module_name, None)
                        with suppress(ValueError):
                            registered_modules.remove(module_name)
                    logger.error(f"Failed to load tool file {py_file!r}: {e!r}")
                    continue
        except BaseException:
            for mn in registered_modules:
                sys.modules.pop(mn, None)
            raise

        total_tools = sum(len(entry.tools) for entry in files.values())
        logger.info(f"Loaded {total_tools} tool(s) from {len(files)} file(s) in {tools_dir!r}")
        return files
