from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web

from psi_agent.gateway import Gateway


class _FakeSite:
    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True


class _UnexpectedTray:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("GatewayTray should not be created in desktop mode.")


@pytest.mark.anyio
async def test_gateway_desktop_launches_electron_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    launched_urls: list[str] = []
    opened_urls: list[str] = []
    favicon_paths: list[str | None] = []

    async def fake_create_app(_aim: Any, _sm: Any, favicon_path: str | None = None) -> web.Application:
        favicon_paths.append(favicon_path)
        return web.Application()

    async def fake_run_desktop(ui_url: str) -> None:
        launched_urls.append(ui_url)

    def fake_open(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setattr("psi_agent.gateway._random_port", lambda: 43123)
    monkeypatch.setattr("psi_agent.gateway.create_app", fake_create_app)
    monkeypatch.setattr("psi_agent.gateway.create_site", lambda _runner, _addr: _FakeSite())
    monkeypatch.setattr("psi_agent.gateway.run_desktop", fake_run_desktop)
    monkeypatch.setattr("psi_agent.gateway.webbrowser.open", fake_open)
    monkeypatch.setattr("psi_agent.gateway.GatewayTray", _UnexpectedTray)

    await Gateway(desktop=True, tray="icon.png").run()

    assert launched_urls == ["http://127.0.0.1:43123"]
    assert opened_urls == []
    assert favicon_paths == ["icon.png"]
