"""Tests for the Haitun workspace ``fetch`` tool."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from yarl import URL

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_fetch_impl")
fetch_tool: Any = importlib.import_module("fetch")


class _FakeContent:
    """Mimics ``aiohttp`` response ``.content`` streaming interface."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._body), size):
            yield self._body[i : i + size]


class _FakeResponse:
    def __init__(self, status: int, url: str, content_type: str, body: bytes, charset: str) -> None:
        self.status = status
        self.url = URL(url)
        self.headers = {"content-type": content_type} if content_type else {}
        self.charset = charset
        self.content = _FakeContent(body)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    """Stands in for ``aiohttp.ClientSession``; records the requested URL."""

    seen_url: str = ""

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, allow_redirects: bool = True) -> _FakeResponse:
        type(self).seen_url = url
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _mock_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 200,
    url: str = "https://example.com/doc",
    content_type: str = "text/html; charset=utf-8",
    text: str = "",
    body: bytes | None = None,
    charset: str = "utf-8",
    error: Exception | None = None,
) -> None:
    """Patch ``aiohttp.ClientSession`` in the impl to return a canned response."""
    payload = body if body is not None else text.encode(charset or "utf-8")
    response = _FakeResponse(status, url, content_type, payload, charset)
    _FakeSession.seen_url = ""

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeSession:
        return _FakeSession(error if error is not None else response)

    monkeypatch.setattr(impl.aiohttp, "ClientSession", _factory)


def test_tool_metadata_is_loadable() -> None:
    """The public tool must expose valid metadata for the ToolRegistry."""
    meta = ToolFunction.from_callable(fetch_tool.fetch)
    assert meta.name == "fetch"
    assert "Markdown" in meta.description
    props = meta.parameters["properties"]
    assert set(props) == {"url", "max_chars", "raw"}
    assert meta.parameters["required"] == ["url"]


async def test_html_is_converted_to_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (
        "<html><head><title>Hello Doc</title></head><body>"
        "<nav>menu junk</nav>"
        "<article><h1>Main Heading</h1><p>First paragraph of the body.</p>"
        "<p>Second paragraph with a <a href='https://x'>link</a>.</p></article>"
        "</body></html>"
    )
    _mock_session(monkeypatch, text=html)
    result = json.loads(await fetch_tool.fetch("https://example.com/doc"))

    assert result["ok"] is True
    assert result["format"] == "markdown"
    assert result["title"] == "Hello Doc"
    # Headings become ATX (#) and links become Markdown syntax.
    assert "# Main Heading" in result["content"]
    assert "First paragraph of the body." in result["content"]
    assert "[link](https://x)" in result["content"]


async def test_readability_drops_boilerplate(monkeypatch: pytest.MonkeyPatch) -> None:
    # On a realistically sized document readability isolates the article and
    # drops navigation/footer chrome.
    body_paras = "".join(f"<p>Body sentence number {i} carrying the real article content here.</p>" for i in range(30))
    html = (
        "<html><head><title>Real Article</title></head><body>"
        "<nav>HOME ABOUT CONTACT navigation boilerplate</nav>"
        f"<article><h1>Article Title</h1>{body_paras}</article>"
        "<footer>copyright footer boilerplate junk</footer>"
        "</body></html>"
    )
    _mock_session(monkeypatch, content_type="text/html", text=html)
    result = json.loads(await fetch_tool.fetch("https://example.com/article"))
    assert result["ok"] is True
    assert "Body sentence number 15" in result["content"]
    assert "navigation boilerplate" not in result["content"]
    assert "footer boilerplate junk" not in result["content"]


async def test_plain_text_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, content_type="text/plain", text="just text")
    result = json.loads(await fetch_tool.fetch("https://example.com/robots.txt"))
    assert result["ok"] is True
    assert result["format"] == "text"
    assert result["content"] == "just text"


async def test_binary_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, content_type="image/png", body=b"\x89PNG\r\n")
    result = json.loads(await fetch_tool.fetch("https://example.com/logo.png"))
    assert result["ok"] is False
    assert "not textual" in result["message"]


async def test_raw_returns_html_unconverted(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, content_type="image/svg+xml", text="<svg></svg>")
    # raw=True bypasses both the textual guard and Markdown conversion.
    result = json.loads(await fetch_tool.fetch("https://example.com/pic.svg", raw=True))
    assert result["ok"] is True
    assert result["format"] == "raw"
    assert result["content"] == "<svg></svg>"


async def test_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, status=404, content_type="text/html", text="nope")
    result = json.loads(await fetch_tool.fetch("https://example.com/missing"))
    assert result["ok"] is False
    assert result["status"] == 404


async def test_bare_host_gets_https(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, content_type="text/plain", text="ok")
    await fetch_tool.fetch("example.com/page")
    assert _FakeSession.seen_url.startswith("https://example.com/page")


async def test_content_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, content_type="text/plain", text="x" * 500)
    result = json.loads(await fetch_tool.fetch("https://example.com/big", max_chars=100))
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["content"]) == 100


async def test_timeout_reports_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, error=TimeoutError("slow"))
    result = json.loads(await fetch_tool.fetch("https://example.com/slow"))
    assert result["ok"] is False
    assert "timed out" in result["message"]


async def test_client_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, error=aiohttp.ClientConnectionError("boom"))
    result = json.loads(await fetch_tool.fetch("https://example.com/broken"))
    assert result["ok"] is False
    assert "Request failed" in result["message"]
