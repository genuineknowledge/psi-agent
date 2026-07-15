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

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def setup_logging(*, verbose: bool = False) -> int:
    """Install the loguru stderr handler once and return its id.

    Deliberately one-shot: guarded by the module-global ``_handler_id``, the
    first call installs the handler and every subsequent call is a no-op that
    returns the existing id **without** re-applying ``verbose``. Whoever calls
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
    logger.configure(patcher=lambda record: record["extra"].update(trace_id=trace_id_var.get()))
    _handler_id = logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            " <cyan>{extra[trace_id]}</cyan> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    return _handler_id


def generate_trace_id() -> str:
    return str(uuid.uuid4())[:8]


@asynccontextmanager
async def trace_context(request: web.Request) -> AsyncIterator[str]:
    """Extract trace_id from headers or generate new one, and manage ContextVar."""
    trace_id = request.headers.get("X-Trace-ID") or generate_trace_id()
    token = trace_id_var.set(trace_id)
    try:
        yield trace_id
    finally:
        trace_id_var.reset(token)


def retry_async(
    *,
    attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: type[Exception] | tuple[type[Exception], ...] = Exception,
) -> Any:
    """Simple exponential backoff retry decorator for async functions."""

    def decorator(func: Any) -> Any:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            for attempt in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == attempts - 1:
                        raise
                    logger.warning(f"Attempt {attempt + 1} failed: {e!r}. Retrying in {current_delay}s...")
                    await anyio.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator
