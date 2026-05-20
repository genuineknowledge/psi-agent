from __future__ import annotations

from loguru import logger

from psi_agent.logging import setup_logging


def test_setup_logging_default_info() -> None:
    logger.remove()
    handler_id = setup_logging(verbose=False)
    assert handler_id is not None
    logger.remove(handler_id)


def test_setup_logging_verbose_debug() -> None:
    logger.remove()
    handler_id = setup_logging(verbose=True)
    assert handler_id is not None
    logger.remove(handler_id)
