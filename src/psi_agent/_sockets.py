"""Shared socket utilities for resolving transport addresses and readiness checks.

All components (AI, Session, Channel) use the same prefix-based
detection convention:
- bare filesystem path → Unix socket
- http(s)://host:port  → TCP
- \\\\.\\pipe\\name → Windows Named Pipe

Named pipe support requires aiohttp >= 3.6.0 and the Proactor
event loop (Windows only).
"""

from __future__ import annotations

import time
import urllib.parse

import aiohttp
import anyio
from aiohttp import web

_DEFAULT_SOCKET_TIMEOUT: float = 10.0  # max seconds to wait for socket to appear
_DEFAULT_POLL_INTERVAL: float = 0.05   # seconds between existence checks
_SOCKET_ACCEPT_GRACE: float = 0.3      # extra wait after socket detected, for aiohttp accept()


def _detect_transport(address: str) -> str:
    """Classify an address into one of the three supported transport types."""
    if address.startswith(("http://", "https://")):
        return "tcp"
    if address.startswith("\\\\.\\pipe\\"):
        return "pipe"
    return "unix"


async def _check_ready(transport: str, address: str) -> bool:
    """Return ``True`` when the server at ``address`` is ready for connections."""
    match transport:
        case "unix":
            if not await anyio.Path(address).exists():
                return False
            await anyio.sleep(_SOCKET_ACCEPT_GRACE)
            return True

        case "tcp":
            parsed = urllib.parse.urlparse(address)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            try:
                _, writer = await anyio.connect_tcp(host, port)
                await writer.aclose()
                return True
            except (OSError, ConnectionError):
                return False

        case "pipe":
            try:
                if not await anyio.Path(address).exists():
                    return False
                await anyio.sleep(_SOCKET_ACCEPT_GRACE)
                return True
            except Exception:
                return False

        case _:
            return False


async def wait_for_socket(
    address: str,
    *,
    max_wait: float = _DEFAULT_SOCKET_TIMEOUT,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> None:
    """Wait until a server socket is ready to accept connections.

    Supports three transport types using the same prefix detection:

    * bare filesystem path → Unix domain socket
    * ``http(s)://host:port`` → TCP
    * ``\\\\\\\\.\\\\pipe\\\\\\name`` → Windows Named Pipe
    """
    transport = _detect_transport(address)

    deadline = time.monotonic() + max_wait
    while True:
        ready = await _check_ready(transport, address)
        if ready:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"Server at {address} did not become ready within {max_wait}s")
        await anyio.sleep(poll_interval)


def resolve_connector_and_endpoint(
    addr: str,
    *,
    path_prefix: str = "/chat/completions",
) -> tuple[aiohttp.BaseConnector, str]:
    """Client side: resolve a transport address to (connector, HTTP endpoint).

    ``path_prefix`` is appended to the URL path for TCP endpoints.  Unix
    socket endpoints always use ``http://localhost/{path_prefix}``.
    """
    if addr.startswith(("http://", "https://")):
        connector = aiohttp.TCPConnector(ssl=addr.startswith("https://"))
        endpoint = addr.rstrip("/") + path_prefix
    elif addr.startswith("\\\\.\\pipe\\"):
        connector = aiohttp.NamedPipeConnector(path=addr)
        endpoint = f"http://localhost{path_prefix}"
    else:
        connector = aiohttp.UnixConnector(path=addr)
        endpoint = f"http://localhost{path_prefix}"
    return connector, endpoint


def create_site(
    runner: web.AppRunner,
    addr: str,
) -> web.BaseSite:
    """Server side: create an aiohttp site for the given address.

    ``addr`` can be a Unix socket path, a ``http(s)://host:port`` URL,
    or a Windows named pipe path (``\\\\.\\pipe\\name``).
    """
    if addr.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(addr)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8080
        return web.TCPSite(runner, host, port)
    if addr.startswith("\\\\.\\pipe\\"):
        return web.NamedPipeSite(runner, addr)
    return web.UnixSite(runner, addr)
