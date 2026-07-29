from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.gateway._state import GatewayState


@pytest.mark.anyio
async def test_state_save_and_load_roundtrip(tmp_path: Path) -> None:
    state = GatewayState(
        _path=anyio.Path(tmp_path) / "state" / "latest.json",
        _legacy_path=anyio.Path(tmp_path) / "legacy" / "latest.json",
    )

    await state.save(
        ais=[
            {
                "id": "a1",
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "sk-abc",
                "base_url": "https://api.oai.com",
            },
            {"id": "a2", "provider": "anthropic", "model": "claude-3", "api_key": "sk-xyz", "base_url": ""},
        ],
        sessions=[
            {"id": "s1", "ai_id": "a1", "workspace": "/tmp/ws"},
        ],
        titles=[{"id": "s1", "title": "Hello Chat"}],
    )

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 2
    assert snapshot["ais"][0]["provider"] == "openai"
    assert snapshot["ais"][0]["api_key"] == "sk-abc"
    assert snapshot["ais"][1]["base_url"] == ""
    assert len(snapshot["sessions"]) == 1
    assert snapshot["sessions"][0]["backend_type"] == "ai"
    assert snapshot["sessions"][0]["backend_id"] == "a1"
    assert snapshot["sessions"][0]["workspace"] == "/tmp/ws"
    assert snapshot["titles"] == [{"id": "s1", "title": "Hello Chat"}]


@pytest.mark.anyio
async def test_state_load_missing_file_returns_empty(tmp_path: Path) -> None:
    state = GatewayState(
        _path=anyio.Path(tmp_path) / "nonexistent" / "latest.json",
        _legacy_path=anyio.Path(tmp_path) / "also-missing" / "latest.json",
    )
    snapshot = await state.load()
    assert snapshot == {"ais": [], "routers": [], "sessions": [], "titles": []}


@pytest.mark.anyio
async def test_state_overwrite_on_save(tmp_path: Path) -> None:
    state = GatewayState(
        _path=anyio.Path(tmp_path) / "state" / "latest.json",
        _legacy_path=anyio.Path(tmp_path) / "legacy" / "latest.json",
    )

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k1", "base_url": ""}],
        sessions=[],
        titles=[],
    )
    await state.save(
        ais=[{"id": "a2", "provider": "x", "model": "y", "api_key": "k2", "base_url": ""}],
        sessions=[],
        titles=[],
    )

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 1
    assert snapshot["ais"][0]["id"] == "a2"


@pytest.mark.anyio
async def test_state_save_writes_history_file(tmp_path: Path) -> None:
    state = GatewayState(
        _path=anyio.Path(tmp_path) / "state" / "latest.json",
        _history_dir=anyio.Path(tmp_path) / "state",
        _legacy_path=anyio.Path(tmp_path) / "legacy" / "latest.json",
        _startup_ts="20260703-120000",
    )

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k", "base_url": ""}],
        sessions=[],
        titles=[],
    )

    assert await (anyio.Path(tmp_path) / "state" / "latest.json").exists()
    assert await (anyio.Path(tmp_path) / "state" / "20260703-120000.json").exists()


@pytest.mark.anyio
async def test_state_no_history_file_without_startup_ts(tmp_path: Path) -> None:
    state = GatewayState(
        _path=anyio.Path(tmp_path) / "state" / "latest.json",
        _legacy_path=anyio.Path(tmp_path) / "legacy" / "latest.json",
        _startup_ts="",
    )

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k", "base_url": ""}],
        sessions=[],
        titles=[],
    )

    assert await (anyio.Path(tmp_path) / "state" / "latest.json").exists()
    assert not await (anyio.Path(tmp_path) / "state" / "20260703-120000.json").exists()


@pytest.mark.anyio
async def test_state_from_appdata_writes_under_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    state = await GatewayState.from_appdata(str(appdata))
    state._legacy_path = anyio.Path(tmp_path) / "legacy" / "latest.json"
    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k", "base_url": ""}],
        sessions=[],
        titles=[],
    )
    assert await (anyio.Path(str(appdata)) / "state" / "latest.json").is_file()


@pytest.mark.anyio
async def test_state_dual_read_legacy_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "appdata"
    legacy_dir = tmp_path / "cwd-state"
    await anyio.Path(str(legacy_dir)).mkdir()
    legacy = anyio.Path(str(legacy_dir)) / "latest.json"
    await legacy.write_text(
        '{"ais":[{"id":"legacy","provider":"o","model":"m","api_key":"k","base_url":""}],'
        '"routers":[],"sessions":[],"titles":[]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    state = await GatewayState.from_appdata(str(appdata))
    state._legacy_path = legacy
    snapshot = await state.load()
    assert snapshot["ais"][0]["id"] == "legacy"
    await state.save(ais=snapshot["ais"], sessions=[], titles=[])
    assert await (anyio.Path(str(appdata)) / "state" / "latest.json").is_file()


@pytest.mark.anyio
async def test_state_appdata_wins_over_legacy(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    await anyio.Path(str(appdata / "state")).mkdir(parents=True)
    await (anyio.Path(str(appdata)) / "state" / "latest.json").write_text(
        '{"ais":[{"id":"new","provider":"o","model":"m","api_key":"k","base_url":""}],'
        '"routers":[],"sessions":[],"titles":[]}',
        encoding="utf-8",
    )
    legacy = anyio.Path(str(tmp_path / "legacy.json"))
    await legacy.write_text(
        '{"ais":[{"id":"old","provider":"o","model":"m","api_key":"k","base_url":""}],'
        '"routers":[],"sessions":[],"titles":[]}',
        encoding="utf-8",
    )
    state = await GatewayState.from_appdata(str(appdata))
    state._legacy_path = legacy
    snapshot = await state.load()
    assert snapshot["ais"][0]["id"] == "new"
