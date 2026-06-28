from __future__ import annotations

import sys

from loguru import logger

_handler_id: int | None = None


def setup_logging(*, verbose: bool = False) -> int:
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
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    return _handler_id
