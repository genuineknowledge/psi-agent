"""Tests for the Haitun workspace ``clarify`` tool."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

clarify_tool: Any = importlib.import_module("clarify")
clarify = clarify_tool.clarify


async def test_multiple_choice_numbers_options_and_appends_other():
    out = await clarify("用哪种缓存方案?", options=["Redis", "内存", "文件"])
    lines = out.splitlines()
    assert lines[0] == "用哪种缓存方案?"
    assert "  1. Redis" in out
    assert "  2. 内存" in out
    assert "  3. 文件" in out
    # "Other" is auto-appended as the option after the real ones.
    assert "  4. Other — type your own answer" in out
    assert out.strip().endswith("回复序号即可, 或直接说你想要的.")


async def test_recommended_marks_only_that_option():
    out = await clarify("选哪个?", options=["A", "B", "C"], recommended=2)
    assert "  2. B (recommended)" in out
    assert "  1. A\n" in out + "\n"
    assert out.count("(recommended)") == 1


async def test_recommended_zero_marks_nothing():
    out = await clarify("选哪个?", options=["A", "B"])
    assert "(recommended)" not in out


async def test_open_ended_without_options():
    out = await clarify("要部署到哪个环境?")
    assert out == "要部署到哪个环境?"
    assert "Other" not in out


async def test_open_ended_with_default():
    out = await clarify("部署到哪个环境?", default="staging")
    assert out.startswith("部署到哪个环境?")
    assert "默认 staging" in out


async def test_empty_question_is_rejected():
    out = await clarify("   ")
    assert out.startswith("[Error]")


async def test_too_many_options_rejected():
    out = await clarify("选?", options=["A", "B", "C", "D", "E"])
    assert out.startswith("[Error]")
    assert "At most 4" in out


async def test_recommended_out_of_range_rejected():
    out = await clarify("选?", options=["A", "B"], recommended=5)
    assert out.startswith("[Error]")


async def test_blank_options_are_dropped():
    # Whitespace-only options are filtered; here that collapses to open-ended.
    out = await clarify("随便问", options=["  ", ""])
    assert out == "随便问"


def test_tool_schema_is_valid():
    """The tool must expose a valid JSON schema (name, description, params)."""
    tf = ToolFunction.from_callable(clarify)
    assert tf.name == "clarify"
    assert "clarification" in tf.description
    props = tf.parameters["properties"]
    assert props["question"]["type"] == "string"
    assert props["options"]["type"] == "array"
    assert props["options"]["items"]["type"] == "string"
    assert props["recommended"]["type"] == "integer"
    # Only `question` is required; options/recommended/default have defaults.
    assert tf.parameters["required"] == ["question"]
