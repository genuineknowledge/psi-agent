"""Private helper for the ``fetch`` tool.

Fetches a single URL over HTTP(S) and converts the response body into clean,
readable text. HTML goes through readability (main-article extraction) and then
markdownify (HTML -> Markdown) so the agent gets the article body as Markdown
instead of a wall of navigation/boilerplate. Plain-text and other textual
responses are passed through as-is; binary responses are refused with a clear
message rather than returning garbage.

This complements ``search``: ``search`` returns result snippets, while ``fetch``
retrieves the full content of a specific URL the agent already knows about.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from markdownify import markdownify as _md  # type: ignore[import-untyped]
from readability import Document  # type: ignore[import-untyped]

# Guardrails. Kept conservative so a single fetch can't hang a turn or blow up
# the context window with a huge page.
DEFAULT_TIMEOUT = 20.0  # seconds for connect+read
MAX_BYTES = 5 * 1024 * 1024  # 5 MiB cap on the downloaded body
DEFAULT_MAX_CHARS = 20000  # cap on returned text to protect the context window
_USER_AGENT = "Mozilla/5.0 (compatible; psi-agent-fetch/1.0; +https://github.com/genuineknowledge/psi-agent)"


def dumps_result(result: dict[str, Any]) -> str:
    """Serialize a result dict to compact JSON for the tool return value."""
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, url: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "url": url, "message": message, **extra}


def _looks_textual(content_type: str) -> bool:
    """True when a Content-Type header denotes something we can render as text."""
    ct = content_type.split(";", 1)[0].strip().lower()
    if ct.startswith("text/"):
        return True
    return ct in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/rss+xml",
        "application/atom+xml",
        "application/ld+json",
        "application/javascript",
    }


def _is_html(content_type: str, body: str) -> bool:
    ct = content_type.split(";", 1)[0].strip().lower()
    if ct in {"text/html", "application/xhtml+xml"}:
        return True
    if ct.startswith("text/") or ct == "":
        # Some servers mislabel or omit the type; sniff the first chunk.
        head = body[:512].lstrip().lower()
        return head.startswith("<!doctype html") or "<html" in head
    return False


def _html_to_markdown(html: str, url: str) -> tuple[str, str]:
    """Extract the main article from HTML and convert it to Markdown.

    Returns ``(title, markdown)``. Falls back to a plain markdownify of the full
    document when readability can't isolate an article.
    """
    title = ""
    article_html = html
    try:
        doc = Document(html, url=url)
        title = (doc.short_title() or "").strip()
        summary = doc.summary(html_partial=True)
        if summary and summary.strip():
            article_html = summary
    except Exception:
        # readability failed (malformed markup, etc.) -> convert the whole doc.
        article_html = html

    markdown = _md(article_html, heading_style="ATX", strip=["script", "style"])
    # Collapse the runs of blank lines markdownify tends to emit.
    lines = [ln.rstrip() for ln in markdown.splitlines()]
    cleaned: list[str] = []
    blank = 0
    for ln in lines:
        if ln:
            blank = 0
            cleaned.append(ln)
        else:
            blank += 1
            if blank <= 1:
                cleaned.append(ln)
    return title, "\n".join(cleaned).strip()


async def fetch_impl(
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    raw: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch ``url`` and return a result dict (see ``fetch`` tool docstring)."""
    if not url or not isinstance(url, str):
        return _error("A non-empty URL string is required.", str(url))
    normalized = url.strip()
    if normalized.startswith("//"):
        normalized = "https:" + normalized
    if not normalized.startswith(("http://", "https://")):
        # Be forgiving: assume https for bare hosts like "example.com/page".
        normalized = "https://" + normalized

    if max_chars <= 0:
        max_chars = DEFAULT_MAX_CHARS

    client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout_s,
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
    )
    try:
        async with client, client.stream("GET", normalized) as response:
            status = response.status_code
            final_url = str(response.url)
            content_type = response.headers.get("content-type", "")

            if status >= 400:
                return _error(
                    f"HTTP {status} when fetching the URL.",
                    normalized,
                    final_url=final_url,
                    status=status,
                    content_type=content_type,
                )

            if not raw and not _looks_textual(content_type) and content_type:
                return _error(
                    f"Content-Type {content_type!r} is not textual; refusing to "
                    "return binary data. Use a dedicated tool for this file type.",
                    normalized,
                    final_url=final_url,
                    status=status,
                    content_type=content_type,
                )

            chunks: list[bytes] = []
            total = 0
            truncated_bytes = False
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_BYTES:
                    truncated_bytes = True
                    break
            encoding = response.encoding or "utf-8"
    except httpx.TimeoutException:
        return _error(f"Request timed out after {timeout_s:.0f}s.", normalized)
    except httpx.HTTPError as exc:
        return _error(f"Request failed: {type(exc).__name__}: {exc}", normalized)

    raw_body = b"".join(chunks)
    try:
        body = raw_body.decode(encoding, errors="replace")
    except LookupError, UnicodeDecodeError:
        body = raw_body.decode("utf-8", errors="replace")

    title = ""
    if raw:
        content = body
        content_format = "raw"
    elif _is_html(content_type, body):
        try:
            title, content = _html_to_markdown(body, final_url)
            content_format = "markdown"
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Failed to convert HTML to Markdown: {exc}", normalized, final_url=final_url)
    else:
        content = body
        content_format = "text"

    truncated_chars = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated_chars = True

    return {
        "ok": True,
        "url": normalized,
        "final_url": final_url,
        "status": status,
        "content_type": content_type,
        "title": title,
        "format": content_format,
        "truncated": truncated_bytes or truncated_chars,
        "content": content,
    }
