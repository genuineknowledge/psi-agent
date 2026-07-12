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
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import anyio
from aiohttp import web
from loguru import logger


async def serve_app(
    app: web.Application,
    addr: str,
    *,
    wait_func: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """Run an aiohttp application on the given address with shielded cleanup.

    If ``wait_func`` is provided, it is awaited after the server starts.
    Otherwise, it sleeps forever until cancelled.
    """
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, addr)
        await site.start()
    except Exception:
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    try:
        if wait_func is not None:
            await wait_func()
        else:
            await anyio.sleep_forever()
    finally:
        with anyio.CancelScope(shield=True):
            await runner.cleanup()


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
        logger.debug(f"Resolved transport: addr={addr!r} → TCP endpoint={endpoint!r}")
    elif addr.startswith("\\\\.\\pipe\\"):
        connector = aiohttp.NamedPipeConnector(path=addr)
        endpoint = f"http://localhost{path_prefix}"
        logger.debug(f"Resolved transport: addr={addr!r} → Named Pipe endpoint={endpoint!r}")
    else:
        connector = aiohttp.UnixConnector(path=addr)
        endpoint = f"http://localhost{path_prefix}"
        logger.debug(f"Resolved transport: addr={addr!r} → Unix socket endpoint={endpoint!r}")
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
        logger.debug(f"Creating TCP site: {host}:{port}")
        return web.TCPSite(runner, host, port)
    if addr.startswith("\\\\.\\pipe\\"):
        logger.debug(f"Creating Named Pipe site: {addr}")
        return web.NamedPipeSite(runner, addr)
    logger.debug(f"Creating Unix site: {addr}")
    return web.UnixSite(runner, addr)
