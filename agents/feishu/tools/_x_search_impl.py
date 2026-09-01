"""Private helper for the ``x_search`` tool.

Searches recent public posts on X (formerly Twitter) through the official
`X API v2 recent-search endpoint
<https://docs.x.com/x-api/posts/recent-search>`_
(``GET /2/tweets/search/recent``), which returns Posts from the last 7 days
matching a query. It talks to the REST API over ``aiohttp`` (already a core
dependency, so no new packages) and shapes the response into a compact result
the agent can read, joining each Post with its author from the ``includes``
block.

Auth: a bearer token read from the ``X_BEARER_TOKEN`` environment variable
(load it into the workspace ``.env`` / process env before starting the agent).
Every helper returns a plain ``dict`` — ``ok=True`` with data, or ``ok=False``
with a ``message`` — so the thin tool layer never has to handle exceptions.
"""

from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

# X API v2 recent-search: returns matching Posts from the last 7 days.
_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
_TOKEN_ENV = "X_BEARER_TOKEN"

# Guardrails so a single search can't hang a turn or blow up the context window.
DEFAULT_TIMEOUT = 20.0  # seconds for connect+read
DEFAULT_MAX_RESULTS = 10
# The endpoint itself only accepts max_results in [10, 100].
_API_MIN_RESULTS = 10
_API_MAX_RESULTS = 100

# Ask the API for the fields we actually surface, plus the author expansion so
# each Post can be joined to a username rather than a bare author_id.
_TWEET_FIELDS = "created_at,author_id,public_metrics,lang,entities"
_USER_FIELDS = "name,username,verified"
_EXPANSIONS = "author_id"


def dumps_result(result: dict[str, Any]) -> str:
    """Serialize a result dict to compact JSON for the tool return value."""
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _clamp_results(max_results: int) -> int:
    """Coerce a requested count into the API's accepted [10, 100] range."""
    if max_results <= 0:
        return DEFAULT_MAX_RESULTS
    return max(_API_MIN_RESULTS, min(max_results, _API_MAX_RESULTS))


def _tweet_dict(tweet: dict[str, Any], users_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Shape one raw API tweet object into the compact form we return."""
    author_id = tweet.get("author_id", "")
    author = users_by_id.get(author_id, {})
    metrics = tweet.get("public_metrics") or {}
    tweet_id = tweet.get("id", "")
    username = author.get("username", "")
    return {
        "id": tweet_id,
        "text": tweet.get("text", ""),
        "created_at": tweet.get("created_at"),
        "lang": tweet.get("lang"),
        "author": {
            "id": author_id,
            "name": author.get("name"),
            "username": username,
            "verified": author.get("verified"),
        },
        "metrics": {
            "retweet_count": metrics.get("retweet_count"),
            "reply_count": metrics.get("reply_count"),
            "like_count": metrics.get("like_count"),
            "quote_count": metrics.get("quote_count"),
        },
        # A best-effort permalink; needs the username, which the author
        # expansion provides.
        "url": f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else None,
    }


def _describe_api_error(status: int, payload: Any) -> str:
    """Turn a non-2xx X API response into a short, actionable message."""
    detail = ""
    if isinstance(payload, dict):
        # v2 errors come back under ``title``/``detail`` or an ``errors`` list.
        if payload.get("detail"):
            detail = str(payload["detail"])
        elif payload.get("title"):
            detail = str(payload["title"])
        elif isinstance(payload.get("errors"), list) and payload["errors"]:
            first = payload["errors"][0]
            detail = str(first.get("message") or first.get("detail") or first)
    if status == 401:
        return f"X API authentication failed (HTTP 401): check {_TOKEN_ENV}. {detail}".strip()
    if status == 429:
        return f"X API rate limit exceeded (HTTP 429): try again later. {detail}".strip()
    return f"X API error (HTTP {status}). {detail}".strip()


async def x_search_impl(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    sort_order: str = "recency",
    timeout_s: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run a recent-search against the X API v2 and return a result dict.

    See the ``x_search`` tool docstring for the parameter contract. Returns
    ``{"ok": True, "query", "tweets": [...], "result_count"}`` on success, or
    ``{"ok": False, "message"}`` on any failure (missing token, bad request,
    auth, rate limit, network error).
    """
    query = (query or "").strip()
    if not query:
        return _error("`query` must not be empty.")

    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        return _error(
            f"X search is not configured. Set the {_TOKEN_ENV} environment variable to an "
            "X API v2 bearer token (App-only OAuth 2.0)."
        )

    if sort_order not in ("recency", "relevancy"):
        return _error("`sort_order` must be 'recency' or 'relevancy'.")

    params = {
        "query": query,
        "max_results": str(_clamp_results(max_results)),
        "sort_order": sort_order,
        "tweet.fields": _TWEET_FIELDS,
        "user.fields": _USER_FIELDS,
        "expansions": _EXPANSIONS,
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    try:
        async with (
            aiohttp.ClientSession(timeout=timeout, headers=headers) as session,
            session.get(_SEARCH_URL, params=params) as response,
        ):
            status = response.status
            try:
                payload = await response.json()
            except aiohttp.ContentTypeError, json.JSONDecodeError, ValueError:
                payload = None
            if status != 200:
                return _error(_describe_api_error(status, payload), status=status)
    except TimeoutError:
        return _error(f"Request timed out after {timeout_s:.0f}s.")
    except aiohttp.ClientError as exc:
        return _error(f"Request failed: {type(exc).__name__}: {exc}")

    if not isinstance(payload, dict):
        return _error("X API returned an unexpected (non-JSON) response.")

    # A valid response with zero hits carries no ``data`` key, just meta.
    tweets_raw = payload.get("data") or []
    includes = payload.get("includes") or {}
    users_by_id = {u.get("id", ""): u for u in includes.get("users", [])}
    meta = payload.get("meta") or {}

    tweets = [_tweet_dict(t, users_by_id) for t in tweets_raw]
    return {
        "ok": True,
        "query": query,
        "result_count": meta.get("result_count", len(tweets)),
        "tweets": tweets,
    }
