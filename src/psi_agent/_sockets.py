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
from contextvars import Token
from typing import Any

import aiohttp
import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import trace_id_var


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


async def serve_app(app: web.Application, addr: str) -> None:
    """Unified server lifecycle manager for all components."""
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, addr)
        await site.start()
        logger.info(f"Serving on {addr}")
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down server on {addr}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()


@web.middleware
async def trace_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    """Propagate X-Trace-ID header to trace_id_var ContextVar."""
    trace_id = request.headers.get("X-Trace-ID", "-")
    token: Token[str] = trace_id_var.set(trace_id)
    try:
        return await handler(request)
    finally:
        trace_id_var.reset(token)
