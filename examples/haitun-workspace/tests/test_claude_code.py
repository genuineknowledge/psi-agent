"""Tests for the Haitun workspace ``claude_code`` tool.

``claude`` is an external Node CLI, so these tests never invoke it. They patch
``_preflight`` (to bypass the CLI-on-PATH check), stub ``anyio.run_process``,
and force ``anyio.Path.is_dir`` true, then assert on argv assembly, json-result
formatting, and the validation/error branches — all OS-independent.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tool: Any = importlib.import_module("claude_code")


@dataclass
class _FakeProcess:
    returncode: int
    stdout: bytes
    stderr: bytes


@pytest.fixture()
def claude(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Bypass preflight + dir check and capture the argv passed to run_process.

    Returns a mutable dict: set ``result["proc"]`` to control the fake process,
    read ``result["cmd"]`` / ``result["cwd"]`` after the call.
    """
    captured: dict[str, Any] = {"cmd": None, "cwd": None, "proc": _FakeProcess(0, b'{"result": "done"}', b"")}

    async def fake_run_process(
        cmd: list[str], *, cwd: str | None = None, check: bool = False, **_: Any
    ) -> _FakeProcess:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return captured["proc"]

    async def fake_is_dir(self: Any) -> bool:
        return True

    monkeypatch.setattr(tool, "_resolve_cli", lambda: "claude")
    monkeypatch.setattr(tool.anyio, "run_process", fake_run_process)
    monkeypatch.setattr(anyio.Path, "is_dir", fake_is_dir)
    return captured


async def test_minimal_prompt_builds_expected_argv(claude: dict[str, Any]) -> None:
    await tool.claude_code("add a health endpoint")
    cmd = claude["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert "add a health endpoint" in cmd
    # json is the default output format, acceptEdits the default permission mode.
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert claude["cwd"] == "."


async def test_options_map_to_flags(claude: dict[str, Any]) -> None:
    await tool.claude_code(
        "open a PR",
        directory="/repo",
        model="opus",
        permission_mode="bypassPermissions",
        allowed_tools='"Bash(git push *)" Read',
        disallowed_tools="Edit",
        add_dirs="../lib ../apps",
        append_system_prompt="Use TypeScript",
        max_turns=5,
        output_format="text",
    )
    cmd = claude["cmd"]
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    # allowed_tools is shlex-split: the quoted rule stays one token.
    ai = cmd.index("--allowed-tools")
    assert cmd[ai + 1] == "Bash(git push *)"
    assert cmd[ai + 2] == "Read"
    assert cmd[cmd.index("--disallowed-tools") + 1] == "Edit"
    # Each add_dir gets its own --add-dir flag.
    assert cmd.count("--add-dir") == 2
    assert cmd[cmd.index("--append-system-prompt") + 1] == "Use TypeScript"
    assert cmd[cmd.index("--max-turns") + 1] == "5"
    assert cmd[cmd.index("--output-format") + 1] == "text"
    assert claude["cwd"] == "/repo"


async def test_resume_and_continue_flags(claude: dict[str, Any]) -> None:
    await tool.claude_code("iterate", resume_session_id="abc-123", continue_recent=True)
    cmd = claude["cmd"]
    assert cmd[cmd.index("--resume") + 1] == "abc-123"
    assert "--continue" in cmd


async def test_json_result_is_summarized(claude: dict[str, Any]) -> None:
    payload = {
        "result": "Implemented the feature.",
        "session_id": "sess-9",
        "num_turns": 4,
        "total_cost_usd": 0.12,
        "is_error": False,
    }
    claude["proc"] = _FakeProcess(0, json.dumps(payload).encode(), b"")
    out = await tool.claude_code("do it")
    assert "Implemented the feature." in out
    assert "session_id=sess-9" in out
    assert "turns=4" in out
    assert "cost_usd=0.12" in out


async def test_text_format_returns_raw(claude: dict[str, Any]) -> None:
    claude["proc"] = _FakeProcess(0, b"just the text", b"")
    out = await tool.claude_code("do it", output_format="text")
    assert out == "just the text"


async def test_nonzero_exit_is_reported(claude: dict[str, Any]) -> None:
    claude["proc"] = _FakeProcess(2, b"", b"boom")
    out = await tool.claude_code("do it")
    assert out.startswith("[Error] Claude Code exited with code 2")
    assert "boom" in out


async def test_empty_prompt_rejected(claude: dict[str, Any]) -> None:
    assert (await tool.claude_code("   ")).startswith("[Error] prompt is required")


async def test_bad_permission_mode_rejected(claude: dict[str, Any]) -> None:
    out = await tool.claude_code("do it", permission_mode="nope")
    assert out.startswith("[Error] permission_mode must be one of")


async def test_bad_output_format_rejected(claude: dict[str, Any]) -> None:
    out = await tool.claude_code("do it", output_format="yaml")
    assert out.startswith("[Error] output_format must be one of")


async def test_missing_directory_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "_resolve_cli", lambda: "claude")

    async def fake_is_dir(self: Any) -> bool:
        return False

    monkeypatch.setattr(anyio.Path, "is_dir", fake_is_dir)
    out = await tool.claude_code("do it", directory="/nope")
    assert out.startswith("[Error] directory does not exist")


async def test_missing_cli_reports_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.shutil, "which", lambda _: None)
    out = await tool.claude_code("do it")
    assert out.startswith("[Error] `claude` CLI not found")
    assert "npm install -g @anthropic-ai/claude-code" in out


def test_tool_registers_as_valid_toolfunction() -> None:
    """The public async fn must convert to a ToolFunction (schema is valid)."""
    tf = ToolFunction.from_callable(tool.claude_code)
    assert tf.name == "claude_code"
    assert tf.description
    # Only ``prompt`` is required; everything else has a default.
    assert tf.parameters["required"] == ["prompt"]
    assert "permission_mode" in tf.parameters["properties"]
