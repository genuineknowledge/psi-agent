from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from loguru import logger

_handler_id: int | None = None

# ContextVar holding the current active trace ID (defaults to "system" when not in a request context)
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="system")


@asynccontextmanager
async def trace_context(request_or_headers: Any) -> AsyncIterator[str]:
    """Async context manager to activate a given trace ID.

    If a trace ID is supplied in headers or dictionary under 'X-Trace-ID',
    we reuse it. Otherwise, we generate a fresh high-entropy UUID trace ID.
    """
    trace_id = ""
    if request_or_headers is not None:
        if hasattr(request_or_headers, "headers"):
            trace_id = request_or_headers.headers.get("X-Trace-ID", "")
        elif hasattr(request_or_headers, "get"):
            trace_id = request_or_headers.get("X-Trace-ID", "")
    if not trace_id:
        trace_id = uuid.uuid4().hex

    token = trace_id_var.set(trace_id)
    try:
        yield trace_id
    finally:
        trace_id_var.reset(token)


def _patcher(record: Any) -> None:
    """Loguru record patcher to automatically expose trace_id under extra."""
    record["extra"]["trace_id"] = trace_id_var.get()


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
    logger.configure(patcher=_patcher)
    level = "DEBUG" if verbose else "INFO"
    _handler_id = logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<yellow>[{extra[trace_id]}]</yellow> - "
            "<level>{message}</level>"
        ),
    )
    return _handler_id
