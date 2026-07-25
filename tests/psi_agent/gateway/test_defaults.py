from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.gateway._defaults import resolve_default_agent, resolve_default_workspace
from psi_agent.gateway._session_manager import SessionInfo


@pytest.mark.anyio
async def test_resolve_default_workspace_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "user-ws"
    await anyio.Path(ws).mkdir()
    assert await resolve_default_workspace(str(ws)) == str(await anyio.Path(ws).resolve())


@pytest.mark.anyio
async def test_resolve_default_workspace_falls_back_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert await resolve_default_workspace("") == str(await anyio.Path.cwd())


@pytest.mark.anyio
async def test_resolve_default_agent_soft_haitun_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = tmp_path / "examples" / "haitun-workspace"
    await anyio.Path(agent).mkdir(parents=True)
    assert await resolve_default_agent("") == str(await anyio.Path(agent).resolve())


@pytest.mark.anyio
async def test_resolve_default_agent_empty_without_soft_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert await resolve_default_agent("") == ""


def test_session_info_includes_agent_field() -> None:
    info = SessionInfo(
        id="s1",
        backend_type="ai",
        backend_id="ai-1",
        workspace="/ws",
        channel_socket="sock",
        agent="/agent",
    )
    assert info.agent == "/agent"
    assert info.ai_id == "ai-1"
