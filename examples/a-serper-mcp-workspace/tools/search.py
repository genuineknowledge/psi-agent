from __future__ import annotations

import contextlib
import importlib
import os
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _mcp import mcp
finally:
    sys.path.pop(0)


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
    for mod_name in ("serper_mcp_server.core", "serper_mcp_server.server"):
        vars(importlib.import_module(mod_name))["SERPER_API_KEY"] = key
    return key


def _transport():
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


@mcp
def serper() -> dict[str, object]:
    """Uses a deployment-wide ``SERPER_API_KEY`` (gateway process env, or workspace ``.env``)."""
    _sync_api_key()
    return {
        "type": "coroutine",
        "fn": _transport(),
    }
