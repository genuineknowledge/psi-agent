from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.gateway.electron import ElectronRuntimeNotFoundError, ensure_electron_runtime, resolve_launch_spec


def test_resolve_launch_spec_prefers_local_electron_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "electron"
    cli_path = project_dir / "node_modules" / "electron" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name == "node":
            return "C:/Tools/node.exe"
        return None

    monkeypatch.setattr("psi_agent.gateway.electron.shutil.which", fake_which)

    spec = resolve_launch_spec(project_dir=project_dir)

    assert spec.command == ("C:/Tools/node.exe", str(cli_path), str(project_dir.resolve()))
    assert spec.cwd == str(project_dir.resolve())


def test_resolve_launch_spec_falls_back_to_global_electron(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "electron"
    project_dir.mkdir()

    def fake_which(name: str) -> str | None:
        if name in {"electron", "electron.cmd"}:
            return "C:/Tools/electron.cmd"
        return None

    monkeypatch.setattr("psi_agent.gateway.electron.shutil.which", fake_which)

    spec = resolve_launch_spec(project_dir=project_dir)

    assert spec.command == ("C:/Tools/electron.cmd", str(project_dir.resolve()))
    assert spec.cwd == str(project_dir.resolve())


def test_resolve_launch_spec_raises_helpful_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "electron"
    project_dir.mkdir()
    monkeypatch.setattr("psi_agent.gateway.electron.shutil.which", lambda _name: None)

    with pytest.raises(ElectronRuntimeNotFoundError, match="install it automatically"):
        resolve_launch_spec(project_dir=project_dir)


@pytest.mark.anyio
async def test_ensure_electron_runtime_bootstraps_local_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "electron"
    project_dir.mkdir()
    cli_path = project_dir / "node_modules" / "electron" / "cli.js"
    state = {"installed": False}
    commands: list[tuple[tuple[str, ...], str]] = []

    def fake_which(name: str) -> str | None:
        if name in {"npm", "npm.cmd"}:
            return "C:/Tools/npm.cmd"
        if state["installed"] and name == "node":
            return "C:/Tools/node.exe"
        return None

    async def fake_run_process(command: list[str], cwd: str, env: dict[str, str]) -> None:
        commands.append((tuple(command), cwd))
        assert env["PATH"]
        cli_path.parent.mkdir(parents=True)
        cli_path.write_text("", encoding="utf-8")
        state["installed"] = True

    monkeypatch.setattr("psi_agent.gateway.electron.shutil.which", fake_which)
    monkeypatch.setattr("psi_agent.gateway.electron.anyio.run_process", fake_run_process)

    spec = await ensure_electron_runtime(project_dir=project_dir)

    assert commands == [(("C:/Tools/npm.cmd", "install"), str(project_dir.resolve()))]
    assert spec.command == ("C:/Tools/node.exe", str(cli_path), str(project_dir.resolve()))
    assert spec.cwd == str(project_dir.resolve())


@pytest.mark.anyio
async def test_ensure_electron_runtime_requires_npm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "electron"
    project_dir.mkdir()
    monkeypatch.setattr("psi_agent.gateway.electron.shutil.which", lambda _name: None)

    with pytest.raises(FileNotFoundError, match="npm was not found"):
        await ensure_electron_runtime(project_dir=project_dir)
