from __future__ import annotations

import contextlib
import sys
import uuid
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

_handler_id: int | None = None
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


@contextlib.asynccontextmanager
async def trace_context(request: web.Request | None = None) -> AsyncIterator[str]:
    """Context manager to manage the trace_id for the current task.

    If *request* is provided, extracts ``X-Trace-ID`` header or generates a
    new one. Sets the ``trace_id_var`` ContextVar.
    """
    trace_id = ""
    if request is not None:
        trace_id = request.headers.get("X-Trace-ID", "")
    if not trace_id:
        trace_id = uuid.uuid4().hex[:8]

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
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Exponential backoff retry decorator for async functions.

    Use it sparingly, only for idempotent operations (like initial upstream
    connection).
    """

    def decorator(func: Any) -> Any:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err = None
            curr_delay = delay
            for i in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_err = e
                    if i < attempts - 1:
                        logger.warning(f"Retry {i + 1}/{attempts} for {func.__name__} after {curr_delay}s: {e!r}")
                        await anyio.sleep(curr_delay)
                        curr_delay *= backoff
            raise last_err  # type: ignore

        return wrapper

    return decorator


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
    _handler_id = logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<magenta>{extra[trace_id]}</magenta> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logger.configure(patcher=lambda record: record["extra"].update(trace_id=trace_id_var.get()))
    return _handler_id
