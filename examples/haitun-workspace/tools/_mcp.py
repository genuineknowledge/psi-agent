from __future__ import annotations

import concurrent.futures
import inspect
import json
import pathlib
import sys
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, suppress
from typing import Any

import anyio
from loguru import logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


class MCPError(RuntimeError):
    pass


def _is_fatal(exc: BaseException) -> bool:
    """True for exceptions that must always propagate (never be swallowed).

    A misbehaving MCP server (HTTP 502, timeout, dropped connection) surfaces as an
    ``Exception`` — but its anyio/httpx teardown can *also* raise
    ``BaseExceptionGroup`` and ``GeneratorExit`` (both ``BaseException``, not
    ``Exception``). Those slip past a plain ``except Exception`` and, uncaught, take
    the whole gateway process down. We contain everything MCP-related here, while
    still letting genuine interpreter-control signals through.
    """
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        # A group is fatal only if it contains a fatal leaf.
        return exc.subgroup((KeyboardInterrupt, SystemExit)) is not None
    return False


def _describe(exc: BaseException) -> str:
    """Human-readable cause of *exc*, unwrapping anyio/httpx exception groups.

    The MCP streamable-HTTP client runs its transport in an anyio task group, so a plain
    "connection refused" reaches us as ``ExceptionGroup: unhandled errors in a TaskGroup
    (1 sub-exception)`` — a message that names neither the endpoint nor the real error and
    sent debugging down the wrong path (it reads like a bug in our own task handling).
    Flatten the group and report the leaf exceptions instead.
    """
    leaves: list[str] = []

    def _walk(e: BaseException, depth: int = 0) -> None:
        if isinstance(e, BaseExceptionGroup) and depth < 5:
            for sub in e.exceptions:
                _walk(sub, depth + 1)
            return
        text = str(e).strip()
        leaves.append(f"{type(e).__name__}: {text}" if text else type(e).__name__)

    _walk(exc)
    # Preserve order while dropping duplicates (a group often repeats the same leaf).
    seen: set[str] = set()
    unique = [x for x in leaves if not (x in seen or seen.add(x))]
    return "; ".join(unique) if unique else repr(exc)


_T = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def _json_type(ps: Mapping[str, Any]) -> str:
    """Return a single JSON-Schema type string for a property.

    A property's ``type`` may be a **list** (JSON Schema union, e.g.
    ``["string", "null"]`` or ``["number", "array"]``) or absent entirely (when
    the property uses ``anyOf`` / ``oneOf`` / ``enum`` instead). Some MCP servers
    (e.g. Excalidraw) emit union types; feeding a ``list`` straight into
    ``_T.get`` raised ``TypeError: unhashable type: 'list'`` and crashed tool
    loading. We collapse to the first non-``null`` member (falling back to
    ``string``) so the generated signature stays a simple, hashable type.
    """
    t = ps.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if not isinstance(t, str):
        return "string"
    return t


