from __future__ import annotations

from loguru import logger

import psi_agent._logging as _logging
from psi_agent._logging import setup_logging


def test_setup_logging_default_info() -> None:
    _logging._handler_id = None
    handler_id = setup_logging(verbose=False)
    assert isinstance(handler_id, int)
    logger.remove(handler_id)


def test_setup_logging_verbose_debug() -> None:
    _logging._handler_id = None
    handler_id = setup_logging(verbose=True)
    assert isinstance(handler_id, int)
    logger.remove(handler_id)
