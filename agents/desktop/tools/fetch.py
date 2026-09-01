"""Fetch tool - retrieve a URL's full content and convert it to Markdown.

Complements ``search`` (which only returns result snippets): use ``fetch`` when
you have a specific URL and need its full page content — to read documentation,
an article, a changelog, or an API reference the search snippet was too short to
answer from.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _fetch_impl as _f


async def fetch(url: str, max_chars: int = 20000, raw: bool = False) -> str:
    """Fetch a URL and return its main content as Markdown.

    Use this to read the **full text** of a web page when a ``search`` snippet
    isn't enough — documentation, articles, changelogs, API references. It
    downloads the page over HTTP(S), extracts the main article with readability,
    and converts it to Markdown so you get the body without navigation and ads.
    Pair it with ``search``: ``search`` finds candidate URLs, ``fetch`` reads
    the one you pick.

    HTML is returned as Markdown; plain-text/JSON/XML responses pass through
    unchanged. Binary responses (images, PDFs, archives) are refused with a
    message — use a dedicated tool for those. Redirects are followed, the body
    is capped at 5 MiB, and the returned text is truncated to ``max_chars``.

    Args:
        url: The URL to fetch. A bare host like ``example.com/page`` is treated as https.
        max_chars: Maximum characters of content to return (default 20000; guards the context window).
        raw: When True, skip article extraction and Markdown conversion and return the raw response body.

    Returns:
        JSON with ok, url, final_url, status, content_type, title, format,
        truncated, content — or ok=false with a message on failure.
    """
    result = await _f.fetch_impl(url=url, max_chars=max_chars, raw=raw)
    return _f.dumps_result(result)
