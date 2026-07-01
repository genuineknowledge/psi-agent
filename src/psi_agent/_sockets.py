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
import uuid
from collections.abc import Awaitable, Callable

import aiohttp
import anyio
from aiohttp import web
from loguru import logger


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


@web.middleware
async def trace_id_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Aiohttp middleware that extracts or generates a trace ID and sets it in the logger context."""
    trace_id = request.headers.get("X-Trace-ID") or uuid.uuid4().hex[:8]
    with logger.contextualize(trace_id=trace_id):
        return await handler(request)


async def serve_app(app: web.Application, addr: str, *, name: str = "Service") -> None:
    """Start an aiohttp application on the given address with shielded cleanup."""
    logger.info(f"Starting {name} on {addr}")
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, addr)
        await site.start()
        logger.info(f"{name} listening on {addr}")
        await anyio.sleep_forever()
    except Exception as e:
        if not isinstance(e, anyio.get_cancelled_exc_class()):
            logger.error(f"Failed to start {name} on {addr}: {e}")
        raise
    finally:
        logger.info(f"Shutting down {name} on {addr}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        logger.info(f"{name} shutdown complete on {addr}")
