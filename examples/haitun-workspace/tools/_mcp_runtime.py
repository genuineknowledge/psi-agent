"""MCP bridge with connection pooling — v2.

Pooled connections survive across tool calls within a session turn.
Stateful MCP servers (Playwright) keep browser pages alive.
All connections auto-close on idle timeout, session shutdown, or hot-reload.

Configuration:
    Set env var ``MCP_<PREFIX>_CONFIG`` to a JSON object:

    Stdio (subprocess):
        {"command": "npx", "args": ["@playwright/mcp"], "env": {"NODE_ENV": "production"}}

    SSE / Streamable HTTP:
        {"url": "http://localhost:8080/sse"}

    With explicit transport:
        {"transport": "stdio", "command": "python", "args": ["server.py"]}
        {"transport": "sse", "url": "http://localhost:8080/sse"}
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import shlex
import time
from collections.abc import Mapping
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

_POOL: dict[str, dict[str, Any]] = {}
_IDLE_TTL: float = 300.0  # 5 minutes idle timeout


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

def _config(prefix: str) -> dict[str, Any]:
    """Read MCP server config from ``MCP_<PREFIX>_CONFIG`` env var (JSON)."""

    raw = os.environ.get(f"MCP_{prefix.upper()}_CONFIG", "").strip()
    if not raw:
        # Legacy: try per-field env vars
        transport = os.environ.get(f"MCP_{prefix.upper()}_TRANSPORT", "").strip().lower()
        url = os.environ.get(f"MCP_{prefix.upper()}_URL", "").strip()
        command = os.environ.get(f"MCP_{prefix.upper()}_COMMAND", "").strip()
        args_raw = os.environ.get(f"MCP_{prefix.upper()}_ARGS", "").strip()
        env_raw = os.environ.get(f"MCP_{prefix.upper()}_ENV", "").strip()

        if not transport and not command and not url:
            return {}

        cfg: dict[str, Any] = {}
        if transport:
            cfg["transport"] = transport
        if url:
            cfg["url"] = url
        if command:
            cfg["command"] = command
            try:
                cfg["args"] = json.loads(args_raw) if args_raw else []
            except json.JSONDecodeError:
                cfg["args"] = [a.strip() for a in args_raw.split() if a.strip()]
        else:
            cfg["args"] = []
        if env_raw:
            try:
                cfg["env"] = json.loads(env_raw)
            except json.JSONDecodeError:
                cfg["env"] = {}
        else:
            cfg["env"] = {}
        return cfg

    try:
        cfg = json.loads(raw)
        if not isinstance(cfg, dict):
            return {}
        return cfg
    except json.JSONDecodeError:
        return {}


def _resolve(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a config dict into a standard transport descriptor."""
    if not isinstance(raw, Mapping):
        return {}

    d: dict[str, Any] = dict(raw)

    # Normalize server/command field
    srv: Any = next(
        (d.pop(k) for k in ("server", "mcpServer", "mcp_server", "command", "cmd") if k in d),
        None,
    )
    transport = (d.pop("transport", None) or d.pop("type", None) or "").strip().lower()
    url = (d.pop("url", None) or d.pop("endpoint", None) or "").strip()

    cmd: str | None = None
    args: list[str] = []

    if isinstance(srv, str):
        srv = srv.strip()
        if srv.startswith(("http://", "https://")):
            url = url or srv
        else:
            # Split shell-style: "npx @playwright/mcp"
            parts = shlex.split(srv)
            if parts:
                cmd, args = parts[0], parts[1:]

    elif isinstance(srv, (list, tuple)):
        parts = [str(v) for v in srv]
        if parts:
            if transport in ("http", "sse"):
                url = url or parts[0]
            else:
                cmd, args = parts[0], parts[1:]

    # Merge any extra args from config
    if isinstance(d.get("args"), list):
        args.extend([str(a) for a in d.pop("args")])

    # Determine transport
    if not transport:
        transport = "http" if url else "stdio"
    transport = transport.lower().replace("-", "_").replace("local", "stdio")

    if transport == "stdio" and not cmd:
        return {}
    if transport in ("http", "sse") and not url:
        return {}

    result: dict[str, Any] = {
        "transport": transport,
        "command": cmd,
        "args": args,
        "cwd": d.pop("cwd", None),
        "url": url or None,
    }

    # Env: accept "env" or "environment" key
    env = d.pop("env", None) or d.pop("environment", None)
    if isinstance(env, Mapping):
        result["env"] = {str(k): str(v) for k, v in env.items()}
    else:
        result["env"] = {}

    return result


