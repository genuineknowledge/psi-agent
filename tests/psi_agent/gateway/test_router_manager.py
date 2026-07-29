from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import cast

import anyio
import pytest

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._router_manager import RouterManager, RouterUpstreamInfo, _run_router_service


class FakeAIManager:
    def __init__(self) -> None:
        self.sockets = {"route": "http://route", "simple": "http://simple", "complex": "http://complex"}

    def has(self, ai_id: str) -> bool:
        return ai_id in self.sockets

    def get_socket(self, ai_id: str) -> str:
        return self.sockets[ai_id]


@pytest.mark.anyio
async def test_run_router_service_builds_merged_router(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    class FakeRouter:
        def __init__(self, **kwargs: object) -> None:
            captured.append(kwargs)

        async def run(self) -> None:
            return None

    class FakeModule:
        Router = FakeRouter

    original_import = __import__

    def fake_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType | object:
        if name == "psi_agent.router":
            return FakeModule()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)
    await _run_router_service(
        session_socket="router.sock",
        router_socket="route-ai.sock",
        upstreams=(("simple.sock", "simple tasks"),),
        default_socket="simple.sock",
        router_timeout=None,
        router_context_chars=12_000,
    )
    assert captured[0]["session_socket"] == "router.sock"
    assert captured[0]["router_socket"] == "route-ai.sock"
    assert captured[0]["upstream"] == ['{"socket": "simple.sock", "description": "simple tasks"}']


@pytest.mark.anyio
async def test_create_and_delete_router(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ready(_path: str) -> None:
        await anyio.sleep(0.001)

    async def serve(**_kwargs: object) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr("psi_agent.gateway._router_manager._wait_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._remove_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._run_router_service", serve)
    async with anyio.create_task_group() as tg:
        manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", tg)
        info = await manager.create(
            "smart",
            "route",
            [RouterUpstreamInfo("simple", "simple tasks"), RouterUpstreamInfo("complex", "complex tasks")],
            "simple",
            id="router-1",
        )
        assert manager.get_socket("router-1") == info.socket
        assert [item.ai_id for item in info.upstreams] == ["simple", "complex"]
        await manager.delete("router-1")
        assert not manager.has("router-1")
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_rejects_invalid_router_configuration() -> None:
    async with anyio.create_task_group() as tg:
        manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", tg)
        with pytest.raises(ValueError, match="duplicate"):
            await manager.create(
                "smart",
                "route",
                [RouterUpstreamInfo("simple", "one"), RouterUpstreamInfo("simple", "two")],
                "simple",
            )
        with pytest.raises(LookupError, match="missing"):
            await manager.create(
                "smart",
                "missing",
                [RouterUpstreamInfo("simple", "one")],
                "simple",
            )
        tg.cancel_scope.cancel()