def mcp(
    func: Callable[..., Any] | None = None,
    *,
    dispatch: bool = False,
    keep: tuple[str, ...] = (),
) -> Any:
    """Decorator: generate one workspace tool per MCP server tool.

    Generated tool names default to ``<func name>_<mcp tool name>`` (so
    ``serper`` -> ``serper_search``). A declaration may set ``prefix`` to
    override this — e.g. ``prefix=""`` when the MCP server's own tool names
    already carry the desired prefix (Playwright MCP exposes ``browser_*``),
    which would otherwise double up to ``browser_browser_navigate``.

    **Dispatch mode.** Every generated schema is resident in the request on
    every turn, and an MCP server's schemas are written upstream — we cannot
    shorten them, only choose how many to expose. ``dispatch=True`` therefore
    generates a *single* ``<name>_call(tool, args_json)`` tool that can reach
    every tool on the server, and moves the per-tool parameter documentation
    into a skill the model reads on demand. Measured on this workspace, the
    browser (42 tools) and canvas (26) groups cost 36% of all tool schema
    bytes; dispatching both reclaims ~35%.

    ``keep`` names the tools that stay as their own generated function anyway —
    for the handful called often enough that an extra indirection is not worth
    it. Names are matched after prefixing (``browser_navigate``), and a name
    that the server does not expose is logged and ignored rather than failing
    tool loading, so a rename upstream cannot break the workspace.

    **Lazy startup.** The workspace loader imports tool files *synchronously*
    (a blocking ``exec``), so anything a decorator does at import time — spawning
    an ``npx`` MCP server, connecting to enumerate tools — stalls session
    startup (Playwright MCP costs ~90s cold). To avoid that we:

    1. Read the tool **schemas from an on-disk cache** (``.mcp_cache/<name>.json``)
       when present, so import touches neither ``func()`` nor the server. The
       ``browser_*``/``canvas_*`` tool set is stable, so a committed cache is
       authoritative.
    2. Defer the real config — and therefore ``ensure_server()`` / spawning —
       to **call time** of a generated tool (see ``config_provider``). It is
       re-resolved on every call, so a server that died can be respawned.
    3. Fall back to live discovery only on a cache miss, then **persist** the
       result so subsequent imports are instant.
    """
    # Support both bare ``@mcp`` and parameterised ``@mcp(dispatch=True)``.
    if func is None:

        def _defer(f: Callable[..., Any]) -> Callable[..., Any]:
            return _apply(f, dispatch=dispatch, keep=keep, depth=2)

        return _defer
    return _apply(func, dispatch=dispatch, keep=keep, depth=2)


def _apply(
    func: Callable[..., Any],
    *,
    dispatch: bool,
    keep: tuple[str, ...],
    depth: int,
) -> Callable[..., Any]:
    """Do the work of :func:`mcp`. *depth* is the frame holding the caller's globals."""
    prefix_hint = getattr(func, "__name__", "mcp")
    name = prefix_hint

    def config_provider() -> dict[str, Any]:
        """Resolve the transport config on **every** tool call, never once.

        This used to memoize the first result, which quietly broke self-healing for
        declarations whose body *supervises a server* (``browser`` calls
        ``_browser_impl.ensure_server()``). With the config cached, the declaration body
        ran exactly once per process: if the Playwright MCP server later died or went
        half-dead, every subsequent call kept dialing the stale endpoint and there was no
        code path left that could notice or respawn it — the browser tools stayed broken
        until the whole gateway was restarted.

        Re-resolving per call puts ``ensure_server()`` back in the request path, where its
        own health check can replace a dead server. The declaration bodies are cheap and
        idempotent by contract (a healthy server is a lock + a poll + an HTTP probe), so
        this costs microseconds on the happy path.
        """
        return _resolve(func())

    prefix, schemas = _load_cached_schemas(name)
    if schemas is None:
        # Cache miss — pay the discovery cost now (blocking) and cache it.
        try:
            config = config_provider()
            prefix = config.get("prefix")
            schemas = _discover(config)
        except BaseException as exc:  # contain MCP/anyio teardown (see _is_fatal); re-raise only fatals
            if _is_fatal(exc):
                raise
            # The MCP server was unreachable / errored during discovery (e.g. Playwright
            # MCP returning 502, or npx missing). Degrade gracefully: register no tools and
            # keep loading the rest of the workspace, rather than letting the failure — which
            # can surface as a BaseExceptionGroup — crash tool loading and the gateway.
            logger.warning(f"MCP tool discovery for {name!r} failed; skipping its tools: {exc!r}")
            return func
        _save_cached_schemas(name, prefix, schemas)

    if prefix is None:
        prefix = name + "_"
    g = sys._getframe(depth).f_globals
    prepend = func.__doc__

    if not dispatch:
        for tool_name, schema in schemas.items():
            g[prefix + tool_name] = _build(prefix + tool_name, schema, config_provider, prepend)
        return func

    # Dispatch mode: one generic entrypoint, plus the explicitly kept tools.
    unknown = [k for k in keep if k[len(prefix) :] not in schemas and k not in schemas]
    if unknown:
        # An upstream rename must not break tool loading — the dispatcher can
        # still reach whatever the server does expose.
        logger.warning(f"@mcp(keep=...) for {name!r} names tool(s) the server does not expose: {unknown}")
    kept = 0
    for tool_name, schema in schemas.items():
        if prefix + tool_name in keep or tool_name in keep:
            g[prefix + tool_name] = _build(prefix + tool_name, schema, config_provider, prepend)
            kept += 1
    g[name + "_call"] = _build_dispatch(name, prefix, schemas, config_provider, prepend)
    logger.debug(f"MCP {name!r} in dispatch mode: 1 dispatcher + {kept} kept of {len(schemas)} tool(s)")
    return func


