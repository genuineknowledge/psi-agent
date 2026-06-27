from __future__ import annotations

import sys

from loguru import logger


def setup_logging(*, verbose: bool = False) -> int:
    logger.remove()
    logger.configure(extra={"trace_id": "none"})
    level = "DEBUG" if verbose else "INFO"
    handler_id = logger.add(
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
    return handler_id
