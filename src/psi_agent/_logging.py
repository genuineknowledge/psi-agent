from __future__ import annotations

import sys

from loguru import logger

_handler_id: int | None = None


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
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<magenta>{extra[trace_id]}</magenta> - "
            "<level>{message}</level>"
        ),
    )
    logger.configure(extra={"trace_id": "-"})
    return _handler_id
