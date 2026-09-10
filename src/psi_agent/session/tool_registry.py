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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.session.content_roots import ContentRoot, content_roots_from_env
from psi_agent.session.tool_exposure import read_manifest
from psi_agent.session.tool_layers import Layer, executing_tool_file, layers_open

# ── tools-dir import scope ───────────────────────────────────────────────────

# Tool files import their same-directory private helpers by bare name
# (``import _runtime_paths``), so the tools dir has to be on ``sys.path``
# while a file is exec'd.  Nested and concurrent loads of the same dir are
# refcounted so the last one out removes the entry — otherwise a leaked
# entry silently resolves another workspace's identically-named private
# module (``_assignment_tool_common`` exists in two workspaces with
# different contents).
_path_scope_depth: dict[str, int] = {}
_path_scope_lock = threading.Lock()


@contextmanager
def _tools_dir_on_sys_path(tools_dir: Path) -> Iterator[None]:
    """Put *tools_dir* at the front of ``sys.path`` for the duration.

    Reentrant and refcounted: the entry is inserted by the outermost
    scope and removed by it, and only if this scope actually added it.
    Pre-existing entries (a caller already put it there) are left alone.
    """
    entry = str(tools_dir)
    with _path_scope_lock:
        depth = _path_scope_depth.get(entry, 0)
        inserted = False
        if depth == 0 and entry not in sys.path:
            sys.path.insert(0, entry)
            inserted = True
        _path_scope_depth[entry] = depth + 1
    if inserted:
        _restore_private_modules(entry)
    try:
        yield
    finally:
        with _path_scope_lock:
            remaining = _path_scope_depth.get(entry, 1) - 1
            if remaining <= 0:
                _path_scope_depth.pop(entry, None)
                if inserted:
                    with suppress(ValueError):
                        sys.path.remove(entry)
            else:
                _path_scope_depth[entry] = remaining
        if inserted:
            _stash_private_modules(entry)


# Bare-name private modules (``_assignment_tool_common``) share one global
# ``sys.modules`` slot across every tools dir, and two workspaces ship files
# with that same name but different contents.  So each dir's private modules
# are stashed out of ``sys.modules`` when its scope ends and restored when it
# reopens: workspaces stay isolated, while module-level singletons (e.g.
# ``_background_process_registry``'s live-process table) survive refreshes
# instead of being rebuilt per load.
_private_module_stash: dict[str, dict[str, types.ModuleType]] = {}


def _restore_private_modules(entry: str) -> None:
    """Put this dir's previously stashed private modules back in ``sys.modules``."""
    for name, mod in _private_module_stash.pop(entry, {}).items():
        sys.modules.setdefault(name, mod)


def _stash_private_modules(entry: str) -> None:
    """Move private modules loaded from *entry* out of ``sys.modules``.

    Submodules of a private package (``_feishu.auth``) are stashed too, keyed by
    their own dotted name.  They used to be skipped outright, which left
    ``agents/feishu/tools/_feishu/``'s 15 modules in ``sys.modules`` after every
    scope closed — so a second tools dir shipping its own ``_feishu`` bound the
    first dir's submodules even when the two dirs were loaded *sequentially*,
    with no layering involved.  A dotted name is matched against the package
    directory rather than the tools dir, since that is where the file sits.
    """
    try:
        resolved = Path(entry).resolve()
    except OSError:
        return
    stash: dict[str, types.ModuleType] = {}
    for name in list(sys.modules):
        if not name.startswith("_"):
            continue
        mod = sys.modules.get(name)
        origin = getattr(mod, "__file__", None)
        if mod is None or not origin:
            continue
        try:
            if not _belongs_to_dir(name, Path(origin), resolved):
                continue
        except OSError:
            continue
        stash[name] = mod
        del sys.modules[name]
    if stash:
        _private_module_stash[entry] = stash


def _belongs_to_dir(name: str, origin: Path, tools_dir: Path) -> bool:
    """Whether the module *name* loaded from *origin* is *tools_dir*'s own.

    ``_helper`` (bare) must sit directly in the dir.  ``_pkg.sub`` sits one
    directory down per name segment, and ``_pkg`` itself is a directory with an
    ``__init__.py``, so the expected parent is rebuilt from the name rather than
    compared to *tools_dir* directly.  Rebuilding it — instead of just asking
    whether the file is somewhere under *tools_dir* — keeps a module that merely
    happens to live in a subdirectory from being claimed under the wrong name.
    """
    resolved = origin.resolve()
    *parents, last = name.split(".")
    expected = tools_dir
    for part in parents:
        expected = expected / part
    if resolved.name == "__init__.py":
        return resolved.parent == (expected / last)
    return resolved.parent == expected


# ── process-wide compiled-module cache ───────────────────────────────────────

