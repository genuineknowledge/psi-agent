from __future__ import annotations

import contextlib
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector, UnixConnector, web


def is_tcp_endpoint(endpoint: str) -> bool:
    return endpoint.startswith(("http://", "https://"))


def should_use_windows_tcp_sidecar(endpoint: str) -> bool:
    return os.name == "nt" and not is_tcp_endpoint(endpoint)


def read_endpoint_sidecar(path: str) -> str | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw if is_tcp_endpoint(raw) else None


def resolve_client_endpoint(endpoint: str) -> str:
    if is_tcp_endpoint(endpoint):
        return endpoint
    if should_use_windows_tcp_sidecar(endpoint):
        sidecar = read_endpoint_sidecar(endpoint)
        if sidecar:
            return sidecar
    return endpoint


def _make_bound_localhost_socket() -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    return sock, f"http://127.0.0.1:{port}/v1"


async def make_server_site(runner: web.AppRunner, endpoint: str) -> web.BaseSite:
    if is_tcp_endpoint(endpoint):
        parsed = urlparse(endpoint)
        if not parsed.hostname or parsed.port is None:
            raise ValueError(f"TCP endpoint URL must include host and port: {endpoint}")
        return web.TCPSite(runner, parsed.hostname, parsed.port)

    if should_use_windows_tcp_sidecar(endpoint):
        sock, advertised = _make_bound_localhost_socket()
        path = Path(endpoint)
        await anyio.Path(str(path.parent)).mkdir(parents=True, exist_ok=True)
        await anyio.Path(str(path)).write_text(advertised, encoding="utf-8")
        return web.SockSite(runner, sock)

    return web.UnixSite(runner, endpoint)


def cleanup_endpoint_sidecar(endpoint: str) -> None:
    if not should_use_windows_tcp_sidecar(endpoint):
        return
    with contextlib.suppress(OSError):
        Path(endpoint).unlink()


def make_client_session(endpoint: str, *, timeout: ClientTimeout | None = None) -> tuple[ClientSession, str]:
    resolved = resolve_client_endpoint(endpoint)
    if is_tcp_endpoint(resolved):
        connector = TCPConnector(ssl=resolved.startswith("https://"))
        url = resolved.rstrip("/") + "/chat/completions"
    else:
        connector = UnixConnector(path=resolved)
        url = "http://localhost/v1/chat/completions"
    return ClientSession(connector=connector, timeout=timeout or ClientTimeout(total=None)), url
