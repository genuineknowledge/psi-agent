from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import anyio
from loguru import logger


@dataclass(frozen=True)
class ElectronLaunchSpec:
    command: tuple[str, ...]
    cwd: str


class ElectronRuntimeNotFoundError(FileNotFoundError):
    """Raised when neither a local nor a global Electron runtime is available."""


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def resolve_launch_spec(project_dir: Path | None = None) -> ElectronLaunchSpec:
    resolved_dir = (project_dir or _project_dir()).resolve()
    electron_cli = resolved_dir / "node_modules" / "electron" / "cli.js"
    if electron_cli.exists():
        node = shutil.which("node")
        if node is None:
            raise FileNotFoundError(
                "Node.js was not found on PATH. Install Node.js to use `psi-agent gateway --desktop`."
            )
        return ElectronLaunchSpec(command=(node, str(electron_cli), str(resolved_dir)), cwd=str(resolved_dir))

    electron_name = "electron.cmd" if sys.platform == "win32" else "electron"
    electron_binary = shutil.which(electron_name) or shutil.which("electron")
    if electron_binary is not None:
        return ElectronLaunchSpec(command=(electron_binary, str(resolved_dir)), cwd=str(resolved_dir))

    raise ElectronRuntimeNotFoundError(
        f"Electron runtime was not found under {resolved_dir}. "
        f"`psi-agent gateway --desktop` can install it automatically if Node.js/npm is available."
    )


def _npm_command() -> str:
    npm_name = "npm.cmd" if sys.platform == "win32" else "npm"
    npm_binary = shutil.which(npm_name) or shutil.which("npm")
    if npm_binary is None:
        raise FileNotFoundError("npm was not found on PATH. Install Node.js to bootstrap Electron automatically.")
    return npm_binary


async def ensure_electron_runtime(project_dir: Path | None = None) -> ElectronLaunchSpec:
    resolved_dir = (project_dir or _project_dir()).resolve()
    try:
        return resolve_launch_spec(project_dir=resolved_dir)
    except ElectronRuntimeNotFoundError:
        npm_binary = _npm_command()
        logger.info(f"Electron runtime not found, bootstrapping desktop dependencies in {resolved_dir}")
        try:
            await anyio.run_process([npm_binary, "install"], cwd=str(resolved_dir), env=dict(os.environ))
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to install Electron dependencies in {resolved_dir}: {exc!r}") from exc
        return resolve_launch_spec(project_dir=resolved_dir)


async def run_desktop(ui_url: str) -> None:
    spec = await ensure_electron_runtime()
    command = [*spec.command, f"--url={ui_url}"]
    logger.info(f"Launching Electron desktop shell: {' '.join(command)}")
    process = await anyio.open_process(command, cwd=spec.cwd)
    try:
        return_code = await process.wait()
    except BaseException:
        with anyio.CancelScope(shield=True):
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                await process.wait()
        raise
    if return_code != 0:
        raise RuntimeError(f"Electron exited with code {return_code}.")
