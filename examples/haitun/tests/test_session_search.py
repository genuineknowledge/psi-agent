"""Tests for session keyword/task search tools."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

session_helpers: Any = importlib.import_module("_session_helpers")
keyword_tool: Any = importlib.import_module("session_keyword_search")
task_tool: Any = importlib.import_module("session_task_search")


def test_keyword_tool_metadata() -> None:
    meta = ToolFunction.from_callable(keyword_tool.session_keyword_search)
    assert meta.name == "session_keyword_search"
    assert "query" in meta.parameters["properties"]


def test_task_tool_metadata() -> None:
    meta = ToolFunction.from_callable(task_tool.session_task_search)
    assert meta.name == "session_task_search"
    assert meta.parameters["required"] == ["category"]


@pytest.mark.anyio
async def test_keyword_search_finds_match(tmp_path: Path, app_history: Path) -> None:
    (app_history / "alpha.jsonl").write_text(
        '{"role":"user","content":"talk about Docker deploy"}\n{"role":"assistant","content":"ok"}\n',
        encoding="utf-8",
    )
    (app_history / "beta.jsonl").write_text(
        '{"role":"user","content":"unrelated"}\n',
        encoding="utf-8",
    )

    result = await session_helpers.keyword_search_sessions(
        query="Docker",
        workspace_raw=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["hits"][0]["session_id"] == "alpha"
    assert result["hits"][0]["snippets"]


@pytest.mark.anyio
async def test_keyword_search_scoped_to_session(tmp_path: Path, app_history: Path) -> None:
    (app_history / "one.jsonl").write_text('{"role":"user","content":"needle here"}\n', encoding="utf-8")
    (app_history / "two.jsonl").write_text('{"role":"user","content":"needle there"}\n', encoding="utf-8")

    result = await session_helpers.keyword_search_sessions(
        query="needle",
        session_id="one",
        workspace_raw=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["hits"][0]["session_id"] == "one"


@pytest.mark.anyio
async def test_keyword_search_empty_query(tmp_path: Path) -> None:
    result = await session_helpers.keyword_search_sessions(query="", workspace_raw=str(tmp_path))
    assert result["ok"] is False


@pytest.mark.anyio
async def test_task_search_subagent(tmp_path: Path, app_history: Path) -> None:
    (app_history / "sub-abc12345.jsonl").write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")

    result = await session_helpers.task_search_sessions(
        category="subagent",
        workspace_raw=str(tmp_path),
        include_gateway=False,
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["hits"][0]["session_id"] == "sub-abc12345"
    assert "subagent" in result["hits"][0]["categories"]


@pytest.mark.anyio
async def test_task_search_github(tmp_path: Path, app_history: Path) -> None:
    (app_history / "main.jsonl").write_text(
        '{"role":"user","content":"please open a GitHub pull request"}\n',
        encoding="utf-8",
    )

    result = await session_helpers.task_search_sessions(
        category="github",
        workspace_raw=str(tmp_path),
        include_gateway=False,
    )
    assert result["ok"] is True
    assert result["count"] == 1
    assert "github" in result["hits"][0]["categories"]


@pytest.mark.anyio
async def test_task_search_invalid_category(tmp_path: Path) -> None:
    result = await session_helpers.task_search_sessions(
        category="not-a-category",
        workspace_raw=str(tmp_path),
    )
    assert result["ok"] is False


@pytest.mark.anyio
async def test_tools_return_json(tmp_path: Path, app_history: Path) -> None:
    (app_history / "x.jsonl").write_text('{"role":"user","content":"findme"}\n', encoding="utf-8")

    kw = json.loads(await keyword_tool.session_keyword_search(query="findme", workspace=str(tmp_path)))
    task = json.loads(
        await task_tool.session_task_search(category="all", workspace=str(tmp_path), include_gateway=False)
    )
    assert kw["ok"] is True
    assert task["ok"] is True
    assert task["count"] == 1
