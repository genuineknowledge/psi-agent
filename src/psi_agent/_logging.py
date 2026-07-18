from __future__ import annotations

import functools
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

_handler_id: int | None = None

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def patch_record(record: Any) -> None:
    """Inject current trace_id from contextvar into the log record."""
    trace_id = trace_id_var.get()
    record["extra"]["trace_id"] = trace_id or "-"


@asynccontextmanager
async def trace_context(request: Any = None) -> AsyncIterator[str]:
    """Async context manager to capture and set trace_id from a request or generate a new one."""
    trace_id = None
    if request is not None and hasattr(request, "headers"):
        trace_id = request.headers.get("X-Trace-ID")
    if not trace_id:
        trace_id = str(uuid.uuid4())

    token = trace_id_var.set(trace_id)
    try:
        yield trace_id
    finally:
        trace_id_var.reset(token)


@web.middleware
async def trace_middleware(request: web.Request, handler: Any) -> Any:
    """aiohttp middleware to automatically wrap requests in a trace context."""
    async with trace_context(request):
        response = await handler(request)
        if isinstance(response, (web.StreamResponse, web.Response)):
            trace_id = trace_id_var.get()
            if trace_id:
                response.headers["X-Trace-ID"] = trace_id
        return response


def retry_async(attempts: int = 3, delay: float = 1.0, backoff: float = 2.0) -> Any:
    """Decorator to retry an async function with exponential backoff."""

    def decorator(func: Any) -> Any:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            for attempt in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == attempts - 1:
                        logger.error(f"Failed after {attempts} attempts: {e!r}")
                        raise
                    logger.warning(f"Attempt {attempt + 1} failed: {e!r}. Retrying in {current_delay}s...")
                    await anyio.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator


def setup_logging(*, verbose: bool = False) -> int:
    """Install the loguru stderr handler once and return its id.

    Deliberately one-shot: guarded by the module-global ``_handler_id``, the
    first call installs the handler and every subsequent call is a no-op that
    returns the existing id ``without`` re-applying ``verbose``. Whoever calls
    first wins the level. In ``psi-agent run`` (batch mode) ``Run.run()`` calls
    ``setup_logging(verbose=True)`` before any child component, so batch mode is
    always DEBUG and each component's own ``verbose`` field is intentionally
    ignored. Running a component standalone lets its own ``verbose`` decide.
    """
    global _handler_id
    if _handler_id is not None:
        return _handler_id
    logger.remove()
    level = "DEBUG" if verbose else "INFO"

    # Configure logger patcher
    logger.configure(patcher=patch_record)

    _handler_id = logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<magenta>{extra[trace_id]}</magenta> - "
            "<level>{message}</level>"
        ),
    )
    return _handler_id
