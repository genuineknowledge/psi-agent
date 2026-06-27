"""Shared socket utilities for resolving transport addresses.

All components (AI, Session, Channel) use the same prefix-based
detection convention:
- bare filesystem path → Unix socket
- http(s)://host:port  → TCP
- \\\\.\\pipe\\name → Windows Named Pipe

Named pipe support requires aiohttp >= 3.6.0 and the Proactor
event loop (Windows only).
"""

from __future__ import annotations

import urllib.parse

import aiohttp
import anyio
from aiohttp import web


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


async def serve_app(
    app: web.Application,
    addr: str,
) -> None:
    """Serve an aiohttp application on the given address and block until cancelled."""
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = create_site(runner, addr)
        await site.start()
        await anyio.sleep_forever()
    finally:
        with anyio.CancelScope(shield=True):
            await runner.cleanup()


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
