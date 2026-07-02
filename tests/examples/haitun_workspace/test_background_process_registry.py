"""Tests for haitun-workspace background process registry."""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest

WORKSPACE_TOOLS = Path(__file__).resolve().parents[3] / "examples" / "haitun-workspace" / "tools"
if str(WORKSPACE_TOOLS) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_TOOLS))

from _background_process_registry import (  # noqa: E402
    _is_safe_registry_pid,
    _read_registry,
    _registry_critical,
    _sync_taskkill_pid,
    _write_registry,
    registry_path,
    resolve_workspace,
    shell_argv,
    start_process,
    stop_process,
)


def test_resolve_workspace_default_haitun() -> None:
    ws = resolve_workspace("")
    assert "haitun-workspace" in str(ws)


def test_shell_argv_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        shell_argv("   ")


def test_shell_argv_uses_bash_or_shell() -> None:
    argv, shell = shell_argv("echo hello")
    assert argv[-1] == "echo hello"
    assert shell in ("bash", "powershell", "sh")


def test_is_safe_registry_pid_rejects_self_and_parent(monkeypatch) -> None:
    monkeypatch.setattr("_background_process_registry.os.getpid", lambda: 100)
    monkeypatch.setattr("_background_process_registry.os.getppid", lambda: 200)
    assert _is_safe_registry_pid(100) is False
    assert _is_safe_registry_pid(200) is False
    assert _is_safe_registry_pid(999) is True


def test_sync_taskkill_pid_no_tree_flag(monkeypatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows taskkill semantics")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr("_background_process_registry.subprocess.run", fake_run)
    _sync_taskkill_pid(4242)
    assert calls == [["taskkill", "/F", "/PID", "4242"]]


@pytest.mark.anyio
async def test_start_and_stop_echo(tmp_path: Path) -> None:
    workspace = anyio.Path(tmp_path)
    cmd = "sleep 60"
    started = await start_process(command=cmd, workspace_raw=str(workspace))
    assert started["ok"] is True
    pid = int(started["pid"])
    assert pid > 0
    bg_id = str(started["process_id"])
    assert bg_id.startswith("bg-")

    stopped = await stop_process(process_id=bg_id, workspace_raw=str(workspace))
    assert stopped["ok"] is True
    assert stopped["process_id"] == bg_id

    reg = await _read_registry(registry_path(workspace))
    assert reg.get("processes") == {}


@pytest.mark.anyio
async def test_registry_critical_serializes_writes(tmp_path: Path) -> None:
    workspace = anyio.Path(tmp_path)
    reg_path = registry_path(workspace)
    concurrent = 0
    max_concurrent = 0

    async def bump(process_id: str) -> None:
        nonlocal concurrent, max_concurrent

        async def _mutate(registry: dict[str, object]) -> None:
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await anyio.sleep(0.05)
            processes = registry.setdefault("processes", {})
            if isinstance(processes, dict):
                processes[process_id] = {"pid": 1}
            concurrent -= 1

        async with _registry_critical(workspace):
            registry = await _read_registry(reg_path)
            await _mutate(registry)
            await _write_registry(reg_path, registry)

    async with anyio.create_task_group() as tg:
        for i in range(4):
            tg.start_soon(bump, f"bg-{i}")

    assert max_concurrent == 1
    saved = await _read_registry(reg_path)
    processes = saved.get("processes")
    assert isinstance(processes, dict)
    assert len(processes) == 4
