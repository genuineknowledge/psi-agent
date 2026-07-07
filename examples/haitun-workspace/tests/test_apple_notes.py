"""Tests for the Haitun workspace ``apple_notes`` tool.

``memo`` is a macOS-only external CLI, so these tests never invoke it. They
patch ``_preflight`` (to bypass the platform/CLI check) and the two subprocess
runners, then assert on the argument assembly, list parsing, and local search
filtering — the parts that are OS-independent.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tool: Any = importlib.import_module("apple_notes")

_LIST_OUTPUT = "\nAll your notes:\n\n1. Grocery list\n2. Meeting notes\n3. Groceries backup\n"


@pytest.fixture()
def memo(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Bypass preflight and record calls to the memo runners.

    Returns a list the test can inspect: each entry is
    ``("run", args, input_text)`` or ``("edit", args, input_text, body)``.
    """
    calls: list[tuple] = []
    monkeypatch.setattr(tool, "_preflight", lambda: None)

    async def fake_run(args, *, input_text=None, env_extra=None, timeout_seconds=120):
        calls.append(("run", list(args), input_text))
        if args[0] == "notes" and "-v" in args:
            return 0, "# Grocery list\n\n- milk\n- eggs"
        return 0, _LIST_OUTPUT

    async def fake_edit(args, body, *, input_text=None):
        calls.append(("edit", list(args), input_text, body))
        return 0, ""

    monkeypatch.setattr(tool, "_run_memo", fake_run)
    monkeypatch.setattr(tool, "_run_memo_editing", fake_edit)
    return calls


def test_tool_metadata_is_loadable() -> None:
    meta = ToolFunction.from_callable(tool.apple_notes)
    assert meta.name == "apple_notes"
    props = meta.parameters["properties"]
    assert set(props) == {"action", "query", "folder", "title", "content", "index", "no_cache"}
    # index is `int | None`; every parameter has a default, so nothing is required.
    assert props["index"]["type"] == "integer"
    assert meta.parameters.get("required", []) == []


def test_preflight_blocks_off_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.sys, "platform", "linux")
    msg = asyncio.run(tool.apple_notes(action="list"))
    assert msg.startswith("[Error]")
    assert "macOS" in msg


async def test_list_assembles_args_and_parses(memo: list[tuple]) -> None:
    out = await tool.apple_notes(action="list", folder="Work", no_cache=True)
    assert memo[-1] == ("run", ["notes", "-f", "Work", "-nc"], None)
    assert out == "Notes:\n1. Grocery list\n2. Meeting notes\n3. Groceries backup"


async def test_search_filters_titles_locally(memo: list[tuple]) -> None:
    out = await tool.apple_notes(action="search", query="groc")
    # memo is only asked to list; filtering happens in-process (case-insensitive).
    assert memo[-1][1] == ["notes"]
    assert out == "Matching notes:\n1. Grocery list\n3. Groceries backup"


async def test_search_no_match(memo: list[tuple]) -> None:
    out = await tool.apple_notes(action="search", query="nonexistent")
    assert out == "No notes found matching 'nonexistent'."


async def test_view_requires_index(memo: list[tuple]) -> None:
    assert (await tool.apple_notes(action="view")).startswith("[Error]")


async def test_view_calls_memo(memo: list[tuple]) -> None:
    out = await tool.apple_notes(action="view", index=1)
    assert memo[-1] == ("run", ["notes", "-v", "1"], None)
    assert "Grocery list" in out


async def test_create_injects_body(memo: list[tuple]) -> None:
    out = await tool.apple_notes(action="create", title="Hi", content="body here", folder="Work")
    kind, args, _input, body = memo[-1]
    assert kind == "edit"
    assert args == ["notes", "-a", "-f", "Work"]
    assert body == "# Hi\n\nbody here"
    assert "Work" in out


async def test_create_defaults_folder_and_requires_content(memo: list[tuple]) -> None:
    await tool.apple_notes(action="create", content="just a body")
    assert memo[-1][1] == ["notes", "-a", "-f", "Notes"]
    assert (await tool.apple_notes(action="create")).startswith("[Error]")


async def test_edit_feeds_index_over_stdin(memo: list[tuple]) -> None:
    out = await tool.apple_notes(action="edit", index=2, content="new body")
    kind, args, input_text, body = memo[-1]
    assert kind == "edit"
    assert args == ["notes", "-e"]
    assert input_text == "2\n"
    assert body == "new body"
    assert "2" in out


async def test_edit_validation(memo: list[tuple]) -> None:
    assert (await tool.apple_notes(action="edit", content="x")).startswith("[Error]")
    assert (await tool.apple_notes(action="edit", index=1)).startswith("[Error]")


async def test_unknown_action(memo: list[tuple]) -> None:
    assert (await tool.apple_notes(action="frobnicate")).startswith("[Error] Unknown action")


def test_parse_notes_ignores_noise() -> None:
    parsed = tool._parse_notes("Fetching notes...\n\n1. Alpha\n2. Beta\nnot a note line\n")
    assert parsed == [(1, "Alpha"), (2, "Beta")]
