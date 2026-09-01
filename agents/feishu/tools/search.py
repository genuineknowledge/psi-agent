from __future__ import annotations

import contextlib
import importlib
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _mcp import mcp
finally:
    sys.path.pop(0)


def _install_lowlevel_decorator_shim() -> bool:
    """Restore the ``Server.list_tools()`` / ``Server.call_tool()`` decorators that
    ``mcp`` 2.0 removed, translating them onto the replacement API.

    The primary fix for this incompatibility is the ``mcp>=1.28.1,<2.0.0`` pin in
    ``pyproject.toml``; this shim is a safety net for an environment that resolved
    to 2.x anyway. That is not hypothetical — it is how the incident happened: the
    image installs with ``pip install -e .``, which ignores ``uv.lock`` and
    re-resolves, so a then-unbounded ``mcp>=1.28.1`` picked up 2.0.0 on a rebuild.

    ``serper-mcp-server`` 0.0.10 — the latest release, still declaring
    ``mcp[cli]>=1.6.0`` with no fixed version upstream — applies both decorators at
    **module import time**. Under ``mcp`` 2.0, which replaced them with
    ``Server.add_request_handler(method, params_type, handler)``, merely importing
    the package raises ``AttributeError: 'Server' object has no attribute
    'list_tools'``, and the broken ``__init__`` takes ``core`` down with it. Because
    :func:`_transport` imports the package in-process, every search then fails
    before a request is ever sent — and an agent that cannot search tends to fall
    back to scraping search-engine HTML, which returns plausible-looking garbage
    rather than an honest error.

    Returns whether the shim was installed. It is idempotent and guarded on
    ``hasattr``, so it is a no-op under the pinned 1.x — where the real class still
    has the decorators — and becomes one automatically once upstream supports 2.0.
    """
    from mcp.server.lowlevel.server import Server  # noqa: PLC0415

    if hasattr(Server, "list_tools") and hasattr(Server, "call_tool"):
        return False  # mcp 1.x, or upstream grew 2.0 support.

    import mcp.types as types  # noqa: PLC0415

    def list_tools(self: Any) -> Any:
        """Register a ``() -> list[Tool]`` coroutine as the ``tools/list`` handler."""

        def decorator(func: Any) -> Any:
            async def handler(_ctx: Any, _params: Any) -> types.ListToolsResult:
                return types.ListToolsResult(tools=list(await func()))

            # ``PaginatedRequestParams`` is all-optional, so a request carrying no
            # ``params`` member still reaches the handler with defaults.
            self.add_request_handler("tools/list", types.PaginatedRequestParams, handler)
            return func

        return decorator

    def call_tool(self: Any, *_args: Any, **_kwargs: Any) -> Any:
        """Register a ``(name, arguments) -> Sequence[ContentBlock]`` coroutine as
        the ``tools/call`` handler.

        Extra arguments are accepted and ignored: the 1.x decorator grew keyword
        options over time, and a caller passing one must not crash the shim.
        """

        def decorator(func: Any) -> Any:
            async def handler(_ctx: Any, params: Any) -> types.CallToolResult:
                content = await func(params.name, params.arguments or {})
                return types.CallToolResult(content=list(content))

            self.add_request_handler("tools/call", types.CallToolRequestParams, handler)
            return func

        return decorator

    Server.list_tools = list_tools
    Server.call_tool = call_tool
    logger.debug("Installed mcp 1.x lowlevel decorator shim (list_tools/call_tool) for serper-mcp-server")
    return True


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in os.environ:
            continue
        value = value.strip().strip("\"'")
        os.environ[key] = value


def _sync_api_key() -> str:
    """Resolve ``SERPER_API_KEY`` as a deployment-wide global and push it into the
    ``serper_mcp_server`` package on every connection.

    The package captures the key **once, at import time**, in two module-level
    globals: ``core.SERPER_API_KEY`` (used to build the request header) and
    ``server.SERPER_API_KEY`` (the empty-key guard, imported from ``core``).
    ``importlib`` caches the package in ``sys.modules`` under its real name — with
    no per-session suffix — so it is imported once *per process* and shared by
    every Feishu user's session in the one gateway process. That first captured
    value is therefore frozen for everyone: whichever user searches first decides
    the key for the whole process, and later users are silently skipped (their
    ``.env`` key is dropped by the ``if key in os.environ`` guard above), so their
    searches either bill the wrong account or fail outright.

    We treat the key as a single deployment-wide global instead: read it live from
    the process environment and write it back into both package globals here, so
    the frozen import-time value never matters. The workspace ``.env`` is still
    loaded (into the shared ``os.environ``) for backward compatibility with
    deployments that place the key there; set ``SERPER_API_KEY`` in the gateway
    process environment to configure it for all users at once.
    """
    _load_env(Path(__file__).parent.parent / ".env")
    key = os.getenv("SERPER_API_KEY", "").strip()
    _install_lowlevel_decorator_shim()  # Must precede any serper import.
    for mod_name in ("serper_mcp_server.core", "serper_mcp_server.server"):
        vars(importlib.import_module(mod_name))["SERPER_API_KEY"] = key
    return key


def _transport():
    _install_lowlevel_decorator_shim()  # Must precede any serper import.
    mod = importlib.import_module("serper_mcp_server.server")
    server = mod.server

    @contextlib.asynccontextmanager
    async def connect():
        # Refresh the deployment-wide key on every connection so the value the
        # serper package froze at import time can never shadow the current one.
        _sync_api_key()
        c2s_send, c2s_recv = anyio.create_memory_object_stream()
        s2c_send, s2c_recv = anyio.create_memory_object_stream()
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                server.run,
                c2s_recv,
                s2c_send,
                server.create_initialization_options(),
            )
            try:
                yield s2c_recv, c2s_send
            finally:
                tg.cancel_scope.cancel()

    return connect


@mcp(
    dispatch=True,
    # ``serper_google_search`` is the plain web search and the only one with
    # meaningful recorded traffic; the other twelve verticals (images, maps,
    # scholar, patents, ...) are reachable through ``serper_call``.
    keep=("serper_google_search",),
)
def serper() -> dict[str, object]:
    """Uses a deployment-wide ``SERPER_API_KEY`` (gateway process env, or workspace ``.env``).

    Pass ``num`` as a *string*: serper's schema types it as one, and an int fails
    pydantic validation with the complaint wrapped in a non-error text block, which
    reads like an empty result rather than a bad argument.
    """
    _sync_api_key()
    return {
        "type": "coroutine",
        "fn": _transport(),
    }
