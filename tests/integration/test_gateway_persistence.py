from __future__ import annotations

import json
import sys

import anyio
import pytest

from psi_agent.gateway._manager import _socket_path
from psi_agent.gateway._state import GatewayState


@pytest.mark.anyio
async def test_gateway_state_persistence_roundtrip(tmp_path: str) -> None:
    state_path = anyio.Path(tmp_path) / "state" / "latest.json"
    state = GatewayState(_path=state_path)

    ais_data = {
        "a1": {
            "id": "a1",
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "sk-abc",
            "base_url": "https://api.oai.com",
        },
    }
    sessions_data = {
        "s1": {"id": "s1", "ai_id": "a1", "workspace": str(tmp_path)},
    }
    titles_data = [{"id": "s1", "title": "Test Chat"}]

    await state.save(
        ais=list(ais_data.values()),
        sessions=list(sessions_data.values()),
        titles=titles_data,
    )

    raw = await state_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert isinstance(parsed["ais"], list)
    assert parsed["ais"][0]["provider"] == "openai"
    assert isinstance(parsed["sessions"], list)
    assert parsed["sessions"][0]["ai_id"] == "a1"
    assert isinstance(parsed["titles"], list)
    assert parsed["titles"][0] == {"id": "s1", "title": "Test Chat"}

    snapshot = await state.load()
    assert len(snapshot["ais"]) == 1
    assert len(snapshot["sessions"]) == 1
    assert len(snapshot["titles"]) == 1


@pytest.mark.anyio
async def test_gateway_state_corrupt_json_falls_back(tmp_path: str) -> None:
    state_path = anyio.Path(tmp_path) / "state" / "latest.json"
    await state_path.parent.mkdir(parents=True)
    await state_path.write_text("{invalid json", encoding="utf-8")

    state = GatewayState(_path=state_path)
    snapshot = await state.load()
    assert snapshot == {"ais": [], "sessions": [], "titles": []}


def test_aim_get_socket_computes_for_unknown_id() -> None:
    socket = _socket_path("test", "ais", "unknown-id")
    if sys.platform == "win32":
        assert socket.startswith("\\\\.\\pipe\\")
    else:
        assert socket.endswith(".sock")
    assert "unknown-id" in socket