# ---------------------------------------------------------------------------
# Transport connection
# ---------------------------------------------------------------------------

class _Session:
    """Async context manager wrapping an MCP transport + ClientSession."""

    def __init__(self, opener: Any) -> None:
        self._o = opener
        self._c: Any = None
        self._s: ClientSession | None = None

    async def __aenter__(self) -> ClientSession:
        self._c = self._o()
        r, w, *_ = await self._c.__aenter__()
        self._s = ClientSession(r, w)
        return await self._s.__aenter__()

    async def __aexit__(self, *a: Any) -> None:
        with contextlib.suppress(Exception):
            if self._s is not None:
                with anyio.CancelScope(shield=True):
                    await self._s.__aexit__(*a)
        with contextlib.suppress(Exception):
            if self._c is not None:
                with anyio.CancelScope(shield=True):
                    await self._c.__aexit__(*a)


def _connect(config: dict[str, Any]) -> _Session:
    """Build a ``_Session`` from a resolved transport config."""
    t = config.get("transport", "stdio")

    if t == "stdio":
        p = StdioServerParameters(
            command=config["command"],
            args=config.get("args", []),
            env=config.get("env") or None,
        )
        return _Session(lambda: stdio_client(p))

    if t == "sse":
        url = config["url"]
        return _Session(lambda: sse_client(url))

    # streamable_http
    url = config["url"]
    return _Session(lambda: streamable_http_client(url))


# ---------------------------------------------------------------------------
# Pooling logic
# ---------------------------------------------------------------------------

async def _get_or_create(prefix: str) -> tuple[ClientSession, dict[str, Any]]:
    """Return a pooled (session, entry) for *prefix*. Reuses live connections
    within the idle TTL; creates a fresh one otherwise."""

    key = prefix.upper()
    now = time.monotonic()
    entry = _POOL.get(key)

    if entry is not None:
        elapsed = now - entry.get("last_used", 0)
        if elapsed < _IDLE_TTL:
            entry["last_used"] = now
            return entry["session"], entry

    # Stale or missing — close old, create new
    if entry is not None:
        await _close_one(key, entry)

    raw_cfg = _config(prefix)
    if not raw_cfg:
        raise RuntimeError(
            f"No MCP config for prefix '{prefix}'. "
            f"Set MCP_{key}_CONFIG env var (JSON). "
            f'Example: MCP_{key}_CONFIG={{"command":"npx","args":["@playwright/mcp"]}}'
        )

    resolved = _resolve(raw_cfg)
    if not resolved:
        raise RuntimeError(
            f"Invalid MCP config for prefix '{prefix}': {json.dumps(raw_cfg)}. "
            f"Expected keys: command+args (stdio) or url (HTTP/SSE)."
        )

    session_ctx = _connect(resolved)
    transport_ctx = await session_ctx.__aenter__()
    session: ClientSession = transport_ctx  # _Session.__aenter__ returns the ClientSession

    entry = {
        "transport": session_ctx,
        "session": session,
        "last_used": now,
    }
    _POOL[key] = entry

    try:
        await session.initialize()
    except Exception:
        await _close_one(key, entry)
        raise

    return session, entry


