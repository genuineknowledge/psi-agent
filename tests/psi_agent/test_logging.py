from __future__ import annotations

import pytest
from loguru import logger

import psi_agent._logging as _logging
from psi_agent._logging import setup_logging, trace_context, trace_id_var


def test_setup_logging_default_info() -> None:
    _logging._handler_id = None
    handler_id = setup_logging(verbose=False)
    assert isinstance(handler_id, int)
    logger.remove(handler_id)
    _logging._handler_id = None


def test_setup_logging_verbose_debug() -> None:
    _logging._handler_id = None
    handler_id = setup_logging(verbose=True)
    assert isinstance(handler_id, int)
    logger.remove(handler_id)
    _logging._handler_id = None


@pytest.mark.anyio
async def test_trace_context_generates_new_id() -> None:
    assert trace_id_var.get() == "system"
    async with trace_context(None) as trace_id:
        assert len(trace_id) == 32
        assert trace_id_var.get() == trace_id
    assert trace_id_var.get() == "system"


@pytest.mark.anyio
async def test_trace_context_reuses_existing_id_from_headers() -> None:
    class FakeRequest:
        def __init__(self) -> None:
            self.headers = {"X-Trace-ID": "test-headers-1234"}

    async with trace_context(FakeRequest()) as trace_id:
        assert trace_id == "test-headers-1234"
        assert trace_id_var.get() == "test-headers-1234"


@pytest.mark.anyio
async def test_trace_context_reuses_existing_id_from_dict() -> None:
    fake_dict = {"X-Trace-ID": "test-dict-1234"}
    async with trace_context(fake_dict) as trace_id:
        assert trace_id == "test-dict-1234"
        assert trace_id_var.get() == "test-dict-1234"


@pytest.mark.anyio
async def test_trace_patcher_exposes_trace_id_in_loguru() -> None:
    _logging._handler_id = None
    captured_records = []

    def mock_sink(message):
        captured_records.append(message.record)

    logger.remove()
    logger.configure(patcher=_logging._patcher)
    logger.add(mock_sink, level="DEBUG")

    logger.info("Message outside trace context")
    assert captured_records[-1]["extra"]["trace_id"] == "system"

    async with trace_context({"X-Trace-ID": "abc-123"}):
        logger.info("Message inside trace context")
        assert captured_records[-1]["extra"]["trace_id"] == "abc-123"

    logger.info("Message outside trace context again")
    assert captured_records[-1]["extra"]["trace_id"] == "system"

    logger.remove()
