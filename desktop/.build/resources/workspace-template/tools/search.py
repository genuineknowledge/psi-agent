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


def _transport():
    mod = importlib.import_module("serper_mcp_server.server")
    server = mod.server

    @contextlib.asynccontextmanager
    async def connect():
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


@mcp
def serper() -> dict[str, object]:
    """Requires ``SERPER_API_KEY`` in workspace ``.env``."""
    _load_env(Path(__file__).parent.parent / ".env")
    return {
        "type": "coroutine",
        "fn": _transport(),
    }
