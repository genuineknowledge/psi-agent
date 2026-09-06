from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_smoke_module():
    path = Path(__file__).parents[1] / "scripts" / "pyinstaller_system_smoke.py"
    spec = importlib.util.spec_from_file_location("pyinstaller_system_smoke", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_stop_kills_frozen_process_tree_after_parent_exit(monkeypatch):
    smoke = _load_smoke_module()
    calls: list[int] = []

    class FakeProcess:
        pid = 1234

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(smoke, "_is_windows", lambda: True)
    monkeypatch.setattr(smoke, "_kill_process_tree", lambda pid: calls.append(pid))

    smoke._stop(FakeProcess())

    assert calls == [1234]


def test_kill_process_tree_uses_taskkill_tree_mode(monkeypatch):
    smoke = _load_smoke_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    smoke._kill_process_tree(5678)

    assert calls == [
        (
            ["taskkill", "/PID", "5678", "/T", "/F"],
            {"check": False, "stdout": smoke.subprocess.DEVNULL, "stderr": smoke.subprocess.DEVNULL},
        )
    ]