def _cache_path(name: str) -> pathlib.Path:
    """Path to the on-disk schema cache for MCP declaration *name*."""
    return pathlib.Path(__file__).resolve().parent / ".mcp_cache" / f"{name}.json"


def _load_cached_schemas(name: str) -> tuple[str | None, dict[str, dict[str, Any]] | None]:
    """Return ``(prefix, schemas)`` from the on-disk cache, or ``(None, None)`` on miss.

    The cache holds the *tool schemas* only — never a live connection — so it is
    safe to read on the (blocking) import path. A malformed/absent cache is a
    miss, so the caller falls back to live discovery."""
    path = _cache_path(name)
    try:
        if not path.is_file():
            return None, None
        data = json.loads(path.read_text(encoding="utf-8"))
        schemas = data["schemas"]
        if not isinstance(schemas, dict):
            return None, None
        logger.debug(f"Loaded {len(schemas)} cached MCP schema(s) for {name!r} from {path}")
        return data.get("prefix"), schemas
    except Exception as exc:
        # Any cache problem => treat as a miss and fall back to live discovery.
        logger.warning(f"Ignoring unreadable MCP schema cache for {name!r} ({path}): {exc!r}")
        return None, None


def _save_cached_schemas(name: str, prefix: str | None, schemas: dict[str, dict[str, Any]]) -> None:
    """Persist freshly discovered *schemas* so later imports skip live discovery."""
    path = _cache_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"prefix": prefix, "schemas": schemas}, indent=2), encoding="utf-8")
        logger.debug(f"Cached {len(schemas)} MCP schema(s) for {name!r} to {path}")
    except Exception as exc:
        # Caching is best-effort; never fail tool loading over a write error.
        logger.warning(f"Failed to write MCP schema cache for {name!r} ({path}): {exc!r}")


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


