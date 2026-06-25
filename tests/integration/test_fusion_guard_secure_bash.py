from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.fusion_guard.runner import secure_bash
from psi_agent.session.runtime_context import SessionToolContext


def _ctx(tmp_path: Path) -> SessionToolContext:
    return SessionToolContext(
        session_id="session-1",
        workspace_path=tmp_path,
        history_path=tmp_path / "histories" / "session-1.jsonl",
        history_messages=[{"role": "user", "content": "run a command"}],
        latest_user_message={"role": "user", "content": "run a command"},
        ai_socket="http://127.0.0.1:1",
    )


@pytest.mark.anyio
async def test_secure_bash_denies_and_does_not_execute(tmp_path: Path) -> None:
    calls: list[str] = []

    async def fake_analysis(*, prompt: str, ctx: SessionToolContext) -> str:
        assert "USER_MESSAGE_BEGIN" in prompt
        return "DENY"

    async def fake_executor(command: str, cwd: str | None, ctx: SessionToolContext) -> str:
        calls.append(command)
        return "executed"

    result = await secure_bash(
        "cat /etc/shadow",
        cwd=str(tmp_path),
        context_override=_ctx(tmp_path),
        analysis_runner=fake_analysis,
        executor=fake_executor,
    )

    assert result == "[Fusion-Guard] Security policy denied this request: Fusion-Guard denied the requested operation"
    assert calls == []


@pytest.mark.anyio
async def test_secure_bash_none_executes_without_policy_install(tmp_path: Path) -> None:
    installed: list[list[str]] = []

    async def fake_analysis(*, prompt: str, ctx: SessionToolContext) -> str:
        return "NONE"

    async def fake_installer(rules: list[str], ctx: SessionToolContext) -> None:
        installed.append(rules)

    async def fake_executor(command: str, cwd: str | None, ctx: SessionToolContext) -> str:
        return f"executed: {command}"

    result = await secure_bash(
        "pwd",
        cwd=str(tmp_path),
        context_override=_ctx(tmp_path),
        analysis_runner=fake_analysis,
        policy_installer=fake_installer,
        executor=fake_executor,
    )

    assert result == "executed: pwd"
    assert installed == []


@pytest.mark.anyio
async def test_secure_bash_allow_rules_installs_before_execute(tmp_path: Path) -> None:
    events: list[str] = []

    async def fake_analysis(*, prompt: str, ctx: SessionToolContext) -> str:
        return "allow a_t b_t:file { read };"

    async def fake_installer(rules: list[str], ctx: SessionToolContext) -> None:
        events.append(f"install:{rules[0]}")

    async def fake_executor(command: str, cwd: str | None, ctx: SessionToolContext) -> str:
        events.append(f"execute:{command}")
        return "ok"

    result = await secure_bash(
        "pwd",
        context_override=_ctx(tmp_path),
        analysis_runner=fake_analysis,
        policy_installer=fake_installer,
        executor=fake_executor,
    )

    assert result == "ok"
    assert events == ["install:allow a_t b_t:file { read };", "execute:pwd"]
