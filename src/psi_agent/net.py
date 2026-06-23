from __future__ import annotations

import contextlib
import os
import socket
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import anyio
from aiohttp import ClientSession, ClientTimeout, TCPConnector, UnixConnector, web
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver


class HostAliasResolver(AbstractResolver):
    """Resolve selected hostnames to explicit IPs from PSI_AGENT_HOST_ALIASES."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self._aliases = {host.lower(): ip for host, ip in aliases.items()}
        self._default: AbstractResolver = DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        alias = self._aliases.get(host.lower())
        if not alias:
            return await self._default.resolve(host=host, port=port, family=family)
        return [
            cast(
                ResolveResult,
                {
                    "hostname": host,
                    "host": alias,
                    "port": port,
                    "family": socket.AF_INET6 if ":" in alias else socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                },
            )
        ]

    async def close(self) -> None:
        await self._default.close()


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


def parse_host_aliases(raw: str | None = None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in (raw if raw is not None else os.environ.get("PSI_AGENT_HOST_ALIASES", "")).split(","):
        if not item.strip() or "=" not in item:
            continue
        host, ip = item.split("=", 1)
        host = host.strip().lower()
        ip = ip.strip()
        if host and ip:
            aliases[host] = ip
    return aliases


def make_tcp_connector(*, ssl: bool | None = None) -> TCPConnector:
    connector_ssl = True if ssl is None else ssl
    aliases = parse_host_aliases()
    if aliases:
        return TCPConnector(ssl=connector_ssl, resolver=HostAliasResolver(aliases))
    return TCPConnector(ssl=connector_ssl)


def make_client_session(endpoint: str, *, timeout: ClientTimeout | None = None) -> tuple[ClientSession, str]:
    resolved = resolve_client_endpoint(endpoint)
    if is_tcp_endpoint(resolved):
        connector = make_tcp_connector(ssl=resolved.startswith("https://"))
        url = resolved.rstrip("/") + "/chat/completions"
    else:
        connector = UnixConnector(path=resolved)
        url = "http://localhost/v1/chat/completions"
    return ClientSession(connector=connector, timeout=timeout or ClientTimeout(total=None)), url
