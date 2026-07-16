"""Static tool index — scan ``tools/*.py`` and extract tool definitions via AST.

Shared implementation for the ``tool_search`` / ``tool_search_code`` /
``tool_describe`` meta-tools. It answers "what tools exist and what do they do"
by parsing tool source files with the standard-library ``ast`` module — it never
imports or executes them. Executing a tool module can trigger import-time side
effects (e.g. ``_mcp`` connecting to a Playwright MCP server, blocking for tens
of seconds, or crashing the gateway), so discovery must stay purely static.

A tool is a top-level ``async def`` whose name does not start with ``_``, living
in a ``*.py`` file whose name does not start with ``_`` — matching the loading
rules in ``ToolRegistry._load_from_dir``.

All file IO uses ``anyio.Path``; ``ast.parse`` is pure CPU work with no IO.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import anyio


def tools_dir() -> anyio.Path:
    """Return the ``tools/`` directory these tools live in (this file's parent)."""
    # Path(__file__).parent is pure path arithmetic, not IO — allowed under the
    # "no pathlib for IO" rule. anyio.Path is used for the actual IO below.
    return anyio.Path(str(Path(__file__).resolve().parent))


@dataclass
class ToolMeta:
    """One discovered tool: its name, source file, signature and docstring."""

    name: str
    file: str
    signature: str
    summary: str
    description: str
    docstring: str


# ── docstring parsing ────────────────────────────────────────────────────────

_SECTION_HEADERS = ("Args:", "Returns:", "Yields:", "Raises:")


def _parse_description(doc: str) -> str:
    """Everything before the first ``Args:``/``Returns:``/``Yields:``/``Raises:``.

    Mirrors ``ToolFunction._parse_description`` so descriptions line up with what
    the LLM actually sees, but implemented independently of the framework.
    """
    lines: list[str] = []
    for line in doc.strip().split("\n"):
        stripped = line.strip()
        if any(stripped.startswith(h) for h in _SECTION_HEADERS):
            break
        if stripped:
            lines.append(stripped)
    return " ".join(lines)


def _summary(docstring: str) -> str:
    """First non-empty line of the docstring, capped for compact list display."""
    for line in docstring.strip().split("\n"):
        stripped = line.strip()
        if stripped:
            if len(stripped) > 200:
                stripped = stripped[:197].rstrip() + "..."
            return stripped
    return ""


# ── signature rendering ──────────────────────────────────────────────────────


def _render_signature(name: str, func: ast.AsyncFunctionDef) -> str:
    """Rebuild a human-readable ``name(param: type = default, ...)`` from AST."""
    args = func.args
    parts: list[str] = []

    positional = args.posonlyargs + args.args
    # Defaults align to the tail of the positional parameters.
    default_offset = len(positional) - len(args.defaults)
    for i, arg in enumerate(positional):
        parts.append(_render_arg(arg, args.defaults, i - default_offset))

    if args.vararg is not None:
        parts.append("*" + _render_arg(args.vararg, [], -1))
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        rendered = _render_arg(arg, [], -1)
        if default is not None:
            rendered += f" = {ast.unparse(default)}"
        parts.append(rendered)

    if args.kwarg is not None:
        parts.append("**" + _render_arg(args.kwarg, [], -1))

    return f"{name}({', '.join(parts)})"


def _render_arg(arg: ast.arg, defaults: list[ast.expr], default_idx: int) -> str:
    """Render one ``arg`` as ``name: annotation = default`` (parts optional)."""
    rendered = arg.arg
    if arg.annotation is not None:
        rendered += f": {ast.unparse(arg.annotation)}"
    if 0 <= default_idx < len(defaults):
        rendered += f" = {ast.unparse(defaults[default_idx])}"
    return rendered


# ── scanning ─────────────────────────────────────────────────────────────────


async def _iter_tool_files(directory: anyio.Path):
    """Yield ``*.py`` tool files (skipping ``_``-prefixed), name-sorted."""
    try:
        if not await directory.is_dir():
            return
    except OSError:
        return
    paths = [p async for p in directory.glob("*.py") if not p.name.startswith("_")]
    for path in sorted(paths, key=lambda p: p.name):
        yield path


def _extract_metas(source: str, file_name: str) -> list[ToolMeta]:
    """Parse *source* and return one ToolMeta per top-level public async def."""
    tree = ast.parse(source)
    metas: list[ToolMeta] = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        docstring = ast.get_docstring(node) or ""
        description = _parse_description(docstring)
        metas.append(
            ToolMeta(
                name=node.name,
                file=file_name,
                signature=_render_signature(node.name, node),
                summary=_summary(docstring),
                description=description,
                docstring=docstring,
            )
        )
    return metas


async def index_tools(directory: anyio.Path | None = None) -> list[ToolMeta]:
    """Index every tool in *directory* (defaults to this ``tools/`` dir).

    Files that cannot be read or parsed are skipped silently — a single broken
    tool file must not break discovery of the rest. Results are sorted by tool
    name for stable output.
    """
    directory = directory or tools_dir()
    metas: list[ToolMeta] = []
    async for path in _iter_tool_files(directory):
        try:
            source = await path.read_text(encoding="utf-8")
            metas.extend(_extract_metas(source, path.name))
        except OSError, SyntaxError, ValueError, UnicodeDecodeError:
            continue
    metas.sort(key=lambda m: m.name)
    return metas