# ``ToolRegistry.load()`` builds a fresh instance and passes ``old_files=None``,
# so the per-instance hash comparison in ``_exec_tool_files`` can only ever fire
# on the ``refresh()`` path — every new session re-compiled and re-exec'd every
# tool file from scratch.  This cache lifts that reuse to the process: same
# bytes in the same layer means the already-exec'd module object is handed out
# again, with no ``compile`` and no second round of module-level side effects.
#
# The key must carry a layer identifier as well as the hash.  Byte-identical
# files live in different tools dirs (two workspaces ship the same file with a
# different same-directory ``_priv_helper``), and a hash-only key would hand the
# second dir the first dir's module — including its helper bindings.
#
# Which identifier decides how much reuse is reachable.  A per-*mount* id (the
# resolved tools-dir path, what this used to be) makes one shipped layer look
# like N layers when N workspaces mount it, so the shared content is compiled N
# times.  A declared **content root** name is per-*content*, so those N mounts
# hit one entry — while two differently-named roots stay as separate as two paths
# were.  See ``content_roots``.
#
# Entries are keyed by content hash and never evicted, so editing a tool file
# leaves the old module behind.  That matches what ``sys.modules`` already does
# with these per-hash module names; a bounded cache would be a separate change.
_module_cache: dict[tuple[str, str], types.ModuleType] = {}
_module_cache_lock = threading.Lock()


def _cache_key(layer_id: str, file_hash: str) -> tuple[str, str]:
    """The one place the reuse key is formed.

    A named function rather than a tuple literal at each of the three touch
    points, because the key *is* the isolation boundary: dropping ``layer_id``
    from it would hand one layer another layer's module, and that has to be one
    reviewable line instead of a shape repeated where it can drift apart.
    """
    return (layer_id, file_hash)


