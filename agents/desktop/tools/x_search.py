"""x_search tool — search recent public posts on X (formerly Twitter).

Complements ``search`` (general web search) and ``fetch`` (read one URL): use
``x_search`` when you specifically want what people are posting on X right now —
reactions to news, product mentions, a hashtag, or an account's recent activity.
It queries the official X API v2 recent-search endpoint (Posts from the last 7
days); the heavy logic lives in ``_x_search_impl`` so the import stays light on
the tool-discovery path.

Auth: set ``X_BEARER_TOKEN`` in the environment to an X API v2 bearer token
(App-only OAuth 2.0).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _x_search_impl as _x


async def x_search(query: str, max_results: int = 10, sort_order: str = "recency") -> str:
    """Search recent public posts on X (Twitter) and return the matches.

    Use this to find what people are posting on X about a topic in the last 7
    days — breaking news reactions, hashtag activity, product buzz, or a
    specific account's posts. It runs the query against the X API v2
    recent-search endpoint and returns each Post with its author and engagement
    metrics.

    The ``query`` accepts X's search operators, e.g. ``from:nasa``,
    ``#python``, ``"exact phrase"``, ``lang:en``, ``-is:retweet``,
    ``url:github.com``. Combine them: ``claude lang:en -is:retweet`` finds
    original English posts mentioning "claude". Only public Posts from roughly
    the last 7 days are searchable.

    Args:
        query: The X search query. Supports X search operators (from:, #tag,
            "exact phrase", lang:, -is:retweet, url:, and boolean combinations).
        max_results: How many Posts to return (default 10). The API accepts
            10-100; values outside that range are clamped.
        sort_order: Result ordering — "recency" (newest first, default) or
            "relevancy" (best match first).

    Returns:
        JSON with ok=true, the echoed query, result_count, and a ``tweets``
        list ({id, text, created_at, lang, author {id, name, username,
        verified}, metrics {retweet/reply/like/quote counts}, url}); or
        ok=false with a ``message`` on failure (missing token, rate limit,
        bad query, network error).
    """
    result = await _x.x_search_impl(query=query, max_results=max_results, sort_order=sort_order)
    return _x.dumps_result(result)
