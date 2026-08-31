from __future__ import annotations

import aiohttp
import pytest

from psi_agent.runtime._summary_manager import SummaryManager
from psi_agent.runtime._title_manager import TitleManager
from psi_agent.session.ai_client import AiClient


class AsyncContent:
    def __init__(self, items: list[bytes] | None = None) -> None:
        self._items = items or []

    def __aiter__(self) -> AsyncContent:
        self._iter = iter(self._items)
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.anyio
async def test_ai_client_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_timeout: aiohttp.ClientTimeout | None = None

    class DummyResponse:
        status = 200

        @property
        def content(self):
            return AsyncContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class DummySession:
        def __init__(self, connector=None, timeout=None):
            nonlocal captured_timeout
            captured_timeout = timeout

        def post(self, endpoint, json):
            return DummyResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr(aiohttp, "ClientSession", DummySession)

    client = AiClient(ai_socket="http://127.0.0.1:9999")
    async for _ in client.stream({"messages": []}):
        pass

    assert captured_timeout is not None
    assert captured_timeout.total is None
    assert captured_timeout.connect == 30.0


@pytest.mark.anyio
async def test_title_manager_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_timeout: aiohttp.ClientTimeout | None = None

    class DummyResponse:
        status = 200

        @property
        def content(self):
            return AsyncContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class DummySession:
        def __init__(self, connector=None, timeout=None):
            nonlocal captured_timeout
            captured_timeout = timeout

        def post(self, endpoint, json):
            return DummyResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("psi_agent.runtime._title_manager.ClientSession", DummySession)

    tm = TitleManager()
    await tm.generate("session_1", "http://127.0.0.1:9999", "hi", "hello")

    assert captured_timeout is not None
    assert captured_timeout.total is None
    assert captured_timeout.connect == 30.0


@pytest.mark.anyio
async def test_summary_manager_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_timeout: aiohttp.ClientTimeout | None = None

    class DummyResponse:
        status = 200

        @property
        def content(self):
            return AsyncContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class DummySession:
        def __init__(self, connector=None, timeout=None):
            nonlocal captured_timeout
            captured_timeout = timeout

        def post(self, endpoint, json):
            return DummyResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("psi_agent.runtime._summary_manager.ClientSession", DummySession)

    sm = SummaryManager()
    await sm.generate("session_1", "http://127.0.0.1:9999", "hi", "hello")

    assert captured_timeout is not None
    assert captured_timeout.total is None
    assert captured_timeout.connect == 30.0
