"""Tests for the Haitun workspace ``x_search`` tool.

The X API is never hit: ``aiohttp.ClientSession`` in ``_x_search_impl`` is
monkeypatched to return a canned JSON response, so these exercise argument
validation, the missing-token config path, error mapping, and result shaping
without network access.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import aiohttp
import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_x_search_impl")
x_search_tool: Any = importlib.import_module("x_search")


class _FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    """Stands in for ``aiohttp.ClientSession``; records the requested params."""

    seen_url: ClassVar[str] = ""
    seen_params: ClassVar[dict[str, Any]] = {}

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        type(self).seen_url = url
        type(self).seen_params = params or {}
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _mock_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    payload: Any = None,
    error: Exception | None = None,
) -> None:
    """Patch ``aiohttp.ClientSession`` in the impl to return a canned response."""
    response = _FakeResponse(status, payload)
    _FakeSession.seen_url = ""
    _FakeSession.seen_params = {}

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeSession:
        return _FakeSession(error if error is not None else response)

    monkeypatch.setattr(impl.aiohttp, "ClientSession", _factory)


def _sample_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "1001",
                "text": "hello from space",
                "created_at": "2026-07-10T12:00:00.000Z",
                "author_id": "42",
                "lang": "en",
                "public_metrics": {
                    "retweet_count": 5,
                    "reply_count": 1,
                    "like_count": 20,
                    "quote_count": 2,
                },
            }
        ],
        "includes": {
            "users": [{"id": "42", "name": "NASA", "username": "nasa", "verified": True}],
        },
        "meta": {"result_count": 1},
    }


def test_tool_metadata_is_loadable() -> None:
    """The public tool must expose valid metadata for the ToolRegistry."""
    meta = ToolFunction.from_callable(x_search_tool.x_search)
    assert meta.name == "x_search"
    assert meta.description
    props = meta.parameters["properties"]
    assert set(props) == {"query", "max_results", "sort_order"}
    assert meta.parameters["required"] == ["query"]


async def test_search_shapes_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _mock_session(monkeypatch, payload=_sample_payload())
    result = json.loads(await x_search_tool.x_search("space"))
    assert result["ok"] is True
    assert result["result_count"] == 1
    tweet = result["tweets"][0]
    assert tweet["text"] == "hello from space"
    assert tweet["author"]["username"] == "nasa"
    assert tweet["author"]["verified"] is True
    assert tweet["metrics"]["like_count"] == 20
    assert tweet["url"] == "https://x.com/nasa/status/1001"


async def test_query_and_params_are_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _mock_session(monkeypatch, payload=_sample_payload())
    await x_search_tool.x_search("#python", max_results=25, sort_order="relevancy")
    assert _FakeSession.seen_url == impl._SEARCH_URL
    assert _FakeSession.seen_params["query"] == "#python"
    assert _FakeSession.seen_params["max_results"] == "25"
    assert _FakeSession.seen_params["sort_order"] == "relevancy"
    assert _FakeSession.seen_params["expansions"] == "author_id"


async def test_max_results_clamped_to_api_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _mock_session(monkeypatch, payload=_sample_payload())
    await x_search_tool.x_search("space", max_results=5)
    assert _FakeSession.seen_params["max_results"] == "10"  # clamped up to the API minimum
    await x_search_tool.x_search("space", max_results=500)
    assert _FakeSession.seen_params["max_results"] == "100"  # clamped down to the API maximum


async def test_zero_hits_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    # A valid response with no matches has no ``data`` key.
    _mock_session(monkeypatch, payload={"meta": {"result_count": 0}})
    result = json.loads(await x_search_tool.x_search("asdkjhaksjdh"))
    assert result["ok"] is True
    assert result["tweets"] == []
    assert result["result_count"] == 0


async def test_empty_query_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    result = json.loads(await x_search_tool.x_search("   "))
    assert result["ok"] is False
    assert "query" in result["message"]


async def test_bad_sort_order_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    result = json.loads(await x_search_tool.x_search("space", sort_order="popular"))
    assert result["ok"] is False
    assert "sort_order" in result["message"]


async def test_missing_token_reports_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    result = json.loads(await x_search_tool.x_search("space"))
    assert result["ok"] is False
    assert "X_BEARER_TOKEN" in result["message"]


async def test_auth_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "bad-token")
    _mock_session(monkeypatch, status=401, payload={"title": "Unauthorized"})
    result = json.loads(await x_search_tool.x_search("space"))
    assert result["ok"] is False
    assert "401" in result["message"]


async def test_rate_limit_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _mock_session(monkeypatch, status=429, payload={"title": "Too Many Requests"})
    result = json.loads(await x_search_tool.x_search("space"))
    assert result["ok"] is False
    assert "429" in result["message"]


async def test_timeout_reports_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _mock_session(monkeypatch, error=TimeoutError("slow"))
    result = json.loads(await x_search_tool.x_search("space"))
    assert result["ok"] is False
    assert "timed out" in result["message"]


async def test_client_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _mock_session(monkeypatch, error=aiohttp.ClientConnectionError("boom"))
    result = json.loads(await x_search_tool.x_search("space"))
    assert result["ok"] is False
    assert "Request failed" in result["message"]
