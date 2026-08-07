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
import math
import re
import sys
import threading
import types
import typing
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
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

        type_hints = typing.get_type_hints(func, include_extras=True)

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
            schema_metadata: dict[str, Any] = {}

            is_type_optional = False
            while annotation is not None:
                origin = getattr(annotation, "__origin__", None)
                if typing.get_origin(annotation) is typing.Annotated:
                    annotation, *metadata = typing.get_args(annotation)
                    for item in metadata:
                        if not isinstance(item, dict):
                            raise TypeError(
                                f"Unsupported Annotated metadata: {item!r}. Use JSON Schema constraint dicts."
                            )
                        unsupported = set(item) - {"minLength", "maxLength", "minimum", "maximum", "pattern"}
                        if unsupported:
                            raise TypeError(f"Unsupported JSON Schema constraints: {sorted(map(repr, unsupported))!r}")
                        schema_metadata.update(item)
                elif origin is types.UnionType:
                    args = getattr(annotation, "__args__", ())
                    non_none = [a for a in args if a is not type(None)]
                    if len(non_none) == 1:
                        annotation = non_none[0]
                        is_type_optional = True
                    else:
                        raise TypeError(
                            f"Unsupported union type: {annotation!r}. Use X | None for a single optional type."
                        )
                else:
                    break

            if annotation is not None:
                _type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
                origin = getattr(annotation, "__origin__", None)
                if origin is not None:
                    if origin is typing.Literal:
                        values = list(typing.get_args(annotation))
                        value_types = {type(value) for value in values}
                        if (
                            len(value_types) != 1
                            or not values
                            or value_types.pop() not in _type_map
                            or not cls._is_strict_json(values)
                        ):
                            raise TypeError(
                                f"Unsupported Literal type: {annotation!r}. Use values of one primitive type."
                            )
                        resolved = {"type": _type_map[type(values[0])], "enum": values}
                    elif origin is not list:
                        raise TypeError(f"Unsupported generic type: {annotation!r}. Only list[X] is supported.")
                    else:
                        args = getattr(annotation, "__args__", ())
                        item = args[0] if args else str
                        item_origin = getattr(item, "__origin__", None)
                        if item_origin is dict:
                            dict_args = getattr(item, "__args__", ())
                            key_type, value_type = dict_args if len(dict_args) == 2 else (str, str)
                            if key_type is not str or value_type not in (*_type_map, Any):
                                raise TypeError(
                                    f"Unsupported dict item type: {item!r}. "
                                    "Keys must be str and values must be str, int, float, or bool"
                                )
                            resolved = {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": (
                                        {} if value_type is Any else {"type": _type_map[value_type]}
                                    ),
                                },
                            }
                        elif item_origin is not None or item not in _type_map:
                            raise TypeError(f"Unsupported list item type: {item!r}. Supported: str, int, float, bool")
                        else:
                            resolved = {"type": "array", "items": {"type": _type_map[item]}}
                elif annotation not in _type_map:
                    raise TypeError(
                        f"Unsupported parameter type: {annotation!r}. Supported: str, int, float, bool, list[X]"
                    )
                else:
                    resolved = {"type": _type_map[annotation]}
            else:
                resolved = {"type": "string"}

            cls._validate_schema_metadata(schema_metadata, resolved["type"])
            resolved.update(schema_metadata)
            if param.default is not inspect.Parameter.empty:
                if not cls._is_strict_json(param.default):
                    raise TypeError(f"Parameter '{param_name}' default must be strict JSON: {param.default!r}")
                if not cls._default_conforms(param.default, resolved, nullable=is_type_optional):
                    raise TypeError(
                        f"Parameter '{param_name}' default does not conform to its generated schema: {param.default!r}"
                    )
                resolved["default"] = param.default
            properties[param_name] = resolved | {"description": param_desc.get(param_name, "")}

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return cls(
            name=func.__name__,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _is_strict_json(value: Any) -> bool:
        value_type = type(value)
        if value is None or value_type in (bool, int, str):
            return True
        if value_type is float:
            return math.isfinite(value)
        if value_type is list:
            return all(ToolFunction._is_strict_json(item) for item in value)
        if value_type is dict:
            return all(type(key) is str and ToolFunction._is_strict_json(item) for key, item in value.items())
        return False

    @staticmethod
    def _validate_schema_metadata(metadata: dict[str, Any], schema_type: str) -> None:
        string_constraints = {"minLength", "maxLength", "pattern"}
        numeric_constraints = {"minimum", "maximum"}
        for key, value in metadata.items():
            if key in string_constraints and schema_type != "string":
                raise TypeError(f"JSON Schema constraint '{key}' is not supported for schema type '{schema_type}'")
            if key in numeric_constraints and schema_type not in ("integer", "number"):
                raise TypeError(f"JSON Schema constraint '{key}' is not supported for schema type '{schema_type}'")
            if key in ("minLength", "maxLength") and (type(value) is not int or value < 0):
                raise TypeError(f"Invalid JSON Schema constraint '{key}': expected a nonnegative integer")
            if key == "pattern" and type(value) is not str:
                raise TypeError("Invalid JSON Schema constraint 'pattern': expected a string")
            if key in numeric_constraints:
                valid_type = type(value) is int if schema_type == "integer" else type(value) in (int, float)
                if not valid_type or (type(value) is float and not math.isfinite(value)):
                    expected = "a finite integer" if schema_type == "integer" else "a finite number"
                    raise TypeError(f"Invalid JSON Schema constraint '{key}': expected {expected}")

        if metadata.get("minLength", 0) > metadata.get("maxLength", math.inf):
            raise TypeError("JSON Schema constraint 'minLength' must not exceed 'maxLength'")
        if metadata.get("minimum", -math.inf) > metadata.get("maximum", math.inf):
            raise TypeError("JSON Schema constraint 'minimum' must not exceed 'maximum'")

    @staticmethod
    def _default_conforms(value: Any, schema: dict[str, Any], *, nullable: bool = False) -> bool:
        if value is None:
            return nullable

        schema_type = schema["type"]
        if schema_type == "string":
            conforms = type(value) is str
        elif schema_type == "integer":
            conforms = type(value) is int
        elif schema_type == "number":
            conforms = type(value) in (int, float)
        elif schema_type == "boolean":
            conforms = type(value) is bool
        elif schema_type == "array":
            conforms = type(value) is list and all(
                ToolFunction._default_conforms(item, schema["items"]) for item in value
            )
        else:
            return False

        if not conforms or ("enum" in schema and value not in schema["enum"]):
            return False
        if schema_type == "string":
            return (
                len(value) >= schema.get("minLength", 0)
                and len(value) <= schema.get("maxLength", math.inf)
                and ("pattern" not in schema or re.search(schema["pattern"], value) is not None)
            )
        if schema_type in ("integer", "number"):
            return value >= schema.get("minimum", -math.inf) and value <= schema.get("maximum", math.inf)
        return True

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

    ``module_name`` is the ``sys.modules`` key this file's code was executed
    under. It is kept so a refresh can evict the module it supersedes: the name
    embeds the file's content hash, so every edit mints a *new* key and the old
    one would otherwise stay in ``sys.modules`` for the life of the process.
    """

    file_hash: str
    tools: dict[str, ToolFunction]
    funcs: dict[str, Callable[..., Any]]
    fresh: bool = False
    module_name: str = ""


# ── sys.path handling for helper imports ─────────────────────────────────────


_SYS_PATH_LOCK = threading.Lock()
"""Guards the refcount read-then-modify; held only for that, never for a scan.

A ``threading.Lock``, not ``anyio.Lock``, 刻意为之: acquiring it is not an ``await``,
so the same lock can guard the decrement inside ``finally``. An ``anyio.Lock`` there
would be a cancellation checkpoint — a cancelled scan would skip the removal and leak
the entry for the life of the process (observed, then fixed). It also removes the need
to reason about which mutations the GIL happens to make atomic.
"""

_SYS_PATH_DEPTH: dict[str, int] = {}
"""How many in-flight loads need each ``sys.path`` entry (see ``_tools_dir_on_sys_path``)."""


@asynccontextmanager
async def _tools_dir_on_sys_path(entry: str) -> AsyncIterator[None]:
    """Put *entry* at the front of ``sys.path`` for the duration of loading.

    Tool files import their private helpers by bare module name
    (``from _feishu_impl import ...``). Those helpers are siblings in ``tools/``,
    not installed packages, so the import only resolves if ``tools/`` is on
    ``sys.path``. Before this, it worked by accident: several tool files call
    ``sys.path.insert`` themselves at import time, so whether a *later* file's
    bare import resolved depended on ``glob`` order and on which earlier files
    happened to patch the path. The same file would register on one run and raise
    ``ModuleNotFoundError`` on the next.

    **Refcounted per path, 刻意为之 — both simpler alternatives are wrong, each in
    its own direction (observed, not reasoned about):**

    - Insert unconditionally and always remove → every overlapping loader stacks
      another copy of the entry, so ``sys.path`` grows for the overlap window.
    - Insert only when absent and remove only what you inserted → the loader that
      inserted can finish first and pull the entry out from under a second one that
      is still mid-scan, whose bare ``from _helper import ...`` then raises
      ``ModuleNotFoundError``. This is *worse* than the duplicate.

    One counter keyed by path fixes both: exactly one entry for any number of
    overlapping loaders, removed only when the last leaves. An entry that was
    **already on the path before any load** is borrowed rather than counted — tool
    files insert this very directory themselves at import time, and removing theirs
    would be a different corruption.

    The lock covers only that counter's read-then-modify, never a whole scan: a full
    load is ~2.7s for haitun's 262 tools, so a scan-wide lock would make N Sessions
    starting together queue N x 2.7s. It therefore does **not** give exclusive path
    access — two different agent packages can be on ``sys.path`` at once.

    That is accepted because the real hazards of bare sibling imports are not in
    ``sys.path`` and no lock here can reach them: helpers never reload, same-named
    helpers collide across agent packages, and a tool file named after a stdlib
    module poisons the whole process. The honest boundary is **one agent package per
    process, and no tool file named after a stdlib module** — see
    ``session/AGENTS.md`` § 裸导入的三个已知隐患, where each is spelled out and
    pinned by a test.

    *entry* must already be an absolute path string. Normalizing here would read the
    process CWD — filesystem state, inside an async function, which is what
    ``ASYNC240`` exists to catch. In production it arrives absolute anyway
    (``Session.run`` resolves it via ``await anyio.Path(...).resolve()``).
    """
    with _SYS_PATH_LOCK:
        depth = _SYS_PATH_DEPTH.get(entry, 0)
        if depth == 0 and entry in sys.path:
            # Someone else owns this entry — a tool file that inserted it at import
            # time, or the host process. Borrow it and never remove it.
            owned = False
        else:
            owned = True
            _SYS_PATH_DEPTH[entry] = depth + 1
            if depth == 0:
                sys.path.insert(0, entry)
    try:
        yield
    finally:
        # Same lock on the way out, and still no ``await`` in this cleanup: a
        # cancellation checkpoint here would let a cancelled scan skip the removal
        # and leak the entry for the life of the process (observed, then fixed).
        if owned:
            with _SYS_PATH_LOCK:
                remaining = _SYS_PATH_DEPTH.get(entry, 1) - 1
                if remaining <= 0:
                    _SYS_PATH_DEPTH.pop(entry, None)
                    with suppress(ValueError):
                        sys.path.remove(entry)
                else:
                    _SYS_PATH_DEPTH[entry] = remaining


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
        load_failures: dict[str, str] | None = None,
    ) -> None:
        self._files: dict[str, FileEntry] = dict(files or {})
        self._work_dir = work_dir
        self._session_id = session_id
        self._load_failures: dict[str, str] = dict(load_failures or {})

    @property
    def tools(self) -> dict[str, ToolFunction]:
        """Flat dict of all tool metadata (name → ToolFunction)."""
        result: dict[str, ToolFunction] = {}
        for entry in self._files.values():
            result.update(entry.tools)
        return result

    @property
    def load_failures(self) -> dict[str, str]:
        """File name → why it failed to import, for files that registered nothing.

        Kept so the startup exposure check can tell "this tool is missing because
        its dependency isn't installed" apart from "this name never existed".
        """
        return dict(self._load_failures)

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
        failures: dict[str, str] = {}
        files = await cls._load_from_dir(tools_dir, session_id, load_failures=failures)
        return cls(files=files, work_dir=tools_dir, session_id=session_id, load_failures=failures)

    async def refresh(self) -> dict[str, str]:
        """Incremental reload — adds, updates, removes tools.

        Returns a dict mapping tool name to ``'added'``, ``'updated'``,
        ``'removed'``, or ``'skipped'``.  Errors are caught and logged;
        the caller always gets a dict back (empty on failure).
        """
        try:
            return await self._do_refresh()
        except Exception:
            logger.warning("Failed to refresh tools")
            return {}

    async def _do_refresh(self) -> dict[str, str]:
        if self._work_dir is None:
            logger.warning("No work_dir set, cannot refresh tools")
            return {}

        logger.debug("Starting tool refresh")
        failures: dict[str, str] = {}
        new_files = await self._load_from_dir(self._work_dir, self._session_id, self._files, load_failures=failures)
        self._load_failures = failures
        result: dict[str, str] = {}

        # removed — files in old but not on disk any more
        for path in list(self._files):
            if path not in new_files:
                for name in self._files[path].tools:
                    result[name] = "removed"
                self._evict_module(self._files.pop(path))

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
                self._evict_module(old_entry)

        logger.info(f"Tool refresh complete: {result or 'no changes'}")
        return result

    @staticmethod
    def _evict_module(superseded: FileEntry) -> None:
        """Drop a superseded tool module from ``sys.modules``.

        The module name embeds the file's content hash, so each edit mints a new
        key; without this, every refresh left the previous module resident for the
        life of the process (measured: one file edited six times leaves seven dead
        modules, and it grows without bound in a long-lived Gateway).

        Only *tool* modules are evicted, 刻意为之. Helpers are cached under their
        bare name and are deliberately not touched — a live tool that imported one
        still holds a reference to it, and dropping the ``sys.modules`` entry would
        only cause the next importer to build a second, divergent copy. That helpers
        do not reload is a documented limitation (see ``session/AGENTS.md``), not
        something this eviction is trying to fix.
        """
        if superseded.module_name:
            sys.modules.pop(superseded.module_name, None)

    # -- internals -------------------------------------------------------------

    @staticmethod
    async def _load_from_dir(
        tools_dir: Path,
        session_id: str,
        old_files: dict[str, FileEntry] | None = None,
        *,
        load_failures: dict[str, str] | None = None,
    ) -> dict[str, FileEntry]:
        """Scan and import all tool ``.py`` files.

        If *old_files* is provided, files whose hash matches the stored
        value are preserved (copied from *old_files* with ``fresh=False``)
        instead of re-imported.

        *load_failures*, when given, is filled with ``file name → error text``
        for every file that raised while importing.

        Returns ``{file_path: FileEntry}`` for all current ``.py`` files.
        """
        files: dict[str, FileEntry] = {}
        registered_modules: list[str] = []
        tools_anyio = anyio.Path(str(tools_dir))

        try:
            tools_dir_exists = await tools_anyio.is_dir()
            # Normalize here, not inside the sys.path helper: this reads the
            # filesystem, and anyio.Path keeps that off the event loop thread.
            # ``resolve()`` rather than ``absolute()`` because the string is a
            # refcount key — the same directory reached via a symlink, a ``.``
            # segment, or different case would otherwise get its own entry and its
            # own counter, putting two spellings of one directory on ``sys.path``.
            sys_path_entry = str(await tools_anyio.resolve())
        except Exception as e:
            logger.warning(f"Cannot access tools directory {tools_dir!r}: {e!r}")
            return files
        if not tools_dir_exists:
            logger.warning(f"Tools directory not found: {tools_dir!r}")
            return files

        try:
            # ``tools/`` on sys.path for the whole scan: tool files import their
            # siblings by bare name, and that must not depend on glob order.
            async with _tools_dir_on_sys_path(sys_path_entry):
                async for py_file in tools_anyio.glob("*.py"):
                    if py_file.name.startswith("_"):
                        continue

                    module_name = None
                    try:
                        file_bytes = await py_file.read_bytes()
                        file_hash = hashlib.sha256(file_bytes).hexdigest()
                        str_path = str(py_file)

                        if (
                            old_files is not None
                            and str_path in old_files
                            and old_files[str_path].file_hash == file_hash
                        ):
                            logger.debug(f"Skipping unchanged file: {py_file!r}")
                            old = old_files[str_path]
                            # Carry module_name across: it is what a later refresh
                            # evicts, so dropping it here would strand the module.
                            files[str_path] = FileEntry(
                                file_hash=old.file_hash,
                                tools=old.tools,
                                funcs=old.funcs,
                                fresh=False,
                                module_name=old.module_name,
                            )
                            continue

                        module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"

                        # Decode the bytes already read for the hash rather than
                        # re-reading: one less IO, and it guarantees the compiled
                        # source is the exact revision `file_hash` and `module_name`
                        # were derived from even if the file changes mid-scan.
                        compiled = compile(file_bytes.decode("utf-8"), str_path, "exec")

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
                            module_name=module_name,
                        )
                    except Exception as e:
                        if module_name is not None:
                            sys.modules.pop(module_name, None)
                            with suppress(ValueError):
                                registered_modules.remove(module_name)
                        logger.error(f"Failed to load tool file {py_file!r}: {e!r}")
                        if load_failures is not None:
                            load_failures[py_file.name] = repr(e)
                        continue
        except BaseException:
            for mn in registered_modules:
                sys.modules.pop(mn, None)
            raise

        total_tools = sum(len(entry.tools) for entry in files.values())
        logger.info(f"Loaded {total_tools} tool(s) from {len(files)} file(s) in {tools_dir!r}")
        return files
