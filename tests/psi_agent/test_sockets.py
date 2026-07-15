from __future__ import annotations

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
async def test_resolve_complete_endpoint_is_not_duplicated() -> None:
    connector, endpoint = resolve_connector_and_endpoint("https://api.example.com/v1/chat/completions")
    try:
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
async def test_resolve_unix_socket() -> None:
    connector, endpoint = resolve_connector_and_endpoint("/tmp/x.sock")
    try:
        assert isinstance(connector, UnixConnector)
        assert endpoint == "http://localhost/chat/completions"
    finally:
        await connector.close()


@pytest.mark.anyio
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
async def test_create_site_unix() -> None:
    runner = web.AppRunner(web.Application())
    await runner.setup()
    try:
        site = create_site(runner, "/tmp/some.sock")
        assert isinstance(site, web.UnixSite)
    finally:
        await runner.cleanup()
