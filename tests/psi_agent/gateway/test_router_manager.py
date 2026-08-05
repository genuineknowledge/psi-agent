from __future__ import annotations

from typing import Any, cast

import anyio
import pytest
from anyio.lowlevel import checkpoint

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._router_manager import (
    RouterDependencyError,
    RouterManager,
    RouterUpstreamInfo,
    _run_router_service,
)


def _ai(backend_id: str, description: str) -> RouterUpstreamInfo:
    return RouterUpstreamInfo(backend_type="ai", backend_id=backend_id, description=description)


def _router(backend_id: str, description: str) -> RouterUpstreamInfo:
    return RouterUpstreamInfo(backend_type="router", backend_id=backend_id, description=description)


class FakeAIManager:
    def __init__(self) -> None:
        self.sockets = {
            "route": "http://route",
            "simple": "http://simple",
            "complex": "http://complex",
        }

    def has(self, ai_id: str) -> bool:
        return ai_id in self.sockets

    def get_socket(self, ai_id: str) -> str:
        return self.sockets[ai_id]


@pytest.mark.anyio
async def test_run_router_service_builds_current_router(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    class FakeRouter:
        def __init__(self, **kwargs: object) -> None:
            captured.append(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr("psi_agent.gateway._router_manager.Router", FakeRouter)
    await _run_router_service(
        session_socket="router.sock",
        mode="aggregation",
        router_socket="aggregate.sock",
        upstreams=(("simple.sock", "simple tasks", "ai"),),
        router_timeout=30,
        target_timeout=8,
        max_context_chars=9_000,
    )

    assert captured == [
        {
            "session_socket": "router.sock",
            "mode": "aggregation",
            "router_socket": "aggregate.sock",
            "upstream": [("simple.sock", "simple tasks", "ai")],
            "router_timeout": 30,
            "target_timeout": 8,
            "max_context_chars": 9_000,
        }
    ]


@pytest.mark.anyio
async def test_create_aggregation_router_maps_ai_ids_and_current_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def ready(_path: str) -> None:
        return None

    async def remove(_path: str) -> None:
        return None

    async def serve(**kwargs: object) -> None:
        captured.append(kwargs)
        await anyio.sleep_forever()

    monkeypatch.setattr("psi_agent.gateway._router_manager._wait_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._remove_socket", remove)
    monkeypatch.setattr("psi_agent.gateway._router_manager._run_router_service", serve)
    async with anyio.create_task_group() as tg:
        try:
            manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", tg)
            info = await manager.create(
                "  Broadcaster  ",
                " aggregation ",
                " route ",
                [
                    _ai(" simple ", " simple tasks "),
                    _ai(" complex ", " complex tasks "),
                ],
                router_timeout=30,
                target_timeout=8,
                max_context_chars=9_000,
                id="router-1",
            )
            await checkpoint()

            assert info.id == "router-1"
            assert info.name == "Broadcaster"
            assert info.mode == "aggregation"
            assert info.router_ai_id == "route"
            assert info.upstreams == (
                _ai("simple", "simple tasks"),
                _ai("complex", "complex tasks"),
            )
            assert info.router_timeout == 30
            assert info.target_timeout == 8
            assert info.max_context_chars == 9_000
            assert captured == [
                {
                    "session_socket": info.socket,
                    "mode": "aggregation",
                    "router_socket": "http://route",
                    "upstreams": (
                        ("http://simple", "simple tasks", "ai"),
                        ("http://complex", "complex tasks", "ai"),
                    ),
                    "router_timeout": 30,
                    "target_timeout": 8,
                    "max_context_chars": 9_000,
                }
            ]
            await manager.delete("router-1")
            assert not manager.has("router-1")
        finally:
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_aggregation_rejects_router_ai_reused_as_upstream() -> None:
    async with anyio.create_task_group() as tg:
        try:
            manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", tg)
            with pytest.raises(ValueError, match="must not also be an upstream"):
                await manager.create(
                    "aggregate",
                    "aggregation",
                    "route",
                    [_ai("route", "aggregate responses")],
                )
        finally:
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_routing_allows_selector_ai_as_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ready(_path: str) -> None:
        return None

    async def remove(_path: str) -> None:
        return None

    async def serve(**_kwargs: object) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr("psi_agent.gateway._router_manager._wait_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._remove_socket", remove)
    monkeypatch.setattr("psi_agent.gateway._router_manager._run_router_service", serve)
    async with anyio.create_task_group() as tg:
        try:
            manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", tg)
            info = await manager.create(
                "routing",
                "routing",
                "route",
                [_ai("route", "selected responses")],
                id="router-1",
            )
            assert info.router_ai_id == info.upstreams[0].backend_id == "route"
            await manager.delete("router-1")
        finally:
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_rejects_invalid_router_configuration_before_spawning() -> None:
    class RecordingTaskGroup:
        def __init__(self) -> None:
            self.started = False

        def start_soon(self, _func: object) -> None:
            self.started = True

    task_group = RecordingTaskGroup()
    manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", task_group)

    with pytest.raises(ValueError, match="duplicate"):
        await manager.create(
            "smart",
            "routing",
            "route",
            [_ai("simple", "one"), _ai("simple", "two")],
        )
    with pytest.raises(LookupError, match="missing"):
        await manager.create(
            "smart",
            "routing",
            "missing",
            [_ai("simple", "one")],
        )
    assert not task_group.started


@pytest.mark.anyio
@pytest.mark.parametrize("field_name", ["router_timeout", "target_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "30"])
async def test_rejects_invalid_timeouts_before_spawning(field_name: str, value: object) -> None:
    class RecordingTaskGroup:
        def __init__(self) -> None:
            self.started = False

        def start_soon(self, _func: object) -> None:
            self.started = True

    task_group = RecordingTaskGroup()
    manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", task_group)
    with pytest.raises(ValueError, match=f"{field_name} must be a finite positive number or None"):
        if field_name == "router_timeout":
            await manager.create(
                "smart",
                "routing",
                "route",
                [_ai("simple", "one")],
                router_timeout=cast(float | None, value),
            )
        else:
            await manager.create(
                "smart",
                "routing",
                "route",
                [_ai("simple", "one")],
                target_timeout=cast(float | None, value),
            )
    assert not task_group.started


@pytest.mark.anyio
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "9000"])
async def test_rejects_invalid_context_budget_before_spawning(value: object) -> None:
    class RecordingTaskGroup:
        def __init__(self) -> None:
            self.started = False

        def start_soon(self, _func: object) -> None:
            self.started = True

    task_group = RecordingTaskGroup()
    manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", task_group)

    with pytest.raises(ValueError, match="max_context_chars must be a positive integer"):
        await manager.create(
            "smart",
            "routing",
            "route",
            [_ai("simple", "one")],
            max_context_chars=cast(int, value),
        )
    assert not task_group.started


@pytest.mark.anyio
async def test_fallback_resolves_ai_and_router_upstreams_and_protects_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def ready(_path: str) -> None:
        return None

    async def remove(_path: str) -> None:
        return None

    async def serve(**kwargs: object) -> None:
        captured.append(kwargs)
        await anyio.sleep_forever()

    monkeypatch.setattr("psi_agent.gateway._router_manager._wait_socket", ready)
    monkeypatch.setattr("psi_agent.gateway._router_manager._remove_socket", remove)
    monkeypatch.setattr("psi_agent.gateway._router_manager._run_router_service", serve)
    async with anyio.create_task_group() as tg:
        try:
            manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", tg)
            leaf = await manager.create(
                "leaf",
                "fallback",
                None,
                [_ai("simple", "primary"), _ai("complex", "backup")],
                id="leaf",
            )
            parent = await manager.create(
                "parent",
                "routing",
                "route",
                [_ai("simple", "direct"), _router("leaf", "resilient")],
                id="parent",
            )
            await checkpoint()

            assert leaf.router_ai_id is None
            assert captured[0]["router_socket"] is None
            assert captured[1]["upstreams"] == (
                ("http://simple", "direct", "ai"),
                (leaf.socket, "resilient", "router"),
            )
            with pytest.raises(RouterDependencyError, match="parent"):
                await manager.delete("leaf")
            await manager.delete(parent.id)
            await manager.delete(leaf.id)
        finally:
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_manager_rejects_mode_controller_and_backend_contracts_before_spawning() -> None:
    class RecordingTaskGroup:
        def __init__(self) -> None:
            self.started = False

        def start_soon(self, _func: object) -> None:
            self.started = True

    task_group = RecordingTaskGroup()
    manager = RouterManager(cast(AIManager, FakeAIManager()), "gw", task_group)

    with pytest.raises(ValueError, match="router_ai_id"):
        await manager.create("bad", "fallback", "route", [_ai("simple", "one")])
    with pytest.raises(ValueError, match="router_ai_id"):
        await manager.create("bad", "routing", None, [_ai("simple", "one")])
    with pytest.raises(LookupError, match="Router"):
        await manager.create("bad", "fallback", None, [_router("missing", "one")])
    with pytest.raises(ValueError, match="typed backend"):
        await manager.create(
            "bad",
            "fallback",
            None,
            [RouterUpstreamInfo(backend_type=cast(Any, "unknown"), backend_id="simple", description="one")],
        )
    assert not task_group.started
