"""Tests for haitun-workspace subagent registry helpers."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest

WORKSPACE_TOOLS = Path(__file__).resolve().parents[3] / "examples" / "haitun-workspace" / "tools"
if str(WORKSPACE_TOOLS) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_TOOLS))

from _subagent_registry import (  # noqa: E402
    _is_safe_registry_pid,
    _psi_cmd,
    _read_registry,
    _registry_critical,
    _resolve_project_root,
    _sync_taskkill_pid,
    _write_registry,
    ai_pool_missing_message,
    ai_pool_owns_process,
    other_active_subagent_session_ids,
    plan_ai_pool_for_session,
    registry_path,
    resolve_workspace,
    select_idle_session_ids,
    standalone_ai_configured,
)


def test_resolve_workspace_is_sync() -> None:
    ws = resolve_workspace("")
    assert "haitun-workspace" in str(ws)


def test_select_idle_session_ids_marks_stale() -> None:
    now = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    old = (now - timedelta(seconds=2000)).isoformat()
    recent = (now - timedelta(seconds=10)).isoformat()
    registry = {
        "sessions": {
            "sub-old": {"last_used_at": old},
            "sub-new": {"last_used_at": recent},
            "sub-missing": {},
        }
    }
    stale = select_idle_session_ids(registry, now=now, idle_seconds=1800)
    assert "sub-old" in stale
    assert "sub-missing" in stale
    assert "sub-new" not in stale


def test_standalone_ai_configured(monkeypatch) -> None:
    for name in (
        "PSI_AI_API_KEY",
        "FLOW_PSI_API_KEY",
        "OPENAI_API_KEY",
        "PSI_AI_BASE_URL",
        "FLOW_PSI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert standalone_ai_configured() is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert standalone_ai_configured() is True


def test_ai_pool_missing_message_mentions_api_key() -> None:
    msg = ai_pool_missing_message()
    assert "OPENAI_API_KEY" in msg or "FLOW_PSI_API_KEY" in msg


def test_ai_pool_owns_process() -> None:
    assert ai_pool_owns_process({"ai_pid": 12345}) is True
    assert ai_pool_owns_process({"from_parent": True, "ai_pid": 12345}) is False
    assert ai_pool_owns_process({"from_gateway": True, "ai_pid": 999}) is False
    assert ai_pool_owns_process({"ai_pid": -1}) is False


def test_plan_ai_pool_for_session_always_dedicated_per_session() -> None:
    ws = anyio.Path("D:/ws")
    registry: dict[str, object] = {"ai_pools": {}, "sessions": {}}
    key = plan_ai_pool_for_session(registry, ws, "sub-a")
    assert key == f"{ws}::ai::sub-a"


def test_plan_ai_pool_for_session_reuses_existing_session_pool_key() -> None:
    ws = anyio.Path("D:/ws")
    registry = {
        "sessions": {
            "sub-a": {
                "workspace": str(ws),
                "ai_pool_key": f"{ws}::ai::sub-a",
            }
        }
    }
    key = plan_ai_pool_for_session(registry, ws, "sub-a")
    assert key == f"{ws}::ai::sub-a"


def test_resolve_project_root_finds_repo() -> None:
    ws = anyio.Path(str(Path(__file__).resolve().parents[3] / "examples" / "haitun-workspace"))
    root = _resolve_project_root(ws)
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "psi_agent").is_dir()


def test_psi_cmd_prefers_venv_executable() -> None:
    ws = anyio.Path(str(Path(__file__).resolve().parents[3] / "examples" / "haitun-workspace"))
    cmd = _psi_cmd(ws)
    assert cmd
    assert cmd[-1] == "psi-agent" or cmd[-1].endswith("psi-agent.exe")


def test_other_active_subagent_session_ids(monkeypatch) -> None:
    ws = anyio.Path("D:/ws")
    registry = {
        "sessions": {
            "sub-live": {"workspace": str(ws), "session_pid": 11},
            "sub-dead": {"workspace": str(ws), "session_pid": 22},
            "sub-other-ws": {"workspace": "D:/other", "session_pid": 33},
        }
    }
    monkeypatch.setattr("_subagent_registry._pid_alive", lambda pid: pid == 11)
    active = other_active_subagent_session_ids(registry, ws, exclude_session_id="sub-new")
    assert active == ["sub-live"]


def test_is_safe_registry_pid_rejects_self_and_parent(monkeypatch) -> None:
    monkeypatch.setattr("_subagent_registry.os.getpid", lambda: 100)
    monkeypatch.setattr("_subagent_registry.os.getppid", lambda: 200)
    assert _is_safe_registry_pid(100) is False
    assert _is_safe_registry_pid(200) is False
    assert _is_safe_registry_pid(999) is True


def test_sync_taskkill_pid_uses_single_pid_only(monkeypatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows taskkill semantics")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr("_subagent_registry.subprocess.run", fake_run)
    _sync_taskkill_pid(4242)
    assert calls == [["taskkill", "/F", "/PID", "4242"]]
    assert "/T" not in calls[0]


@pytest.mark.anyio
async def test_registry_critical_serializes_concurrent_writes(tmp_path: Path) -> None:
    workspace = anyio.Path(tmp_path)
    reg_path = registry_path(workspace)
    concurrent = 0
    max_concurrent = 0

    async def bump(session_id: str) -> None:
        nonlocal concurrent, max_concurrent

        async def _mutate(registry: dict[str, object]) -> None:
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await anyio.sleep(0.05)
            sessions = registry.setdefault("sessions", {})
            if isinstance(sessions, dict):
                sessions[session_id] = {"last_used_at": session_id}
            concurrent -= 1

        async with _registry_critical(workspace):
            registry = await _read_registry(reg_path)
            await _mutate(registry)
            await _write_registry(reg_path, registry)

    async with anyio.create_task_group() as tg:
        for i in range(4):
            tg.start_soon(bump, f"sub-{i}")

    assert max_concurrent == 1
    saved = await _read_registry(reg_path)
    sessions = saved.get("sessions")
    assert isinstance(sessions, dict)
    assert len(sessions) == 4
