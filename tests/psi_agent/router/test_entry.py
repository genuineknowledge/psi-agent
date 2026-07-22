from __future__ import annotations

import math
from typing import Any

import pytest

from psi_agent.router import Router, serve_router


def _router_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "session_socket": "router.sock",
        "router_socket": "planner.sock",
        "default_socket": "default.sock",
        "upstream": [("research.sock", "research")],
    }
    values.update(overrides)
    return values


@pytest.mark.anyio
async def test_router_run_configures_logging_before_validating_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    configured: list[bool] = []

    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: configured.append(verbose))

    with pytest.raises(ValueError, match="session_socket"):
        await Router(**_router_kwargs(session_socket="")).run()

    assert configured == [False]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"session_socket": ""},
        {"router_socket": " "},
        {"default_socket": ""},
        {"upstream": []},
        {"upstream": [("", "research")]},
        {"upstream": [("research.sock", "")]},
        {"max_tool_rounds": 0},
        {"max_tool_rounds": False},
        {"router_timeout": 0.0},
        {"branch_timeout": -1.0},
        {"aggregate_timeout": math.nan},
        {"run_ttl": 0.0},
        {"run_ttl": math.nan},
        {"default_socket": "router.sock"},
    ],
)
async def test_router_run_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)

    with pytest.raises(ValueError):
        await Router(**_router_kwargs(**overrides)).run()


@pytest.mark.anyio
async def test_router_run_builds_config_and_orchestrator_then_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    configured: list[bool] = []
    served: list[tuple[object, object]] = []

    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: configured.append(verbose))

    async def fake_serve_router(*, config: object, orchestrator: object) -> None:
        served.append((config, orchestrator))

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve_router)
    router = Router(
        **_router_kwargs(
            upstream=[("research.sock", "research"), ("research.sock", "recent sources")],
            max_tool_rounds=3,
            router_timeout=10.0,
            branch_timeout=20.0,
            aggregate_timeout=None,
            run_ttl=30.0,
            verbose=True,
        )
    )

    await router.run()

    assert configured == [True]
    assert len(served) == 1
    config, orchestrator = served[0]
    assert config.session_socket == "router.sock"
    assert config.router_socket == "planner.sock"
    assert config.default_socket == "default.sock"
    assert config.upstream == (("research.sock", "research"), ("research.sock", "recent sources"))
    assert config.max_tool_rounds == 3
    assert config.router_timeout == 10.0
    assert config.branch_timeout == 20.0
    assert config.aggregate_timeout is None
    assert config.run_ttl == 30.0
    assert orchestrator.config is config


@pytest.mark.anyio
async def test_router_run_accepts_supported_transport_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[object] = []

    monkeypatch.setattr("psi_agent.router.entry.setup_logging", lambda *, verbose: None)

    async def fake_serve_router(*, config: object, orchestrator: object) -> None:
        del orchestrator
        served.append(config)

    monkeypatch.setattr("psi_agent.router.entry.serve_router", fake_serve_router)

    await Router(
        **_router_kwargs(
            session_socket="http://127.0.0.1:8000",
            router_socket=r"\\.\pipe\planner",
            default_socket="https://default.example",
            upstream=[("http://127.0.0.1:8100", "research")],
        )
    ).run()

    assert served[0].session_socket == "http://127.0.0.1:8000"
    assert served[0].router_socket == r"\\.\pipe\planner"
    assert served[0].default_socket == "https://default.example"
    assert served[0].upstream == (("http://127.0.0.1:8100", "research"),)


def test_router_is_exported_with_its_server_function() -> None:
    assert Router.__name__ == "Router"
    assert serve_router.__name__ == "serve_router"
