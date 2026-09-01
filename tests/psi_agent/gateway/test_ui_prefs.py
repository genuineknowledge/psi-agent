from __future__ import annotations

import json
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent._appdata import appdata_ui_prefs_path
from psi_agent.gateway.desktop._routes import register_desktop_routes
from psi_agent.gateway.desktop._ui_prefs import UIPrefs
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager


def _prefs(tmp_path: Path) -> UIPrefs:
    return UIPrefs(_path=anyio.Path(tmp_path) / "ui-prefs.json")


@pytest.mark.anyio
async def test_survey_done_defaults_false_when_file_missing(tmp_path: Path) -> None:
    assert await _prefs(tmp_path).survey_done() is False


@pytest.mark.anyio
async def test_survey_done_roundtrip_survives_new_instance(tmp_path: Path) -> None:
    """The whole point: a fresh store (≈ next startup, new port) still sees the flag."""
    await _prefs(tmp_path).set_survey_done()
    assert await _prefs(tmp_path).survey_done() is True


@pytest.mark.anyio
async def test_set_survey_done_is_idempotent(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    await prefs.set_survey_done()
    await prefs.set_survey_done()
    assert await prefs.survey_done() is True


@pytest.mark.anyio
async def test_set_survey_done_false_clears_flag(tmp_path: Path) -> None:
    prefs = _prefs(tmp_path)
    await prefs.set_survey_done()
    await prefs.set_survey_done(False)
    assert await prefs.survey_done() is False


@pytest.mark.anyio
async def test_creates_parent_dir_on_first_write(tmp_path: Path) -> None:
    prefs = UIPrefs(_path=anyio.Path(tmp_path) / "nested" / "deeper" / "ui-prefs.json")
    await prefs.set_survey_done()
    assert await prefs.survey_done() is True


@pytest.mark.anyio
async def test_preserves_unrelated_keys(tmp_path: Path) -> None:
    """A future flag written by another caller must not be dropped on write."""
    path = anyio.Path(tmp_path) / "ui-prefs.json"
    await path.write_text('{"some_other_flag": true}', encoding="utf-8")
    await UIPrefs(_path=path).set_survey_done()
    data = json.loads(await path.read_text(encoding="utf-8"))
    assert data["some_other_flag"] is True
    assert data["survey_done"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["not json at all", "[1, 2, 3]", '"a string"', ""])
async def test_corrupt_file_degrades_to_false(tmp_path: Path, raw: str) -> None:
    """Corrupt prefs must not raise — worst case the popup shows once more."""
    path = anyio.Path(tmp_path) / "ui-prefs.json"
    await path.write_text(raw, encoding="utf-8")
    assert await UIPrefs(_path=path).survey_done() is False


@pytest.mark.anyio
async def test_corrupt_file_is_recoverable_by_write(tmp_path: Path) -> None:
    path = anyio.Path(tmp_path) / "ui-prefs.json"
    await path.write_text("{{{ broken", encoding="utf-8")
    prefs = UIPrefs(_path=path)
    await prefs.set_survey_done()
    assert await prefs.survey_done() is True


@pytest.mark.anyio
async def test_from_appdata_lands_at_expected_path(tmp_path: Path) -> None:
    prefs = await UIPrefs.from_appdata(str(tmp_path))
    await prefs.set_survey_done()
    assert await appdata_ui_prefs_path(str(tmp_path)).is_file()


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
async def test_survey_pref_routes(tmp_path: Path) -> None:
    """GET reports false → POST persists → GET reports true, over real HTTP."""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim = AIManager(_prefix="ui-prefs-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="ui-prefs-test", _tg=tg)
    # appdata=tmp_path so the test never touches the real user AppData.
    # UIPrefs 由 register_desktop_routes 从骨架记下的 appdata 建 —— /ui/prefs/* 是 ToC 专属。
    app = await register_desktop_routes(await create_core_app(aim, sm, TitleManager(), appdata=str(tmp_path)))
    base_url, runner = await _start_app_on_free_port(app)
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as session:
            async with session.get(f"{base_url}/ui/prefs/survey") as resp:
                assert resp.status == 200
                assert (await resp.json())["done"] is False

            async with session.post(f"{base_url}/ui/prefs/survey", json={"done": True}) as resp:
                assert resp.status == 200
                assert (await resp.json())["done"] is True

            async with session.get(f"{base_url}/ui/prefs/survey") as resp:
                assert (await resp.json())["done"] is True

            # Empty/garbage body still means "dismissed" — the only caller is dismiss.
            async with session.post(f"{base_url}/ui/prefs/survey", data="not json") as resp:
                assert resp.status == 200
                assert (await resp.json())["done"] is True
    finally:
        await runner.cleanup()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_survey_pref_survives_app_restart(tmp_path: Path) -> None:
    """Same AppData, new app instance on a new port — the flag must still read true.

    This is the regression the whole change exists for: ``localStorage`` failed
    here because the origin's port changes on every Gateway startup.
    """
    for expected in (False, True):
        tg = anyio.create_task_group()
        await tg.__aenter__()
        aim = AIManager(_prefix=f"ui-prefs-restart-{expected}", _tg=tg)
        sm = SessionManager(_aim=aim, _prefix=f"ui-prefs-restart-{expected}", _tg=tg)
        app = await register_desktop_routes(await create_core_app(aim, sm, TitleManager(), appdata=str(tmp_path)))
        base_url, runner = await _start_app_on_free_port(app)
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                async with session.get(f"{base_url}/ui/prefs/survey") as resp:
                    assert (await resp.json())["done"] is expected
                if not expected:
                    async with session.post(f"{base_url}/ui/prefs/survey", json={"done": True}) as resp:
                        assert resp.status == 200
        finally:
            await runner.cleanup()
            await tg.__aexit__(None, None, None)
