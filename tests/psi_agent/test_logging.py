from __future__ import annotations

import pytest

from psi_agent._logging import generate_trace_id, trace_context, trace_id_var


class MockRequest:
    def __init__(self, headers):
        self.headers = headers


@pytest.mark.anyio
async def test_trace_context_generates_id():
    request = MockRequest(headers={})

    assert trace_id_var.get() == "-"
    async with trace_context(request):  # type: ignore
        tid = trace_id_var.get()
        assert tid != "-"
        assert len(tid) == 8
    assert trace_id_var.get() == "-"


@pytest.mark.anyio
async def test_trace_context_uses_header():
    request = MockRequest(headers={"X-Trace-ID": "test-trace-id"})

    async with trace_context(request):  # type: ignore
        assert trace_id_var.get() == "test-trace-id"
    assert trace_id_var.get() == "-"


def test_generate_trace_id():
    tid = generate_trace_id()
    assert len(tid) == 8
    assert tid != generate_trace_id()
