"""Smoke test: subagent_plan + background_start + subagent_chat (Windows TCP)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKSPACE_TOOLS = Path(__file__).resolve().parents[3] / "examples" / "haitun-workspace" / "tools"
if str(WORKSPACE_TOOLS) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_TOOLS))

from _background_process_registry import start_process, stop_process  # noqa: E402
from _subagent_helpers import chat_subagent, plan_subagent, wait_socket  # noqa: E402


@pytest.mark.anyio
async def test_subagent_tcp_smoke() -> None:
    plan = await plan_subagent(session_id="sub-smoketest", workspace_raw=str(WORKSPACE_TOOLS.parent))
    assert plan["ok"] is True, plan.get("message")
    assert plan["transport"] == "tcp"
    assert plan["shell"] == "powershell"

    started_ai = await start_process(
        command=plan["ai_command"],
        process_id=plan["ai_process_id"],
        cwd=plan["repo_root"],
        workspace_raw=plan["workspace"],
        shell=plan["shell"],
    )
    assert started_ai["ok"] is True, started_ai

    ready_ai = await wait_socket(plan["ai_socket"], timeout_seconds=45.0)
    assert ready_ai["ok"] is True, ready_ai

    started_sess = await start_process(
        command=plan["session_command"],
        process_id=plan["session_process_id"],
        cwd=plan["repo_root"],
        workspace_raw=plan["workspace"],
        shell=plan["shell"],
    )
    assert started_sess["ok"] is True, started_sess

    ready_ch = await wait_socket(plan["channel_socket"], timeout_seconds=45.0)
    assert ready_ch["ok"] is True, ready_ch

    chat = await chat_subagent(
        channel_socket=plan["channel_socket"],
        message=(
            "List exactly 3 skill folder names under skills/ that start with letter p "
            "(lowercase). Reply as a bullet list only."
        ),
        timeout_seconds=120.0,
    )
    assert chat["ok"] is True, chat
    assert "p" in chat["text"].lower()

    stopped_sess = await stop_process(process_id=plan["session_process_id"], workspace_raw=plan["workspace"])
    stopped_ai = await stop_process(process_id=plan["ai_process_id"], workspace_raw=plan["workspace"])
    assert stopped_sess["ok"] is True
    assert stopped_ai["ok"] is True