def _build_dispatch(
    name: str,
    prefix: str,
    schemas: dict[str, dict[str, Any]],
    config_provider: Callable[[], dict[str, Any]],
    prepend_doc: str | None = None,
) -> Any:
    """Build the single ``<name>_call`` tool that can reach every server tool.

    The call path is deliberately identical to :func:`_build`'s — resolve the
    config in a worker thread, connect, ``session.call_tool``, format — so a
    dispatched call behaves exactly like the generated wrapper it replaces
    (verified byte-for-byte on canvas reads, writes, and the error path).

    Two things here are load-bearing rather than incidental:

    - The **same containment** as ``_build``. Without it a transport failure
      propagates instead of becoming a tool error, which is what takes the
      gateway down (see ``_is_fatal``). This was caught by testing the two
      paths against a server that would not start: they only agreed once the
      dispatcher grew the identical ``except``.
    - **Both spellings of the tool name are accepted.** The schema keys are the
      server's own names, which may or may not already carry *prefix*
      (Playwright exposes ``browser_navigate``; Excalidraw exposes
      ``create_element`` and gets ``canvas_`` prepended). The skill documents
      the prefixed name, so a model following it sends ``canvas_create_element``
      while the server wants ``create_element``.
    """
    listing = ", ".join(sorted(prefix + t for t in schemas))
    doc = (
        (prepend_doc.strip() + "\n\n" if prepend_doc else "")
        + f"Call any {name} tool. Read the ``{name}-mcp`` skill first: it documents each "
        f"tool's arguments, which are NOT described here.\n\n"
        "Args:\n"
        f"    tool: Which tool to call. One of: {listing}\n"
        "    args_json: That tool's arguments as a JSON object string, e.g. "
        "``'{\"x\": 10}'``. Omit or pass ``{}`` for a tool that takes none.\n"
    )

    async def _fn(tool: str, args_json: str = "") -> str:
        bare = tool[len(prefix) :] if prefix and tool.startswith(prefix) else tool
        if bare not in schemas:
            # Refuse locally: a typo must not reach the server as a real call.
            return f"Error: unknown {name} tool {tool!r}. Available: {listing}"
        try:
            args = json.loads(args_json) if args_json.strip() else {}
        except json.JSONDecodeError as exc:
            return f"Error: args_json is not valid JSON ({exc}). Pass a JSON object string, e.g. '{{\"x\": 1}}'."
        if not isinstance(args, dict):
            return f"Error: args_json must be a JSON *object*, got {type(args).__name__}."
        try:
            cfg = await anyio.to_thread.run_sync(config_provider)  # ty: ignore
            async with _connect(cfg) as session:
                await session.initialize()
                r = await session.call_tool(bare, args)
            return _fmt(r)
        except BaseException as exc:  # contain MCP/anyio teardown (see _is_fatal); re-raise only fatals
            if _is_fatal(exc):
                raise
            logger.warning(f"MCP tool {bare!r} call failed: {exc!r}")
            return f"Error: MCP tool {bare!r} failed: {_describe(exc)}"

    _fn.__name__ = name + "_call"
    _fn.__qualname__ = name + "_call"
    _fn.__doc__ = doc
    return _fn


