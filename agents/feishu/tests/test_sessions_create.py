"""Tests for sessions_create tool."""

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
create_tool: Any = importlib.import_module("sessions_create")


def test_create_tool_metadata() -> None:
    meta = ToolFunction.from_callable(create_tool.sessions_create)
    assert meta.name == "sessions_create"
    assert meta.parameters["required"] == []


@pytest.mark.anyio
async def test_create_requires_gateway(tmp_path: Path) -> None:
    result = await session_helpers.create_session(workspace_raw=str(tmp_path))
    assert result["ok"] is False
    assert "Gateway" in result["message"]


@pytest.mark.anyio
async def test_create_success_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(_workspace: Path) -> str:
        return "http://127.0.0.1:9999"

    async def fake_ai_id(_url: str, *, workspace: Path, gateway_ai_id: str = "") -> str:
        return "ai-test"

    async def fake_post(_url: str, body: dict[str, Any], *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        assert body["ai_id"] == "ai-test"
        return {
            "id": "new-session-1",
            "ai_id": "ai-test",
            "workspace": body["workspace"],
            "channel_socket": r"\\.\pipe\psi\channels\new-session-1",
        }

    async def fake_ready(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "session_id": kwargs.get("session_id", ""),
            "channel_socket": r"\\.\pipe\psi\channels\new-session-1",
        }

    monkeypatch.setattr(session_helpers._sub, "resolve_gateway_url", fake_resolve)
    monkeypatch.setattr(session_helpers._sub, "_resolve_ai_id_for_workspace", fake_ai_id)
    monkeypatch.setattr(session_helpers._sub, "post_gateway_json", fake_post)
    monkeypatch.setattr(session_helpers, "resolve_channel_socket", fake_ready)

    raw = await create_tool.sessions_create(workspace=str(tmp_path))
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["session_id"] == "new-session-1"
    assert payload["channel_socket"]
