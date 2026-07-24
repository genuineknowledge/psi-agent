"""Tests for the Haitun workspace tool-discovery meta-tools.

Covers ``_tool_index`` (static AST scan) and the ``tool_search`` /
``tool_search_code`` / ``tool_describe`` tools built on top of it.
"""

from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path
from typing import Any

import anyio

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_idx: Any = importlib.import_module("_tool_index")
tool_search: Any = importlib.import_module("tool_search").tool_search
tool_search_code: Any = importlib.import_module("tool_search_code").tool_search_code
tool_describe: Any = importlib.import_module("tool_describe").tool_describe


# ── _tool_index against the real tools/ dir ──────────────────────────────────


async def test_index_finds_known_tools_and_skips_private_files():
    metas = await _idx.index_tools()
    names = {m.name for m in metas}
    # Known public tools are indexed.
    assert "find_files" in names
    assert "fetch" in names
    # The three discovery tools index themselves.
    assert {"tool_search", "tool_search_code", "tool_describe"} <= names
    # Private helper files (``_fetch_impl.py``) never expose a tool.
    assert "fetch_impl" not in names
    assert all(not n.startswith("_") for n in names)


async def test_index_does_not_execute_tool_modules(monkeypatch):
    # Indexing must be pure AST parsing: importing a tool module could trigger
    # side effects (e.g. connecting to an MCP server). Guard by making import
    # of a side-effectful module explode; index_tools must not touch it.
    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "_mcp":
            raise AssertionError("index_tools must not import tool modules")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    metas = await _idx.index_tools()
    assert metas  # still produced a full index


# ── extraction on a synthetic tools dir ──────────────────────────────────────


async def _write(dir_path: anyio.Path, name: str, body: str) -> None:
    await (dir_path / name).write_text(body, encoding="utf-8")


async def test_extract_signature_and_docstring(tmp_path):
    d = anyio.Path(str(tmp_path))
    await _write(
        d,
        "sample.py",
        (
            "async def sample(a: str, b: int = 3, flag: bool = False,\n"
            "                 items: list[str] | None = None) -> str:\n"
            '    """Do a sample thing.\n'
            "\n"
            "    More detail here.\n"
            "\n"
            "    Args:\n"
            "        a: first.\n"
            "    Returns:\n"
            "        text.\n"
            '    """\n'
            "    return a\n"
        ),
    )
    metas = await _idx.index_tools(d)
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "sample"
    assert m.file == "sample.py"
    assert m.signature == "sample(a: str, b: int = 3, flag: bool = False, items: list[str] | None = None)"
    assert m.summary == "Do a sample thing."
    # description stops before Args:/Returns:
    assert "More detail here." in m.description
    assert "first" not in m.description
    assert "Args:" in m.docstring


async def test_syntax_error_file_is_skipped(tmp_path):
    d = anyio.Path(str(tmp_path))
    await _write(d, "good.py", 'async def good() -> str:\n    """Good."""\n    return "x"\n')
    await _write(d, "broken.py", "async def broken( : oops\n")
    metas = await _idx.index_tools(d)
    assert {m.name for m in metas} == {"good"}


async def test_only_async_top_level_public_functions(tmp_path):
    d = anyio.Path(str(tmp_path))
    await _write(
        d,
        "mixed.py",
        (
            "def sync_fn():\n    return 1\n\n"
            "async def _private():\n    return 1\n\n"
            'async def real_tool() -> str:\n    """Real."""\n    return "x"\n'
        ),
    )
    metas = await _idx.index_tools(d)
    assert {m.name for m in metas} == {"real_tool"}


# ── tool_search ──────────────────────────────────────────────────────────────


async def test_tool_search_matches_known_tool():
    out = await tool_search("fetch url markdown")
    assert "fetch" in out


async def test_tool_search_empty_result():
    out = await tool_search("zzz_nonexistent_keyword_qqq")
    assert "no tools match" in out


async def test_tool_search_limit_truncates():
    out = await tool_search("", limit=3)
    lines = [ln for ln in out.splitlines() if " — " in ln and not ln.startswith("[")]
    assert len(lines) == 3
    assert "Truncated at 3" in out


# ── tool_search_code ─────────────────────────────────────────────────────────


async def test_tool_search_code_finds_line():
    out = await tool_search_code(r"def fetch\(")
    assert "fetch.py:" in out
    assert "def fetch(" in out


async def test_tool_search_code_invalid_regex_falls_back():
    out = await tool_search_code("fetch(")  # unbalanced paren -> invalid regex
    assert "Invalid regex" in out
    assert "fetch.py:" in out


async def test_tool_search_code_limit_truncates():
    out = await tool_search_code("import", limit=2)
    hits = [ln for ln in out.splitlines() if ":" in ln and not ln.startswith("[")]
    assert len(hits) == 2
    assert "Truncated at 2" in out


# ── tool_describe ────────────────────────────────────────────────────────────


async def test_tool_describe_known_tool():
    out = await tool_describe("find_files")
    assert "Tool: find_files" in out
    assert "File: find_files.py" in out
    assert "Signature: async def find_files(" in out
    assert "glob pattern" in out


async def test_tool_describe_unknown_suggests():
    out = await tool_describe("fetc")
    assert "no tool named 'fetc'" in out
    assert "fetch" in out


async def test_tool_describe_unknown_no_suggestion():
    out = await tool_describe("zzz_nope_qqq")
    assert "no tool named 'zzz_nope_qqq'" in out
    assert "tool_search" in out


# ── tools load cleanly into the framework registry ───────────────────────────


async def test_discovery_tools_are_valid_tool_functions():
    for name in ("tool_search", "tool_search_code", "tool_describe"):
        mod = importlib.import_module(name)
        func = getattr(mod, name)
        tf = ToolFunction.from_callable(func)
        assert tf.name == name
        assert tf.description
        assert tf.parameters["type"] == "object"
