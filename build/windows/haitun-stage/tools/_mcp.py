from __future__ import annotations

import concurrent.futures
import inspect
import json
import sys
from collections.abc import Callable, Mapping
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


class MCPError(RuntimeError):
    pass


_T = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def mcp(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: auto-discover MCP tools at import time."""
    config = _resolve(func())
    schemas = _discover(config)
    prefix = getattr(func, "__name__", "mcp") + "_"
    g = sys._getframe(1).f_globals
    prepend = func.__doc__
    for name, schema in schemas.items():
        g[prefix + name] = _build(prefix + name, schema, config, prepend)
    return func


def _discover(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    async def _go() -> dict[str, dict[str, Any]]:
        s: dict[str, dict[str, Any]] = {}
        async with _connect(config) as session:
            await session.initialize()
            for t in (await session.list_tools()).tools:
                s[t.name] = {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
        return s

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(anyio.run, _go).result()  # ty: ignore


def _build(name: str, schema: dict[str, Any], config: dict[str, Any], prepend_doc: str | None = None) -> Any:
    props = schema.get("inputSchema", {}).get("properties", {})
    req: list[str] = schema.get("inputSchema", {}).get("required", [])
    params: list[inspect.Parameter] = []
    ann: dict[str, Any] = {}
    for pn, ps in props.items():
        jt = ps.get("type", "string")
        pt = _T.get(jt, str)
        if jt == "array":
            pt = list[_T.get(ps.get("items", {}).get("type", "string"), str)]  # type: ignore[valid-type]  # ty: ignore
        r = pn in req
        ann[pn] = pt | None if not r else pt
        params.append(
            inspect.Parameter(
                pn, inspect.Parameter.KEYWORD_ONLY, default=inspect.Parameter.empty if r else None, annotation=ann[pn]
            )
        )
    ann["return"] = str
    desc = schema.get("description", "")
    doc = (
        (prepend_doc.strip() + "\n\n" if prepend_doc else "")
        + desc
        + "\n\nArgs:\n"
        + "\n".join(
            f"    {p}: {ps.get('description', '')}{'' if p in req else ' (optional)'}" for p, ps in props.items()
        )
    )
    tn, cfg = schema["name"], config

    async def _fn(**kw: Any) -> str:
        async with _connect(cfg) as session:
            await session.initialize()
            r = await session.call_tool(tn, kw)
        return _fmt(r)

    _fn.__name__ = name
    _fn.__qualname__ = name
    _fn.__doc__ = doc
    _fn.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]  # ty: ignore
    _fn.__annotations__ = ann
    _fn.__wrapped__ = None  # type: ignore[attr-defined]  # ty: ignore
    return _fn


def _fmt(result: Any) -> str:
    parts = [b.text for b in getattr(result, "content", []) if isinstance(b, TextContent) and b.text]
    if parts:
        return ("Error: " if getattr(result, "isError", False) else "") + "\n".join(parts)
    sc = getattr(result, "structuredContent", None)
    try:
        return (
            json.dumps(sc, ensure_ascii=False)
            if sc
            else ("Error: " + str(result) if getattr(result, "isError", False) else str(result))
        )
    except TypeError:
        return str(result)


class _Session:
    def __init__(self, opener):
        self._o = opener
        self._c: Any = None
        self._s: ClientSession | None = None

    async def __aenter__(self):
        self._c = self._o()
        r, w, *_ = await self._c.__aenter__()
        self._s = ClientSession(r, w)
        return await self._s.__aenter__()

    async def __aexit__(self, *a):
        if self._s is not None:
            await self._s.__aexit__(*a)
        if self._c is not None:
            await self._c.__aexit__(*a)


def _connect(config: dict[str, Any]):
    t = config["transport"]
    if t == "coroutine":
        return _Session(config["fn"])
    if t == "stdio":
        p = StdioServerParameters(command=config["command"], args=config.get("args", []), env=config.get("env") or None)
        return _Session(lambda: stdio_client(p))
    if t == "sse":
        return _Session(lambda: sse_client(config["url"]))
    return _Session(lambda: streamable_http_client(config["url"]))


def _resolve(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise MCPError(f"MCP declaration must return a mapping: {raw!r}")
    d: dict[str, Any] = dict(raw)
    srv: Any = next((d.pop(k) for k in ("server", "mcpServer", "mcp_server", "command", "cmd") if k in d), None)
    transport = _opt(d.pop("transport", None) or d.pop("type", None))
    url = _opt(d.pop("url", None) or d.pop("endpoint", None))
    cmd: str | None = None
    args: list[str] = []
    if isinstance(srv, str):
        if srv.startswith(("http://", "https://")):
            url = url or srv
        else:
            p = srv.split()
            cmd, args = p[0], p[1:]
    elif isinstance(srv, (list, tuple)):
        p = [str(v) for v in srv]
        if transport == "http":
            url = url or p[0]
        else:
            cmd, args = p[0], p[1:]
    if isinstance(d.get("args"), list):
        args.extend([str(a) for a in d.pop("args")])
    if transport is None:
        transport = "http" if url else "stdio"
    transport = transport.lower().replace("-", "_").replace("local", "stdio")
    if transport == "coroutine":
        fn = d.get("fn")
        if not callable(fn):
            raise MCPError("coroutine transport requires a callable 'fn'")
        return {"transport": "coroutine", "fn": fn}
    if transport not in {"stdio", "http", "sse"}:
        raise MCPError(f"Unsupported transport: {transport!r}")
    if transport == "stdio" and not cmd:
        raise MCPError("stdio transport requires 'server' or 'command'")
    if transport in {"http", "sse"} and not url:
        raise MCPError(f"{transport} transport requires 'url' or HTTP 'server'")
    return {
        "transport": transport,
        "command": cmd,
        "args": args,
        "env": _sd(d.pop("env", None) or d.pop("environment", None)),
        "cwd": _opt(d.pop("cwd", None)),
        "url": url,
        "headers": _sd(d.pop("headers", None)),
    }


def _opt(value: Any) -> str | None:
    return v.strip() if (v := str(value).strip()) else None


def _sd(value: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, Mapping) else {}