def _layer_id(tools_dir: Path, roots: Sequence[ContentRoot] | None = None) -> str:
    """Cache-key component identifying the layer *tools_dir* belongs to.

    A **declared content root** owning this dir supplies the id, so the same
    shipped content mounted into several workspaces is one layer and compiles
    once (see ``content_roots``).  *roots* defaults to whatever the deployment
    declared in ``PSI_CONTENT_ROOTS``.

    With no root claiming the dir the resolved path stands in, which is the
    single-root behaviour: one dir, one layer, and the path is the only identity
    available.  Falling back to the path rather than to a shared constant keeps
    the isolation the key exists for — two unclaimed dirs stay distinct, so a
    byte-identical file in each still gets its own module and its own
    ``_priv_helper`` bindings.
    """
    declared = content_roots_from_env() if roots is None else roots
    if declared:
        try:
            resolved = tools_dir.resolve()
        except OSError:
            resolved = tools_dir
        for root in declared:
            try:
                if root.tools_dir.resolve() == resolved:
                    return root.layer_id
            except OSError:
                continue
    try:
        return str(tools_dir.resolve())
    except OSError:
        return str(tools_dir)


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

    ``layer_id`` records which content layer the file was loaded under. Kept per
    file rather than recomputed from the path because the id is a declared name
    under content roots, not something a path can be read back into
    (``content_roots``), and because exposure decisions are per layer
    (``tool_exposure``) — a tool that cannot say whose it is cannot be ranked.
    """

    file_hash: str
    tools: dict[str, ToolFunction]
    funcs: dict[str, Callable[..., Any]]
    fresh: bool = False
    layer_id: str = ""


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
        manifests: Mapping[str, frozenset[str] | None] | None = None,
    ) -> None:
        self._files: dict[str, FileEntry] = dict(files or {})
        self._work_dir = work_dir
        self._session_id = session_id
        self._manifests: dict[str, frozenset[str] | None] = dict(manifests or {})

    @property
    def tools(self) -> dict[str, ToolFunction]:
        """Flat dict of all tool metadata (name → ToolFunction)."""
        result: dict[str, ToolFunction] = {}
        for entry in self._files.values():
            result.update(entry.tools)
        return result

    @property
    def exposure_manifests(self) -> dict[str, frozenset[str] | None]:
        """Per-layer declared exposure: ``layer_id`` → names, or ``None`` if undeclared.

        Read once per load rather than per turn: the manifest is content shipped
        with the layer, and re-reading it every turn would let the ``tools`` array
        move mid-Session, which is the drift ``ToolDefsCache`` exists to stop.
        """
        return dict(self._manifests)

    @property
    def layer_of_tool(self) -> dict[str, str]:
        """Tool name → the ``layer_id`` whose file currently owns that name.

        Follows the same last-wins direction as ``tools``: when two layers ship
        one name, the answer is the layer ``tools`` and ``get()`` both resolve to,
        so an exposure decision cannot be made against a definition the model
        will never be handed.
        """
        result: dict[str, str] = {}
        for entry in self._files.values():
            for name in entry.tools:
                result[name] = entry.layer_id
        return result

    def get(self, name: str) -> Callable[..., Any] | None:
        """Return the callable for *name*, or None if not registered.

        Files are searched in reverse insertion order so a duplicate name
        resolves to the same file ``tools`` resolves it to — that property
        overwrites per file, so the last-loaded one wins there.  Searching
        forwards would run the first file's body while advertising the
        last file's description and schema (content layering derives
        personal tools from official ones by reusing the name, so the two
        directions have to agree).  Names unique to an earlier file are
        still found; only the winner of a collision changes.
        """
        for entry in reversed(self._files.values()):
            func = entry.funcs.get(name)
            if func is not None:
                return func
        return None

    # -- loading ---------------------------------------------------------------

    @classmethod
    async def load(cls, tools_dir: Path, session_id: str = "") -> ToolRegistry:
        """Full initial load — scan *tools_dir* and import everything.

        The layer id comes from whichever declared content root owns *tools_dir*,
        so a root mounted into many workspaces is compiled once; with none
        declared it is the dir's own path (see ``_layer_id``).
        """
        files = await cls._load_from_dir(tools_dir, session_id)
        layer_id = _layer_id(tools_dir)
        return cls(
            files=files,
            work_dir=tools_dir,
            session_id=session_id,
            manifests={layer_id: await read_manifest(tools_dir)},
        )

    @classmethod
    async def load_content_roots(cls, roots: Sequence[ContentRoot], session_id: str = "") -> ToolRegistry:
        """Full initial load across declared content roots.

        The layering entry point callers reach for: each root carries its own
        identity and rank, so this is ``load_layers`` over ``root.as_layer()``.
        Roots whose ``tools_dir`` is absent are skipped — a deployment declares
        the mount layout once and not every root has tools (the enterprise tier
        ships skills only), so a missing dir is a normal shape rather than a
        misconfiguration to refuse on.
        """
        present: list[Layer] = []
        for root in roots:
            if await anyio.Path(str(root.tools_dir)).is_dir():
                present.append(root.as_layer())
            else:
                logger.debug(f"Content root {root.name!r} has no tools dir at {root.tools_dir}")
        return await cls.load_layers(present, session_id)

    @classmethod
    async def load_layers(cls, layers: Sequence[Layer], session_id: str = "") -> ToolRegistry:
        """Full initial load across several content layers held open together.

        Every layer's tools dir is in scope for the whole load — that is what
        makes cross-layer derivation possible at all — so bare-name private
        helpers can no longer be told apart by ``sys.path`` position.  A
        ``LayerImportHook`` resolves them per layer instead; see ``tool_layers``.

        Layers are exec'd in *ascending* priority so the highest-priority layer
        is inserted last.  That makes the flat ``tools`` dict's last-wins agree
        with ``get()``'s reverse search, and both agree with the ladder: a
        personal tool overrides the official tool of the same name.  Private
        helper resolution does **not** follow that rule — each layer keeps its
        own (see ``LayerImportHook._search_order``).

        ``refresh()`` on the result reloads only the highest-priority layer, the
        one a user can edit; ``work_dir`` is set to its tools dir.
        """
        ordered = sorted(layers, key=lambda layer: layer.priority)
        files: dict[str, FileEntry] = {}
        with layers_open(ordered):
            for layer in ordered:
                files.update(await cls._load_from_dir(layer.tools_dir, session_id, layer_id=layer.layer_id))
        # Read outside the hook: a manifest is a text file, so nothing here
        # imports, and holding the hook open longer than the loads need it only
        # widens the window in which ``sys.meta_path`` carries this load's finder.
        manifests = {layer.layer_id: await read_manifest(layer.tools_dir) for layer in ordered}
        top = ordered[-1].tools_dir if ordered else None
        return cls(files=files, work_dir=top, session_id=session_id, manifests=manifests)

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
        *,
        layer_id: str | None = None,
    ) -> dict[str, FileEntry]:
        """Scan and import all tool ``.py`` files.

        If *old_files* is provided, files whose hash matches the stored
        value are preserved (copied from *old_files* with ``fresh=False``)
        instead of re-imported.

        Returns ``{file_path: FileEntry}`` for all current ``.py`` files.

        *tools_dir* is on ``sys.path`` for the whole scan so every file
        resolves its same-directory private helpers, regardless of the
        process cwd or which file happens to be scanned first.

        *layer_id* names the content layer this dir belongs to, for the import
        hook installed by ``load_layers`` and for the compiled-module cache key.
        Defaults to ``_layer_id()``, which asks the declared content roots first
        and falls back to the dir's own path — what the single-dir path wants: it
        is then the sole layer, and asking on its behalf resolves to itself.
        """
        files: dict[str, FileEntry] = {}
        registered_modules: list[str] = []
        tools_anyio = anyio.Path(str(tools_dir))

        try:
            tools_dir_exists = await tools_anyio.is_dir()
        except Exception as e:
            logger.warning(f"Cannot access tools directory {tools_dir!r}: {e!r}")
            return files
        if not tools_dir_exists:
            logger.warning(f"Tools directory not found: {tools_dir!r}")
            return files

        with _tools_dir_on_sys_path(tools_dir):
            return await ToolRegistry._exec_tool_files(
                tools_anyio,
                tools_dir,
                session_id,
                old_files,
                files,
                registered_modules,
                layer_id or _layer_id(tools_dir),
            )

    @staticmethod
    async def _exec_tool_files(
        tools_anyio: anyio.Path,
        tools_dir: Path,
        session_id: str,
        old_files: dict[str, FileEntry] | None,
        files: dict[str, FileEntry],
        registered_modules: list[str],
        layer_id: str,
    ) -> dict[str, FileEntry]:
        """Compile and exec each tool file; caller owns the ``sys.path`` scope."""

        # ``glob`` yields in filesystem order, which differs across platforms and
        # filesystems and is not stable under renames.  Load order is a hidden
        # input to more than aesthetics: files that carry their own ``sys.path``
        # preamble have to precede the files relying on it (59 tool files once
        # failed to load because they did not), and the first file to import a
        # dotted private helper binds the submodule as an attribute the later
        # ones read instead of resolving.  Sorting by name makes those relative
        # positions the same everywhere.  Measured on both platforms by
        # ``scripts/check_tool_glob_order.py``: NTFS misplaces 16 of feishu's
        # 134 names, ext4 misplaces all 134 (4370 inverted pairs) because it
        # returns htree hash order.  Sort key is the plain file name, so
        # this is the in-layer order once each content layer globs its own dir —
        # layer precedence orders the layers, this orders the files within one.
        try:
            py_files = sorted([path async for path in tools_anyio.glob("*.py")], key=lambda path: path.name)

            for py_file in py_files:
                if py_file.name.startswith("_"):
                    continue

                module_name = None
                compiled_here = False
                try:
                    file_bytes = await py_file.read_bytes()
                    file_hash = hashlib.sha256(file_bytes).hexdigest()
                    str_path = str(py_file)

                    if old_files is not None and str_path in old_files and old_files[str_path].file_hash == file_hash:
                        logger.debug(f"Skipping unchanged file: {py_file!r}")
                        old = old_files[str_path]
                        files[str_path] = FileEntry(
                            file_hash=old.file_hash,
                            tools=old.tools,
                            funcs=old.funcs,
                            fresh=False,
                            layer_id=layer_id,
                        )
                        continue

                    with _module_cache_lock:
                        cached = _module_cache.get(_cache_key(layer_id, file_hash))

                    if cached is not None:
                        # Same bytes, same layer: reuse the module that was already
                        # exec'd.  Its functions carry the *original* load's
                        # ``__module__``, so the ownership filter below has to match
                        # against that name rather than one built from this session.
                        module = cached
                        module_name = module.__name__
                        sys.modules.setdefault(module_name, module)
                        logger.debug(f"Reusing compiled module for {py_file!r}")
                    else:
                        module_name = f"psi_tool_{py_file.stem}_{session_id}_{file_hash}"

                        source = await py_file.read_text(encoding="utf-8")
                        compiled = compile(source, str_path, "exec")

                        module = types.ModuleType(module_name)
                        module.__file__ = str_path
                        sys.modules[module_name] = module
                        registered_modules.append(module_name)
                        compiled_here = True

                        # ``executing_tool_file`` pins this layer as the asker for
                        # the file's imports and releases the plain-name aliases
                        # they took once it finishes — per file, so the next file
                        # cannot inherit this one's bindings.  A no-op when no
                        # layer hook is installed.
                        with executing_tool_file(layer_id):
                            exec(compiled, module.__dict__)

                        with _module_cache_lock:
                            _module_cache.setdefault(_cache_key(layer_id, file_hash), module)

                    attr_names = sorted(name for name in dir(module) if not name.startswith("_"))
                    tools: dict[str, ToolFunction] = {}
                    funcs: dict[str, Callable[..., Any]] = {}

                    for name in attr_names:
                        func = getattr(module, name, None)
                        if not inspect.iscoroutinefunction(func):
                            continue

                        if getattr(func, "__module__", None) != module_name:
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
                        layer_id=layer_id,
                    )
                except Exception as e:
                    # Only unregister a module this iteration created.  A reused
                    # cached module belongs to an earlier load that is still using
                    # it, so evicting it here would break that registry.
                    if module_name is not None and compiled_here:
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
