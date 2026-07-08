"""Tests for the Haitun workspace ``codex`` tool.

``codex`` is an external Rust/npm CLI, so these tests never invoke it. They
stub ``_resolve_cli`` (to bypass the CLI-on-PATH check), stub
``anyio.run_process`` (which also writes the fake final message into the
``--output-last-message`` file so the tool reads it back), and force
``anyio.Path.is_dir`` true, then assert on argv assembly and the
validation/error branches — all OS-independent.
"""

from __future__ import annotations

import importlib
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

tool: Any = importlib.import_module("codex")


@dataclass
class _FakeProcess:
    returncode: int
    stdout: bytes
    stderr: bytes


@pytest.fixture()
def codex(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Bypass CLI resolve + dir check and capture the argv passed to run_process.

    The fake run_process also writes ``last_message`` into the
    ``--output-last-message`` path so the tool reads back a realistic result.
    Set ``result["last_message"]`` / ``result["proc"]`` before the call; read
    ``result["cmd"]`` / ``result["cwd"]`` after.
    """
    captured: dict[str, Any] = {
        "cmd": None,
        "cwd": None,
        "proc": _FakeProcess(0, b"", b""),
        "last_message": "done",
    }

    async def fake_run_process(
        cmd: list[str], *, cwd: str | None = None, check: bool = False, **_: Any
    ) -> _FakeProcess:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        # Emulate --output-last-message: write the fake final message to the file.
        idx = cmd.index("--output-last-message")
        await anyio.Path(cmd[idx + 1]).write_text(captured["last_message"], encoding="utf-8")
        return captured["proc"]

    async def fake_is_dir(self: Any) -> bool:
        return True

    monkeypatch.setattr(tool, "_resolve_cli", lambda: "codex")
    monkeypatch.setattr(tool.anyio, "run_process", fake_run_process)
    monkeypatch.setattr(anyio.Path, "is_dir", fake_is_dir)
    return captured


async def test_minimal_prompt_builds_expected_argv(codex: dict[str, Any]) -> None:
    await tool.codex("add a health endpoint")
    cmd = codex["cmd"]
    assert cmd[:2] == ["codex", "exec"]
    # Prompt is the last positional arg.
    assert cmd[-1] == "add a health endpoint"
    # workspace-write is the default sandbox.
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[cmd.index("--cwd") + 1] == "."
    assert codex["cwd"] == "."


async def test_options_map_to_flags(codex: dict[str, Any]) -> None:
    await tool.codex(
        "open a PR",
        directory="/repo",
        model="gpt-5.2",
        sandbox="danger-full-access",
        skip_git_repo_check=True,
        add_dirs="../lib ../apps",
        images="a.png,b.png",
        output_schema="schema.json",
        json_events=True,
    )
    cmd = codex["cmd"]
    assert cmd[cmd.index("--model") + 1] == "gpt-5.2"
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access"
    assert "--skip-git-repo-check" in cmd
    # Each add_dir gets its own --add-dir flag.
    assert cmd.count("--add-dir") == 2
    assert cmd[cmd.index("--images") + 1] == "a.png,b.png"
    assert cmd[cmd.index("--output-schema") + 1] == "schema.json"
    assert "--json" in cmd
    assert cmd[cmd.index("--cwd") + 1] == "/repo"
    assert codex["cwd"] == "/repo"


async def test_dangerously_bypass_replaces_sandbox(codex: dict[str, Any]) -> None:
    await tool.codex("do it", dangerously_bypass=True)
    cmd = codex["cmd"]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    # --sandbox is omitted when bypassing entirely.
    assert "--sandbox" not in cmd


async def test_resume_by_session_id(codex: dict[str, Any]) -> None:
    await tool.codex("iterate", resume_session_id="sess-123")
    cmd = codex["cmd"]
    assert cmd[2] == "resume"
    assert cmd[3] == "sess-123"


async def test_continue_recent_uses_resume_last(codex: dict[str, Any]) -> None:
    await tool.codex("keep going", continue_recent=True)
    cmd = codex["cmd"]
    assert cmd[2] == "resume"
    assert cmd[3] == "--last"


async def test_last_message_is_returned(codex: dict[str, Any]) -> None:
    codex["last_message"] = "Implemented the feature."
    out = await tool.codex("do it")
    assert out == "Implemented the feature."


async def test_json_events_returns_raw_stream(codex: dict[str, Any]) -> None:
    codex["proc"] = _FakeProcess(0, b'{"type":"AgentMessage","content":"hi"}', b"")
    out = await tool.codex("do it", json_events=True)
    assert out == '{"type":"AgentMessage","content":"hi"}'


async def test_nonzero_exit_is_reported(codex: dict[str, Any]) -> None:
    codex["proc"] = _FakeProcess(2, b"", b"boom")
    out = await tool.codex("do it")
    assert out.startswith("[Error] Codex exited with code 2")
    assert "boom" in out


async def test_empty_prompt_rejected(codex: dict[str, Any]) -> None:
    assert (await tool.codex("   ")).startswith("[Error] prompt is required")


async def test_bad_sandbox_rejected(codex: dict[str, Any]) -> None:
    out = await tool.codex("do it", sandbox="nope")
    assert out.startswith("[Error] sandbox must be one of")


async def test_missing_directory_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool, "_resolve_cli", lambda: "codex")

    async def fake_is_dir(self: Any) -> bool:
        return False

    monkeypatch.setattr(anyio.Path, "is_dir", fake_is_dir)
    out = await tool.codex("do it", directory="/nope")
    assert out.startswith("[Error] directory does not exist")


async def test_missing_cli_reports_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool.shutil, "which", lambda _: None)
    out = await tool.codex("do it")
    assert out.startswith("[Error] `codex` CLI not found")
    assert "npm install -g @openai/codex" in out


def test_tool_registers_as_valid_toolfunction() -> None:
    """The public async fn must convert to a ToolFunction (schema is valid)."""
    tf = ToolFunction.from_callable(tool.codex)
    assert tf.name == "codex"
    assert tf.description
    # Only ``prompt`` is required; everything else has a default.
    assert tf.parameters["required"] == ["prompt"]
    assert "sandbox" in tf.parameters["properties"]
