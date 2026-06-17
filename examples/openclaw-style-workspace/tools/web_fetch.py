"""Web fetch tool — fetch a URL and return readable text content."""

from __future__ import annotations

import re

import aiohttp


async def web_fetch(url: str, timeout: int = 30) -> str:
    """Fetch a URL and return its readable text content.

    HTML tags are stripped; only text content is returned.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        Page text content with HTML stripped, or an error message.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; psi-agent/1.0)",
        "Accept": "text/html,application/xhtml+xml,text/plain",
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status != 200:
                    return f"[Error] HTTP {response.status} fetching {url}"
                content_type = response.headers.get("Content-Type", "")
                text = await response.text(errors="replace")

                if "text/html" in content_type or url.endswith(".html"):
                    text = _strip_html(text)

                return text.strip() or "(empty response)"
    except aiohttp.ClientConnectorError as e:
        return f"[Error] Connection failed: {e}"
    except TimeoutError:
        return f"[Error] Request timed out after {timeout}s"


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    # Remove script and style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    # Collapse whitespace
    html = re.sub(r"\s+", " ", html)
    return html.strip()
