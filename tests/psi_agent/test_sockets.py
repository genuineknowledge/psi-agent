from __future__ import annotations

import sys

import aiohttp
import pytest
from aiohttp import TCPConnector, UnixConnector, web

from psi_agent._sockets import create_site, resolve_connector_and_endpoint


@pytest.mark.anyio
async def test_resolve_http_tcp() -> None:
    connector, endpoint = resolve_connector_and_endpoint("http://example.com:8080")
    try:
        assert isinstance(connector, TCPConnector)
        assert endpoint == "http://example.com:8080/chat/completions"
    finally:
        await connector.close()


@pytest.mark.anyio
async def test_resolve_https_tcp_keeps_base_path() -> None:
    connector, endpoint = resolve_connector_and_endpoint("https://api.example.com/v1")
    try:
        assert isinstance(connector, TCPConnector)
        assert endpoint == "https://api.example.com/v1/chat/completions"
    finally:
        await connector.close()


@pytest.mark.anyio
async def test_resolve_strips_trailing_slash() -> None:
    connector, endpoint = resolve_connector_and_endpoint("http://h:1/")
    try:
        assert endpoint == "http://h:1/chat/completions"
    finally:
        await connector.close()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets are unsupported on Windows")
async def test_resolve_unix_socket() -> None:
    connector, endpoint = resolve_connector_and_endpoint("/tmp/x.sock")
    try:
        assert isinstance(connector, UnixConnector)
        assert endpoint == "http://localhost/chat/completions"
    finally:
        await connector.close()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets are unsupported on Windows")
async def test_resolve_honours_custom_path_prefix() -> None:
    connector, endpoint = resolve_connector_and_endpoint("/tmp/x.sock", path_prefix="/v1/messages")
    try:
        assert endpoint == "http://localhost/v1/messages"
    finally:
        await connector.close()


@pytest.mark.anyio
async def test_create_site_tcp() -> None:
    runner = web.AppRunner(web.Application())
    await runner.setup()
    try:
        site = create_site(runner, "http://127.0.0.1:9999")
        assert isinstance(site, web.TCPSite)
    finally:
        await runner.cleanup()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets are unsupported on Windows")
async def test_create_site_unix() -> None:
    runner = web.AppRunner(web.Application())
    await runner.setup()
    try:
        site = create_site(runner, "/tmp/some.sock")
        assert isinstance(site, web.UnixSite)
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_resolve_unix_path_on_windows_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # On Windows a Unix-socket path must not fall through to UnixConnector —
    # asyncio has no create_unix_connection there and aiohttp would otherwise
    # raise a bare NotImplementedError deep in the connect path. A single-
    # backslash pipe path is the common mis-quoted form that lands here.
    monkeypatch.setattr("psi_agent._sockets.sys.platform", "win32")
    with pytest.raises(ValueError, match="named-pipe"):
        resolve_connector_and_endpoint("/tmp/x.sock")
    with pytest.raises(ValueError, match="named-pipe"):
        resolve_connector_and_endpoint("\\.\\pipe\\psi\\channels\\abc")


@pytest.mark.anyio
@pytest.mark.skipif(
    sys.platform != "win32",
    reason="aiohttp.NamedPipeConnector requires a Windows ProactorEventLoop to construct",
)
async def test_resolve_named_pipe_on_windows_ok() -> None:
    # A well-formed named-pipe address must take the pipe branch (not the Unix
    # guard) and build a NamedPipeConnector. Only meaningful on Windows, where
    # NamedPipeConnector can actually be instantiated.
    connector, endpoint = resolve_connector_and_endpoint("\\\\.\\pipe\\psi\\channels\\abc")
    try:
        assert isinstance(connector, aiohttp.NamedPipeConnector)
        assert endpoint == "http://localhost/chat/completions"
    finally:
        await connector.close()


@pytest.mark.anyio
async def test_create_site_unix_path_on_windows_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # `psi_agent._sockets.sys` *is* the global sys module, so patching its
    # platform is a process-wide mutation. Keep it scoped to the call under
    # test so aiohttp's own platform-dependent setup/cleanup still sees the
    # real platform.
    runner = web.AppRunner(web.Application())
    await runner.setup()
    try:
        with monkeypatch.context() as mp:
            mp.setattr("psi_agent._sockets.sys.platform", "win32")
            with pytest.raises(ValueError, match="named-pipe"):
                create_site(runner, "/tmp/some.sock")
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_resolve_named_pipe_off_windows_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mirror of the Windows/Unix-socket guard: named pipes need asyncio's
    # ProactorEventLoop, which is not even defined off Windows, so the pipe
    # branch must fail with an actionable ValueError rather than the
    # AttributeError aiohttp's own isinstance guard would raise.
    monkeypatch.setattr("psi_agent._sockets.sys.platform", "linux")
    with pytest.raises(ValueError, match="only available on Windows"):
        resolve_connector_and_endpoint("\\\\.\\pipe\\psi\\channels\\abc")


@pytest.mark.anyio
async def test_create_site_named_pipe_off_windows_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = web.AppRunner(web.Application())
    await runner.setup()
    try:
        with monkeypatch.context() as mp:
            mp.setattr("psi_agent._sockets.sys.platform", "darwin")
            with pytest.raises(ValueError, match="only available on Windows"):
                create_site(runner, "\\\\.\\pipe\\psi\\channels\\abc")
    finally:
        await runner.cleanup()
