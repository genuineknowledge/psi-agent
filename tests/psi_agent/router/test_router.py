from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
from aiohttp import web

from psi_agent.router import Router, serve_router
from psi_agent.router.models import Upstream
from psi_agent.router.server import RouterSettings

UPSTREAM = '{"socket":"http://127.0.0.1:7001","description":"simple"}'


def test_ai_router_defaults() -> None:
    router = Router(session_socket="http://127.0.0.1:8100")
    assert router.router_socket == ""
    assert router.upstream == []
    assert router.default_socket == ""
    assert router.router_timeout is None
    assert router.router_context_chars == 12_000
    assert router.log_router_details is False
    assert router.verbose is False


def test_ai_router_run_sets_up_logging_first() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(Router.run)))
    function = tree.body[0]
    assert isinstance(function, ast.AsyncFunctionDef)
    first = function.body[0]
    assert isinstance(first, ast.Expr)
    assert ast.unparse(first.value) == "setup_logging(verbose=self.verbose)"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("router_socket", "", "router-socket"),
        ("upstream", [], "upstream"),
        ("default_socket", "", "default-socket"),
        ("router_context_chars", 0, "router-context-chars"),
        ("router_timeout", 0.0, "router-timeout"),
        ("router_timeout", float("inf"), "router-timeout"),
    ],
)
async def test_ai_router_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, field_name: str, invalid_value: object, message: str
) -> None:
    router = Router(
        session_socket="http://127.0.0.1:8100",
        router_socket="http://127.0.0.1:7999",
        upstream=[UPSTREAM],
        default_socket="http://127.0.0.1:7001",
    )
    setattr(router, field_name, invalid_value)
    with pytest.raises(ValueError, match=message):
        await router.run()


@pytest.mark.anyio
async def test_router_builds_socket_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, RouterSettings]] = []

    async def fake_serve_router(*, socket_path: str, settings: RouterSettings) -> None:
        captured.append((socket_path, settings))

    monkeypatch.setattr("psi_agent.router.serve_router", fake_serve_router)
    router = Router(
        session_socket="http://127.0.0.1:8100",
        router_socket="http://127.0.0.1:7999",
        upstream=[UPSTREAM],
        default_socket="http://127.0.0.1:7001",
    )
    await router.run()
    assert captured == [
        (
            "http://127.0.0.1:8100",
            RouterSettings(
                targets=(Upstream("http://127.0.0.1:7001", "simple"),),
                router_socket="http://127.0.0.1:7999",
                default_socket="http://127.0.0.1:7001",
                router_timeout=None,
                context_chars=12_000,
                log_details=False,
            ),
        )
    ]


@pytest.mark.anyio
async def test_serve_router_cleans_up_runner_on_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_calls: list[bool] = []
    original_cleanup = web.AppRunner.cleanup

    async def spy_cleanup(self: web.AppRunner) -> None:
        cleanup_calls.append(True)
        await original_cleanup(self)

    class BadSite:
        async def start(self) -> None:
            raise RuntimeError("bind failed")

    monkeypatch.setattr(web.AppRunner, "cleanup", spy_cleanup)
    monkeypatch.setattr("psi_agent.router.create_site", lambda runner, addr: BadSite())
    settings = RouterSettings(
        targets=(Upstream("http://upstream", "simple"),),
        router_socket="http://upstream",
        default_socket="http://default",
        router_timeout=None,
        context_chars=12_000,
        log_details=False,
    )
    with pytest.raises(RuntimeError, match="bind failed"):
        await serve_router(socket_path="http://127.0.0.1:8100", settings=settings)
    assert cleanup_calls == [True]
