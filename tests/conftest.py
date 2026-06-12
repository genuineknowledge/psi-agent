from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import _pytest.pathlib
import _pytest.tmpdir
import aiohttp
import anyio
import pytest
from aiohttp import TCPConnector, web
from yarl import URL

from psi_agent.net import cleanup_endpoint_sidecar, make_server_site, read_endpoint_sidecar

_IS_WINDOWS = os.name == "nt"
_TEST_TEMP_ROOT = (
    Path(__file__).resolve().parents[1] / ".pytest-local"
    if _IS_WINDOWS
    else Path(os.environ.get("PSI_TEST_TEMP_ROOT", "/tmp/psi-agent-pytest"))
)

_TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)

if _IS_WINDOWS:
    os.environ["TEMP"] = str(_TEST_TEMP_ROOT)
    os.environ["TMP"] = str(_TEST_TEMP_ROOT)
    os.environ["TMPDIR"] = str(_TEST_TEMP_ROOT)
    tempfile.tempdir = str(_TEST_TEMP_ROOT)
    os.environ.setdefault("UV_CACHE_DIR", str(_TEST_TEMP_ROOT / "uv-cache"))


def _skip_cleanup_dead_symlinks(root: Path) -> None:
    _ = root


def _patch_attr(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


if _IS_WINDOWS:

    @pytest.fixture
    def tmp_path(request: pytest.FixtureRequest) -> Generator[Path]:
        safe_name = re.sub(r"[\W]", "_", request.node.name)[:40]
        path = _TEST_TEMP_ROOT / f"{safe_name}-{uuid.uuid4().hex}"
        path.mkdir(mode=0o755)
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)


def pytest_configure(config: Any) -> None:
    config.option.basetemp = str(_TEST_TEMP_ROOT / f"run-{uuid.uuid4().hex}")
    if _IS_WINDOWS:
        config.option.cacheclear = True
        _patch_attr(_pytest.pathlib, "cleanup_dead_symlinks", _skip_cleanup_dead_symlinks)
        _patch_attr(_pytest.tmpdir, "cleanup_dead_symlinks", _skip_cleanup_dead_symlinks)


if _IS_WINDOWS:
    _original_open_process = anyio.open_process
    _original_request = aiohttp.ClientSession._request

    def _kill_process_tree(pid: int) -> None:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    class WindowsProcessTree:
        def __init__(self, process: Any) -> None:
            self._process = process

        @property
        def pid(self) -> int:
            return self._process.pid

        @property
        def returncode(self) -> int | None:
            return self._process.returncode

        @property
        def stdin(self) -> Any:
            return self._process.stdin

        @property
        def stdout(self) -> Any:
            return self._process.stdout

        @property
        def stderr(self) -> Any:
            return self._process.stderr

        async def wait(self) -> int:
            return await self._process.wait()

        async def aclose(self) -> None:
            await self._process.aclose()

        def send_signal(self, sig: signal.Signals) -> None:
            sigkill = getattr(signal, "SIGKILL", None)
            if sig == signal.SIGTERM or (sigkill is not None and sig == sigkill):
                _kill_process_tree(self.pid)
                return
            self._process.send_signal(sig)

        def terminate(self) -> None:
            _kill_process_tree(self.pid)

        def kill(self) -> None:
            _kill_process_tree(self.pid)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._process, name)

    async def _open_process_with_tree_cleanup(*args: Any, **kwargs: Any) -> WindowsProcessTree:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs.setdefault("stdout", subprocess.DEVNULL)
        kwargs.setdefault("stderr", subprocess.DEVNULL)
        process = await _original_open_process(*args, **kwargs)
        return WindowsProcessTree(process)

    class WindowsUnixSite:
        def __init__(self, runner: web.AppRunner, path: str, *args: Any, **kwargs: Any) -> None:
            _ = args
            _ = kwargs
            self._runner = runner
            self._path = path
            self._site: web.BaseSite | None = None

        @property
        def name(self) -> str:
            return self._site.name if self._site is not None else self._path

        async def start(self) -> None:
            self._site = await make_server_site(self._runner, self._path)
            await self._site.start()

        async def stop(self) -> None:
            if self._site is not None:
                await self._site.stop()
            cleanup_endpoint_sidecar(self._path)

    def _windows_unix_connector(*, path: str, **kwargs: Any) -> TCPConnector:
        endpoint = read_endpoint_sidecar(path) or "http://127.0.0.1:9/v1"
        connector = TCPConnector(
            ssl=kwargs.get("ssl", True),
            force_close=kwargs.get("force_close", False),
            limit=kwargs.get("limit", 100),
            limit_per_host=kwargs.get("limit_per_host", 0),
        )
        _patch_attr(connector, "_psi_agent_sidecar_endpoint", endpoint)
        return connector

    async def _request_with_sidecar(self: aiohttp.ClientSession, method: str, str_or_url: Any, **kwargs: Any) -> Any:
        endpoint = getattr(self.connector, "_psi_agent_sidecar_endpoint", None)
        if endpoint:
            url = URL(str_or_url)
            if url.host == "localhost":
                path = url.raw_path
                if path == "/v1":
                    tail = ""
                elif path.startswith("/v1/"):
                    tail = path.removeprefix("/v1")
                else:
                    tail = path
                rewritten = endpoint.rstrip("/") + tail
                if url.raw_query_string:
                    rewritten += "?" + url.raw_query_string
                str_or_url = rewritten
        return await _original_request(self, method, str_or_url, **kwargs)

    _patch_attr(web, "UnixSite", WindowsUnixSite)
    _patch_attr(aiohttp, "UnixConnector", _windows_unix_connector)
    _patch_attr(aiohttp.connector, "UnixConnector", _windows_unix_connector)
    _patch_attr(aiohttp.ClientSession, "_request", _request_with_sidecar)
    _patch_attr(anyio, "open_process", _open_process_with_tree_cleanup)
