"""Tests for the Haitun workspace ``fetch`` tool."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_fetch_impl")
fetch_tool: Any = importlib.import_module("fetch")


def _mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route all httpx requests in the impl through a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(impl.httpx, "AsyncClient", _factory)


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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    _mock_transport(monkeypatch, handler)
    raw = await fetch_tool.fetch("https://example.com/doc")
    result = json.loads(raw)

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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    _mock_transport(monkeypatch, handler)
    result = json.loads(await fetch_tool.fetch("https://example.com/article"))
    assert result["ok"] is True
    assert "Body sentence number 15" in result["content"]
    assert "navigation boilerplate" not in result["content"]
    assert "footer boilerplate junk" not in result["content"]


async def test_plain_text_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="just text")

    _mock_transport(monkeypatch, handler)
    result = json.loads(await fetch_tool.fetch("https://example.com/robots.txt"))
    assert result["ok"] is True
    assert result["format"] == "text"
    assert result["content"] == "just text"


async def test_binary_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG\r\n")

    _mock_transport(monkeypatch, handler)
    result = json.loads(await fetch_tool.fetch("https://example.com/logo.png"))
    assert result["ok"] is False
    assert "not textual" in result["message"]


async def test_raw_returns_html_unconverted(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/svg+xml"}, text="<svg></svg>")

    _mock_transport(monkeypatch, handler)
    # raw=True bypasses both the textual guard and Markdown conversion.
    result = json.loads(await fetch_tool.fetch("https://example.com/pic.svg", raw=True))
    assert result["ok"] is True
    assert result["format"] == "raw"
    assert result["content"] == "<svg></svg>"


async def test_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, text="nope")

    _mock_transport(monkeypatch, handler)
    result = json.loads(await fetch_tool.fetch("https://example.com/missing"))
    assert result["ok"] is False
    assert result["status"] == 404


async def test_bare_host_gets_https(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

    _mock_transport(monkeypatch, handler)
    await fetch_tool.fetch("example.com/page")
    assert seen["url"].startswith("https://example.com/page")


async def test_content_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="x" * 500)

    _mock_transport(monkeypatch, handler)
    result = json.loads(await fetch_tool.fetch("https://example.com/big", max_chars=100))
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["content"]) == 100


async def test_timeout_reports_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    _mock_transport(monkeypatch, handler)
    result = json.loads(await fetch_tool.fetch("https://example.com/slow"))
    assert result["ok"] is False
    assert "timed out" in result["message"]