def _build(
    name: str,
    schema: dict[str, Any],
    config_provider: Callable[[], dict[str, Any]],
    prepend_doc: str | None = None,
) -> Any:
    """Build one workspace tool function from an MCP tool *schema*.

    *config_provider* returns the resolved MCP transport config, evaluated
    **lazily on each call** — so a cache-backed tool never touches the server
    (or ``ensure_server()``) until the agent actually invokes it, and a server
    that has since died gets a chance to be respawned."""
    props = schema.get("inputSchema", {}).get("properties", {})
    req: list[str] = schema.get("inputSchema", {}).get("required", [])
    params: list[inspect.Parameter] = []
    ann: dict[str, Any] = {}
    # Params the tool runtime cannot express as a simple signature type (nested
    # objects, arrays of objects). We expose them as JSON-string params and
    # decode them back before the MCP call — see json_params below and _fn.
    json_params: set[str] = set()
    for pn, ps in props.items():
        jt = _json_type(ps)
        pt = _T.get(jt, str)
        if jt == "object":
            # The runtime only accepts str/int/float/bool/list[scalar]; a nested
            # object has no such type. Take it as a JSON string the tool decodes.
            pt = str
            json_params.add(pn)
        elif jt == "array":
            item_t = _json_type(ps.get("items", {})) if isinstance(ps.get("items"), Mapping) else "string"
            if item_t in ("object", "array"):
                # array of objects/arrays -> list[dict]/list[list], also rejected
                # by the runtime; pass the whole array as a JSON string instead.
                pt = str
                json_params.add(pn)
            else:
                pt = list[_T.get(item_t, str)]  # type: ignore[valid-type]  # ty: ignore
        r = pn in req
        ann[pn] = pt | None if not r else pt
        params.append(
            inspect.Parameter(
                pn, inspect.Parameter.KEYWORD_ONLY, default=inspect.Parameter.empty if r else None, annotation=ann[pn]
            )
        )
    ann["return"] = str
    desc = schema.get("description", "")

    def _arg_line(p: str, ps: Mapping[str, Any]) -> str:
        hint = ps.get("description", "")
        if p in json_params:
            # Tell the model this arg is a JSON string, not a native object/array.
            hint = (hint + " " if hint else "") + "(pass as a JSON string)"
        return f"    {p}: {hint}{'' if p in req else ' (optional)'}"

    doc = (
        (prepend_doc.strip() + "\n\n" if prepend_doc else "")
        + desc
        + "\n\nArgs:\n"
        + "\n".join(_arg_line(p, ps) for p, ps in props.items())
    )
    tn = schema["name"]

    def _decode(kw: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON-string args back into objects/arrays before the MCP call.

        Only ``json_params`` are decoded, and only when they arrive as strings
        (the model may already send a structured value). A non-JSON string is
        left as-is so the server can surface its own validation error.
        """
        for p in json_params:
            v = kw.get(p)
            if isinstance(v, str) and v.strip():
                with suppress(json.JSONDecodeError):
                    kw[p] = json.loads(v)
        return kw

    async def _fn(**kw: Any) -> str:
        try:
            # Resolve config lazily on first call. This is where a deferred
            # server actually starts (e.g. ``ensure_server()`` spawning npx and
            # blocking ~90s the first time) — run it in a worker thread so a
            # cold start never stalls the shared event loop / other sessions.
            cfg = await anyio.to_thread.run_sync(config_provider)  # ty: ignore
            async with _connect(cfg) as session:
                await session.initialize()
                r = await session.call_tool(tn, _decode(kw))
            return _fmt(r)
        except BaseException as exc:  # contain MCP/anyio teardown (see _is_fatal); re-raise only fatals
            if _is_fatal(exc):
                raise
            # Report the failure back to the agent as a normal tool error instead of
            # letting it (possibly a BaseExceptionGroup from the transport teardown)
            # propagate and crash the session/gateway.
            logger.warning(f"MCP tool {tn!r} call failed: {exc!r}")
            return f"Error: MCP tool {tn!r} failed: {_describe(exc)}"

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
    """Open an MCP transport + :class:`ClientSession` as one async context.

    Uses an :class:`AsyncExitStack` so the session and transport unwind in the correct
    nested order via a single ``aclose()``. Teardown of the streamable-HTTP transport can
    raise ``RuntimeError("Attempted to exit cancel scope in a different task…")`` — an
    anyio quirk when the connection failed mid-flight — which we suppress so a *cleanup*
    error never masks the real failure nor escapes to crash the caller.
    """

    def __init__(self, opener):
        self._o = opener
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self):
        stack = AsyncExitStack()
        try:
            r, w, *_ = await stack.enter_async_context(self._o())
            session = await stack.enter_async_context(ClientSession(r, w))
        except BaseException:
            # Roll back anything already entered before re-raising the connect failure.
            with suppress(RuntimeError):
                await stack.aclose()
            raise
        self._stack = stack
        return session

    async def __aexit__(self, *a):
        if self._stack is not None:
            with suppress(RuntimeError):
                await self._stack.__aexit__(*a)
            self._stack = None


def _connect(config: dict[str, Any]):
    t = config["transport"]
    if t == "coroutine":
        return _Session(config["fn"])
    if t == "stdio":
        p = StdioServerParameters(command=config["command"], args=config.get("args", []), env=config.get("env") or None)
        return _Session(lambda: stdio_client(p))
    if t == "sse":
        return _Session(lambda: sse_client(config["url"]))
    # ``terminate_on_close`` defaults to True (send DELETE on disconnect). For servers
    # that bind stateful resources to the HTTP session — e.g. Playwright MCP ties the
    # open browser tab to the session — set it False so a per-call connection closing
    # doesn't reset the server's state between tool calls.
    terminate = config.get("terminate_on_close", True)
    return _Session(lambda: streamable_http_client(config["url"], terminate_on_close=terminate))


def _resolve(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise MCPError(f"MCP declaration must return a mapping: {raw!r}")
    d: dict[str, Any] = dict(raw)
    prefix = d.pop("prefix", None)
    terminate_on_close = bool(d.pop("terminate_on_close", True))
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
        return {"transport": "coroutine", "fn": fn, "prefix": prefix}
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
        "prefix": prefix,
        "terminate_on_close": terminate_on_close,
    }


def _opt(value: Any) -> str | None:
    return v.strip() if (v := str(value).strip()) else None


def _sd(value: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, Mapping) else {}
