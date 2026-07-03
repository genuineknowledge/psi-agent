from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._state import GatewayState


@pytest.mark.anyio
async def test_state_save_and_load_roundtrip(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "state" / "latest.json")

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
        titles={"s1": "Hello Chat"},
    )

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 2
    assert snapshot["ais"]["a1"]["provider"] == "openai"
    assert snapshot["ais"]["a1"]["api_key"] == "sk-abc"
    assert snapshot["ais"]["a2"]["base_url"] == ""
    assert len(snapshot["sessions"]) == 1
    assert snapshot["sessions"]["s1"]["ai_id"] == "a1"
    assert snapshot["sessions"]["s1"]["workspace"] == "/tmp/ws"
    assert snapshot["titles"] == {"s1": "Hello Chat"}


@pytest.mark.anyio
async def test_state_load_missing_file_returns_empty(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "nonexistent" / "latest.json")
    snapshot = await state.load()
    assert snapshot == {"ais": {}, "sessions": {}, "titles": {}}


@pytest.mark.anyio
async def test_state_overwrite_on_save(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "state" / "latest.json")

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k1", "base_url": ""}],
        sessions=[],
        titles={},
    )
    await state.save(
        ais=[{"id": "a2", "provider": "x", "model": "y", "api_key": "k2", "base_url": ""}],
        sessions=[],
        titles={},
    )

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 1
    assert "a2" in snapshot["ais"]
    assert "a1" not in snapshot["ais"]


@pytest.mark.anyio
async def test_state_save_writes_history_file(tmp_path: str) -> None:
    state = GatewayState(
        _path=anyio.Path(tmp_path) / "state" / "latest.json",
        _history_dir=anyio.Path(tmp_path) / "state",
        _startup_ts="20260703-120000",
    )

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k", "base_url": ""}],
        sessions=[],
        titles={},
    )

    assert await (anyio.Path(tmp_path) / "state" / "latest.json").exists()
    assert await (anyio.Path(tmp_path) / "state" / "20260703-120000.json").exists()


@pytest.mark.anyio
async def test_state_no_history_file_without_startup_ts(tmp_path: str) -> None:
    state = GatewayState(_path=anyio.Path(tmp_path) / "state" / "latest.json", _startup_ts="")

    await state.save(
        ais=[{"id": "a1", "provider": "o", "model": "m", "api_key": "k", "base_url": ""}],
        sessions=[],
        titles={},
    )

    assert await (anyio.Path(tmp_path) / "state" / "latest.json").exists()
    assert not await (anyio.Path(tmp_path) / "state" / "20260703-120000.json").exists()