async def _close_one(key: str, entry: dict[str, Any]) -> None:
    """Close a single pooled connection."""
    transport: _Session | None = entry.pop("transport", None)
    if transport is not None:
        with contextlib.suppress(Exception):
            await transport.__aexit__(None, None, None)
    _POOL.pop(key, None)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def call_mcp(prefix: str, tool_name: str, args_json: str = "{}") -> str:
    """Call a tool on an MCP server with connection pooling.

    Args:
        prefix: MCP server prefix (e.g. PW, FEISHU, MEDIA).
        tool_name: Name of the tool to call.
        args_json: JSON string of arguments (default ``"{}"``).

    Returns:
        Tool result as a formatted string.
    """
    try:
        args: dict[str, Any] = json.loads(args_json) if args_json.strip() else {}
    except json.JSONDecodeError as e:
        return f"[Error] Invalid args_json: {e}"

    key = prefix.upper()
    entry: dict[str, Any] | None = None

    try:
        session, entry = await _get_or_create(prefix)
        result = await session.call_tool(tool_name, args)
        entry["last_used"] = time.monotonic()
        return _format_result(result)
    except Exception as e:
        # Force reconnect on next call
        if entry is not None:
            await _close_one(key, entry)
        return f"[Error] MCP call '{prefix}/{tool_name}' failed: {e}"


async def list_mcp_tools(prefix: str) -> str:
    """List all available tools on an MCP server.

    Args:
        prefix: MCP server prefix (e.g. PW, FEISHU, MEDIA).

    Returns:
        Formatted list of tool names and descriptions.
    """
    key = prefix.upper()
    entry: dict[str, Any] | None = None

    try:
        session, entry = await _get_or_create(prefix)
        tools_response = await session.list_tools()
        entry["last_used"] = time.monotonic()

        if not tools_response.tools:
            return f"No tools found on MCP server '{prefix}'."

        lines: list[str] = []
        for t in tools_response.tools:
            desc = t.description or ""
            lines.append(f"- {t.name}: {desc}" if desc else f"- {t.name}")
        return "\n".join(lines)
    except Exception as e:
        if entry is not None:
            await _close_one(key, entry)
        return f"[Error] Failed to list tools for '{prefix}': {e}"


async def close_mcp(prefix: str = "*") -> str:
    """Close MCP connection(s).

    Args:
        prefix: Server prefix to close, or ``"*"`` to close all.

    Returns:
        Summary of closed connections.
    """
    if prefix.strip() == "*":
        keys = list(_POOL.keys())
        for key in keys:
            entry = _POOL.get(key)
            if entry is not None:
                await _close_one(key, entry)
        if keys:
            return f"[OK] Closed {len(keys)} MCP connection(s): {', '.join(keys)}"
        return "[OK] No open MCP connections."

    key = prefix.upper()
    entry = _POOL.get(key)
    if entry is None:
        return f"[OK] No open connection for '{key}'."
    await _close_one(key, entry)
    return f"[OK] Closed MCP connection: {key}"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_result(result: Any) -> str:
    """Format an MCP tool result into a readable string."""
    parts = [
        b.text
        for b in getattr(result, "content", [])
        if isinstance(b, TextContent) and b.text
    ]
    if parts:
        prefix = "Error: " if getattr(result, "isError", False) else ""
        return prefix + "\n".join(parts)

    sc = getattr(result, "structuredContent", None)
    if sc is not None:
        try:
            return json.dumps(sc, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            pass

    if getattr(result, "isError", False):
        return "Error: " + str(result)
    return str(result)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup_sync() -> None:
    """atexit handler: close all pooled MCP connections."""
    if not _POOL:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_cleanup_async())  # noqa: RUF006
        else:
            loop.run_until_complete(_cleanup_async())
    except RuntimeError:
        with contextlib.suppress(Exception):
            asyncio.run(_cleanup_async())


async def _cleanup_async() -> None:
    """Close all pooled connections asynchronously."""
    keys = list(_POOL.keys())
    for key in keys:
        entry = _POOL.get(key)
        if entry is not None:
            await _close_one(key, entry)


atexit.register(_cleanup_sync)
