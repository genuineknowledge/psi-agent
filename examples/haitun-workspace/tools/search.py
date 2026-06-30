from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _mcp import mcp
finally:
    sys.path.pop(0)


def _transport():
    mod = importlib.import_module("serper_mcp_server.server")
    server = mod.server

    @contextlib.asynccontextmanager
    async def connect():
        c2s_recv, c2s_send = anyio.create_memory_object_stream()
        s2c_recv, s2c_send = anyio.create_memory_object_stream()
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.run, c2s_recv, s2c_send, server.create_initialization_options())
            yield c2s_send, s2c_recv
            tg.cancel_scope.cancel()

    return connect


@mcp
def serper() -> dict[str, object]:
    return {
        "type": "coroutine",
        "fn": _transport(),
    }
