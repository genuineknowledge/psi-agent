from __future__ import annotations

import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent.gateway.desktop import _routes as desktop_routes
from psi_agent.gateway.desktop._routes import register_desktop_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager


async def _start_app_on_free_port(app: web.Application) -> tuple[str, web.AppRunner]:
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    return f"http://127.0.0.1:{port}", runner


@pytest.mark.anyio
async def test_spa_directory_routes_redirect_not_403(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``add_static(..., show_index=False)`` before redirects → 403 on /spa-v2/."""
    spa_root = anyio.Path(str(tmp_path))
    spa_dist = spa_root / "spa" / "dist"
    spa_v2_dist = spa_root / "spa-v2" / "dist"
    await spa_dist.mkdir(parents=True)
    await spa_v2_dist.mkdir(parents=True)
    index = "<html><head><title>__GATEWAY_APP_NAME__</title></head></html>"
    await (spa_dist / "index.html").write_text(index, encoding="utf-8")
    await (spa_v2_dist / "index.html").write_text(index, encoding="utf-8")

    # A7: ``_gateway_spa_root`` 随 register_desktop_routes 搬进 desktop/_routes.py。
    monkeypatch.setattr(desktop_routes, "_gateway_spa_root", lambda: spa_root)

    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim = AIManager(_prefix="spa-route-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="spa-route-test", _tg=tg)
    app = await register_desktop_routes(
        await create_core_app(aim, sm, TitleManager()),
        app_name="Haitun Agent",
    )
    base_url, runner = await _start_app_on_free_port(app)
    timeout = ClientTimeout(total=10)
    try:
        async with ClientSession(timeout=timeout) as session:
            for path, location in (
                ("/spa-v2/", "/spa-v2/index.html"),
                ("/spa-v2", "/spa-v2/index.html"),
                ("/", "/spa-v2/index.html"),
                ("/spa/", "/spa/index.html"),
                ("/spa", "/spa/index.html"),
            ):
                async with session.get(f"{base_url}{path}", allow_redirects=False) as resp:
                    assert resp.status == 302, f"{path} → {resp.status}"
                    assert resp.headers.get("Location") == location

            async with session.get(f"{base_url}/spa-v2/index.html") as resp:
                assert resp.status == 200
                body = await resp.text()
                assert "Haitun Agent" in body
    finally:
        await runner.cleanup()
        await tg.__aexit__(None, None, None)
