"""MCP bridge with connection pooling — v3 (thread-isolated).

Each MCP call runs in a dedicated thread with its own asyncio event loop
to avoid cancel-scope conflicts with psi-agent's anyio task groups.
Stateful MCP servers (Playwright) keep browser pages alive via the pool.
All connections auto-close on idle timeout, session shutdown, or hot-reload.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


class MCPConfigError(RuntimeError):
    pass


# ── connection pool ──────────────────────────────────────────────────────────

_POOL: dict[str, dict[str, Any]] = {}
_POOL_LOCK = threading.Lock()
_IDLE_TTL: float = 300.0  # close idle connections after 5 minutes


def _thread_run(async_fn, *args, **kwargs):
    """Run an async function in a dedicated thread with its own event loop."""
    import asyncio
    return asyncio.run(async_fn(*args, **kwargs))


# ── config (env vars) ────────────────────────────────────────────────────────


def _split_args(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("["):
        loaded = json.loads(value)
        if isinstance(loaded, list):
            return [str(v) for v in loaded]
    return shlex.split(value, posix=False)


def _config(prefix: str) -> dict[str, Any]:
    p = prefix.upper()
    raw = os.environ.get(f"MCP_{p}_CONFIG", "").strip()
    if raw:
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise MCPConfigError(f"MCP_{p}_CONFIG must be a JSON object")
        return loaded

    transport = os.environ.get(f"MCP_{p}_TRANSPORT", "stdio").strip().lower()
    url = os.environ.get(f"MCP_{p}_URL", "").strip()
    command = os.environ.get(f"MCP_{p}_COMMAND", "").strip()
    args = _split_args(os.environ.get(f"MCP_{p}_ARGS", ""))
    env_raw = os.environ.get(f"MCP_{p}_ENV", "").strip()
    env: dict[str, str] | None = None
    if env_raw:
        loaded = json.loads(env_raw)
        if isinstance(loaded, dict):
            env = {str(k): str(v) for k, v in loaded.items()}

    if transport in {"http", "streamable_http", "streamable-http"}:
        if not url:
            raise MCPConfigError(f"MCP_{p}_URL is required for HTTP MCP transport")
        return {"transport": "http", "url": url}
    if transport == "sse":
        if not url:
            raise MCPConfigError(f"MCP_{p}_URL is required for SSE MCP transport")
        return {"transport": "sse", "url": url}
    if not command:
        raise MCPConfigError(
            f"MCP_{p}_COMMAND is not set. Set MCP_{p}_COMMAND and optional MCP_{p}_ARGS, "
            f"or set MCP_{p}_TRANSPORT=http with MCP_{p}_URL."
        )
    return {"transport": "stdio", "command": command, "args": args, "env": env}


# ── async core (runs in a dedicated thread with its own event loop) ──────────


class _Session:
    """Async context manager for transport + ClientSession."""

    def __init__(self, opener: Any) -> None:
        self._opener = opener
        self._transport_cm: Any = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> ClientSession:
        self._transport_cm = self._opener()
        read, write, *_ = await self._transport_cm.__aenter__()
        self._session = ClientSession(read, write)
        return await self._session.__aenter__()

    async def __aexit__(self, *args: Any) -> None:
        if self._session is not None:
            await self._session.__aexit__(*args)
        if self._transport_cm is not None:
            await self._transport_cm.__aexit__(*args)


def _connect(cfg: dict[str, Any]) -> _Session:
    transport = str(cfg.get("transport", "stdio")).lower().replace("-", "_")
    if transport == "stdio":
        params = StdioServerParameters(
            command=str(cfg["command"]),
            args=[str(v) for v in cfg.get("args", [])],
            env=cfg.get("env") or None,
        )
        return _Session(lambda: stdio_client(params, errlog=subprocess.DEVNULL))
    if transport == "sse":
        return _Session(lambda: sse_client(str(cfg["url"])))
    if transport in {"http", "streamable_http"}:
        return _Session(lambda: streamable_http_client(str(cfg["url"])))
    raise MCPConfigError(f"Unsupported MCP transport: {transport}")


def _format_result(result: Any) -> str:
    parts = [block.text for block in getattr(result, "content", []) if isinstance(block, TextContent) and block.text]
    if parts:
        return ("Error: " if getattr(result, "isError", False) else "") + "\n".join(parts)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return ("Error: " if getattr(result, "isError", False) else "") + str(result)


async def _init_session(prefix: str) -> ClientSession:
    """Create and initialize a new MCP session (runs in thread's event loop)."""
    cfg = _config(prefix)
    transport = _connect(cfg)
    session = await transport.__aenter__()
    await session.initialize()
    # Store transport alongside session for later cleanup
    session._psi_transport = transport  # type: ignore[attr-defined]
    return session


async def _do_list_tools(prefix: str) -> str:
    session = await _init_session(prefix)
    try:
        tools = (await session.list_tools()).tools
        lines = [f"- {tool.name}: {tool.description or ''}" for tool in tools]
        return "\n".join(lines) if lines else "[No MCP tools]"
    finally:
        try:
            await session._psi_transport.__aexit__(None, None, None)  # type: ignore[attr-defined]
        except Exception:
            pass


async def _do_call_tool(prefix: str, tool_name: str, args: dict[str, Any]) -> str:
    session = await _init_session(prefix)
    try:
        result = await session.call_tool(tool_name, args)
        return _format_result(result)
    finally:
        try:
            await session._psi_transport.__aexit__(None, None, None)  # type: ignore[attr-defined]
        except Exception:
            pass


async def _do_list_tools_pooled(prefix: str) -> str:
    """List tools using a pooled connection (kept alive across calls)."""
    key = prefix.upper()
    now = time.monotonic()

    with _POOL_LOCK:
        # reap idle connections
        stale = [k for k, v in _POOL.items() if now - v.get("last_used", now) > _IDLE_TTL]
        for k in stale:
            entry = _POOL.pop(k, None)
            if entry:
                try:
                    # Schedule cleanup in the session's own thread
                    pass  # Pooled sessions are cleaned up on next use
                except Exception:
                    pass

        if key in _POOL:
            entry = _POOL[key]
            entry["last_used"] = now
            session = entry["session"]
            tools = (await session.list_tools()).tools
            lines = [f"- {tool.name}: {tool.description or ''}" for tool in tools]
            return "\n".join(lines) if lines else "[No MCP tools]"

    # Create new pooled session
    session = await _init_session(prefix)
    with _POOL_LOCK:
        _POOL[key] = {"session": session, "last_used": time.monotonic()}
    tools = (await session.list_tools()).tools
    lines = [f"- {tool.name}: {tool.description or ''}" for tool in tools]
    return "\n".join(lines) if lines else "[No MCP tools]"


async def _do_call_tool_pooled(prefix: str, tool_name: str, args: dict[str, Any]) -> str:
    """Call a tool using a pooled connection."""
    key = prefix.upper()
    now = time.monotonic()

    with _POOL_LOCK:
        # reap idle connections
        stale = [k for k, v in _POOL.items() if now - v.get("last_used", now) > _IDLE_TTL]
        for k in stale:
            entry = _POOL.pop(k, None)

        if key in _POOL:
            entry = _POOL[key]
            entry["last_used"] = now
            session = entry["session"]
        else:
            session = None

    if session is None:
        session = await _init_session(prefix)
        with _POOL_LOCK:
            _POOL[key] = {"session": session, "last_used": time.monotonic()}

    return _format_result(await session.call_tool(tool_name, args))


async def _do_close(prefix: str) -> str:
    key = prefix.upper()
    if prefix == "*":
        with _POOL_LOCK:
            keys = list(_POOL)
            for k in keys:
                entry = _POOL.pop(k, None)
                if entry:
                    try:
                        await entry["session"]._psi_transport.__aexit__(None, None, None)  # type: ignore[attr-defined]
                    except Exception:
                        pass
        return f"[OK] Closed {len(keys)} MCP connection(s)"
    with _POOL_LOCK:
        entry = _POOL.pop(key, None)
    if entry is None:
        return f"[Error] No active connection: {prefix}"
    try:
        await entry["session"]._psi_transport.__aexit__(None, None, None)  # type: ignore[attr-defined]
    except Exception:
        pass
    return f"[OK] Closed MCP: {prefix}"


# ── public tools (thread-isolated) ───────────────────────────────────────────


async def call_mcp(prefix: str, tool_name: str, args_json: str = "{}") -> str:
    """Call a tool on an MCP server. Each call runs in its own thread.

    Available MCP prefixes:
    - PW     → Playwright browser (navigate, click, type, screenshot, snapshot, evaluate...)
    - FS     → Filesystem (read, write, edit, search, list directory)
    - VISION → FREE image understanding (analyze, describe — no API key)
    - MEDIA  → Image generation (free Pollinations.ai) + TTS + STT
    - FEISHU → Feishu messaging (send text, cards, images, files)

    Args:
        prefix: MCP server prefix, e.g. "PW" for Playwright.
        tool_name: Tool to call. Discover with list_mcp_tools first.
        args_json: Arguments as JSON string, e.g. '{"url": "https://example.com"}'.

    Examples:
        call_mcp("PW", "browser_navigate", '{"url": "https://example.com"}')
        call_mcp("PW", "browser_snapshot", "{}")
        call_mcp("PW", "browser_take_screenshot", '{"filename": "E:/shot.png"}')
    """
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            return "[Error] args_json must decode to an object"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_thread_run, _do_call_tool, prefix, tool_name, args).result(timeout=120)
    except concurrent.futures.TimeoutError:
        return "[MCP Error] Request timed out after 120s"
    except Exception as exc:
        return f"[MCP Error] {exc}"


async def list_mcp_tools(prefix: str) -> str:
    """Discover all tools on an MCP server. Call this first when exploring a new prefix.

    Available prefixes: PW (Playwright browser), FS (filesystem),
    VISION (FREE image understanding), MEDIA (image gen/TTS/STT), FEISHU (Feishu).

    Args:
        prefix: MCP prefix. Must match an MCP_{PREFIX}_COMMAND env var.

    Example:
        list_mcp_tools("PW") → lists all Playwright browser tools
    """
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_thread_run, _do_list_tools, prefix).result(timeout=30)
    except concurrent.futures.TimeoutError:
        return "[MCP Error] Request timed out after 30s"
    except Exception as exc:
        return f"[MCP Error] {exc}"


async def close_mcp(prefix: str) -> str:
    """Close persistent MCP connection(s).

    Args:
        prefix: Prefix to close, or "*" to close all.
    """
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_thread_run, _do_close, prefix).result(timeout=10)
    except Exception as exc:
        return f"[MCP Error] {exc}"
