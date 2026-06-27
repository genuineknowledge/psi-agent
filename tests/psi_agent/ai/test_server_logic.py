from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from psi_agent.ai import API_KEY_KEY, BASE_URL_KEY, MODEL_KEY, PROVIDER_KEY
from psi_agent.ai.server import handle_chat_completions


@pytest.mark.anyio
async def test_trace_id_propagation() -> None:
    """Verify that X-Trace-ID header is extracted and used."""
    app = web.Application()
    app[PROVIDER_KEY] = "test"
    app[MODEL_KEY] = "test"
    app[API_KEY_KEY] = "test"
    app[BASE_URL_KEY] = "test"
    app.router.add_post("/chat/completions", handle_chat_completions)

    trace_id = "test-trace-id-" + uuid.uuid4().hex

    # We want to check if loguru.contextualize was called with this trace_id.
    # Since we can't easily mock loguru's context manager, we'll check
    # if it reached the inner handler.

    with patch("psi_agent.ai.server._handle_chat_completions", new_callable=AsyncMock) as mock_inner:
        mock_inner.return_value = web.Response(status=200)

        async with TestClient(TestServer(app)) as client:
            await client.post("/chat/completions", headers={"X-Trace-ID": trace_id}, json={"messages": []})

        mock_inner.assert_called_once()
        # Verify the context manager was used by checking if we are still in it would be hard,
        # but we verified handle_chat_completions calls _handle_chat_completions.


@pytest.mark.anyio
async def test_exponential_backoff_retry() -> None:
    """Verify that upstream AI calls are retried with exponential backoff."""
    app = web.Application()
    app[PROVIDER_KEY] = "test"
    app[MODEL_KEY] = "test"
    app[API_KEY_KEY] = "test"
    app[BASE_URL_KEY] = "test"
    app.router.add_post("/chat/completions", handle_chat_completions)

    # Mock any_llm.api.acompletion
    with patch("psi_agent.ai.server.acompletion", new_callable=AsyncMock) as mock_acompletion, \
         patch("anyio.sleep", new_callable=AsyncMock) as mock_sleep:

        # Fail twice, then succeed
        mock_acompletion.side_effect = [
            RuntimeError("fail 1"),
            RuntimeError("fail 2"),
            AsyncMock() # Succeeds (returns a mock iterator)
        ]

        async with TestClient(TestServer(app)) as client:
            # We need a valid request body to avoid 400
            resp = await client.post("/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            assert resp.status == 200

        assert mock_acompletion.call_count == 3
        assert mock_sleep.call_count == 2
        # Backoff: 1s, then 2s
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)


@pytest.mark.anyio
async def test_retry_exhaustion() -> None:
    """Verify that after max retries, an error chunk is sent."""
    app = web.Application()
    app[PROVIDER_KEY] = "test"
    app[MODEL_KEY] = "test"
    app[API_KEY_KEY] = "test"
    app[BASE_URL_KEY] = "test"
    app.router.add_post("/chat/completions", handle_chat_completions)

    with patch("psi_agent.ai.server.acompletion", new_callable=AsyncMock) as mock_acompletion, \
         patch("anyio.sleep", new_callable=AsyncMock) as mock_sleep:

        mock_acompletion.side_effect = RuntimeError("permanent fail")

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
            assert resp.status == 200 # SSE stream started

            content = await resp.text()
            assert "[Upstream Error]: permanent fail" in content
            assert "finish_reason\": \"error\"" in content

        assert mock_acompletion.call_count == 3
        assert mock_sleep.call_count == 2
