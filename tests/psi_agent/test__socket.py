from __future__ import annotations

import socket
import sys

import aiohttp
import anyio
import pytest
from aiohttp import web

from psi_agent._socket import (
    _DEFAULT_POLL_INTERVAL,
    _DEFAULT_SOCKET_TIMEOUT,
    _SOCKET_ACCEPT_GRACE,
    _check_ready,
    _detect_transport,
    create_site,
    resolve_connector_and_endpoint,
    wait_for_socket,
)


class TestDetectTransport:
    def test_unix_bare_path(self):
        assert _detect_transport("/tmp/foo.sock") == "unix"

    def test_unix_relative_path(self):
        assert _detect_transport("./relative/path.sock") == "unix"

    def test_tcp_http(self):
        assert _detect_transport("http://localhost:8080") == "tcp"

    def test_tcp_https(self):
        assert _detect_transport("https://api.example.com/v1") == "tcp"

    def test_pipe_windows(self):
        assert _detect_transport(r"\\.\pipe\psi-call-test") == "pipe"

    def test_empty_string(self):
        assert _detect_transport("") == "unix"


class TestCheckReady:
    @pytest.mark.anyio
    async def test_unix_socket_ready(self, tmp_path):
        sock_path = str(tmp_path / "ready.sock")
        app = web.Application()
        app.router.add_post("/chat/completions", lambda r: web.Response())
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.UnixSite(runner, sock_path)
        await site.start()
        await anyio.sleep(0.1)
        assert await _check_ready("unix", sock_path)
        await runner.cleanup()

    @pytest.mark.anyio
    async def test_unix_socket_not_ready(self, tmp_path):
        sock_path = str(tmp_path / "nonexistent.sock")
        assert not await _check_ready("unix", sock_path)

    @pytest.mark.anyio
    async def test_tcp_ready(self):
        app = web.Application()
        app.router.add_post("/chat/completions", lambda r: web.Response())
        runner = web.AppRunner(app)
        await runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        site = web.SockSite(runner, sock)
        await site.start()
        await anyio.sleep(0.1)
        assert await _check_ready("tcp", f"http://127.0.0.1:{port}")
        await runner.cleanup()

    @pytest.mark.anyio
    async def test_tcp_not_ready(self):
        assert not await _check_ready("tcp", "http://127.0.0.1:1")

    @pytest.mark.anyio
    async def test_pipe_not_ready_on_linux(self):
        result = await _check_ready("pipe", r"\\.\pipe\nonexistent")
        assert not result

    @pytest.mark.anyio
    async def test_unknown_transport(self):
        assert not await _check_ready("unknown", "/tmp/foo.sock")


class TestWaitForSocket:
    @pytest.mark.anyio
    async def test_wait_success_unix(self, tmp_path):
        sock_path = str(tmp_path / "wait_ok.sock")

        async def delayed_start():
            await anyio.sleep(0.2)
            app = web.Application()
            app.router.add_post("/chat/completions", lambda r: web.Response())
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.UnixSite(runner, sock_path)
            await site.start()

        async with anyio.create_task_group() as tg:
            tg.start_soon(delayed_start)
            await wait_for_socket(sock_path, max_wait=5.0, poll_interval=0.05)

    @pytest.mark.anyio
    async def test_wait_timeout(self):
        with pytest.raises(TimeoutError, match="did not become ready"):
            await wait_for_socket("/tmp/nonexistent-test.sock", max_wait=0.3, poll_interval=0.05)

    @pytest.mark.anyio
    async def test_wait_tcp_success(self):
        app = web.Application()
        app.router.add_post("/chat/completions", lambda r: web.Response())
        runner = web.AppRunner(app)
        await runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        site = web.SockSite(runner, sock)
        await site.start()
        await wait_for_socket(f"http://127.0.0.1:{port}", max_wait=5.0, poll_interval=0.05)
        await runner.cleanup()


class TestResolveConnectorAndEndpoint:
    @pytest.mark.anyio
    async def test_unix_socket(self):
        connector, endpoint = resolve_connector_and_endpoint("/tmp/foo.sock")
        assert isinstance(connector, aiohttp.UnixConnector)
        assert endpoint == "http://localhost/chat/completions"

    @pytest.mark.anyio
    async def test_unix_socket_custom_prefix(self):
        _, endpoint = resolve_connector_and_endpoint("/tmp/foo.sock", path_prefix="/v1/chat")
        assert endpoint == "http://localhost/v1/chat"

    @pytest.mark.anyio
    async def test_tcp_http(self):
        connector, endpoint = resolve_connector_and_endpoint("http://localhost:8080")
        assert isinstance(connector, aiohttp.TCPConnector)
        assert endpoint == "http://localhost:8080/chat/completions"

    @pytest.mark.anyio
    async def test_tcp_https(self):
        connector, endpoint = resolve_connector_and_endpoint("https://api.example.com/v1")
        assert isinstance(connector, aiohttp.TCPConnector)
        assert endpoint == "https://api.example.com/v1/chat/completions"

    @pytest.mark.skipif(sys.platform != "win32", reason="NamedPipeConnector requires Windows")
    @pytest.mark.anyio
    async def test_named_pipe(self):
        connector, endpoint = resolve_connector_and_endpoint(r"\\.\pipe\test")
        assert isinstance(connector, aiohttp.NamedPipeConnector)
        assert endpoint == "http://localhost/chat/completions"


class TestCreateSite:
    @pytest.mark.anyio
    async def test_create_unix_site(self, tmp_path):
        sock_path = str(tmp_path / "create.sock")
        app = web.Application()
        app.router.add_post("/", lambda r: web.Response())
        runner = web.AppRunner(app)
        await runner.setup()
        site = create_site(runner, sock_path)
        assert isinstance(site, web.UnixSite)
        await runner.cleanup()

    @pytest.mark.anyio
    async def test_create_tcp_site(self):
        app = web.Application()
        app.router.add_post("/", lambda r: web.Response())
        runner = web.AppRunner(app)
        await runner.setup()
        site = create_site(runner, "http://127.0.0.1:0")
        assert isinstance(site, web.TCPSite)
        await runner.cleanup()


class TestConstants:
    def test_default_socket_timeout(self):
        assert _DEFAULT_SOCKET_TIMEOUT == 10.0

    def test_default_poll_interval(self):
        assert _DEFAULT_POLL_INTERVAL == 0.05

    def test_socket_accept_grace(self):
        assert _SOCKET_ACCEPT_GRACE == 0.3
