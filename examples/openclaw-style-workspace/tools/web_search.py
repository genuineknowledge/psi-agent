"""Web search tool — search the web (mock implementation).

NOTE: This is a mock tool. To enable real search, replace the _mock_search()
call with an actual search provider (e.g. SerpAPI, Tavily, Brave Search API).
Set the appropriate API key in the environment variable documented below.
"""

from __future__ import annotations


async def tool(query: str, num_results: int = 5) -> str:
    """Search the web for information.

    Args:
        query: The search query.
        num_results: Number of results to return (max 10).

    Returns:
        Formatted search results, or an error message.

    Environment:
        WEB_SEARCH_API_KEY: API key for the search provider (not set = mock mode).
    """
    import os

    api_key = os.environ.get("WEB_SEARCH_API_KEY", "")
    if not api_key:
        return _mock_search(query, num_results)

    # Real implementation stub — wire up your preferred provider here.
    # Example: Tavily
    # import aiohttp
    # async with aiohttp.ClientSession() as session:
    #     async with session.post(
    #         "https://api.tavily.com/search",
    #         json={"api_key": api_key, "query": query, "max_results": num_results},
    #     ) as resp:
    #         data = await resp.json()
    #         results = data.get("results", [])
    #         ...
    return _mock_search(query, num_results)


def _mock_search(query: str, num_results: int) -> str:
    """Return mock search results for demonstration."""
    capped = min(num_results, 5)
    lines = [f"[Mock] Search results for: {query}\n"]
    for i in range(1, capped + 1):
        lines.append(
            f"{i}. Example Result {i}\n"
            f"   URL: https://example.com/result-{i}\n"
            f"   Snippet: This is a mock result for '{query}'. "
            f"Set WEB_SEARCH_API_KEY to enable real search.\n"
        )
    lines.append("\n[Note] This is a mock. Set WEB_SEARCH_API_KEY to enable real web search.")
    return "\n".join(lines)
