"""llm_wiki toolset — build and query an interlinked Markdown knowledge base.

Implements Andrej Karpathy's "LLM wiki" pattern: rather than re-searching raw
documents on every question, the agent compiles knowledge into durable,
cross-referenced Markdown pages that live under ``<workspace>/wiki/`` and
compound over time. Each page has YAML frontmatter (title, tags, timestamps,
aliases) and a body that links to other pages with ``[[wikilink]]`` syntax.

Use this to sink LLM-domain knowledge (architectures, papers, techniques,
glossary terms) into a browsable second brain you can extend and traverse:

- ``wiki_write`` — create/update a page.
- ``wiki_read`` — read one page's full Markdown.
- ``wiki_search`` — full-text search across titles, tags, and bodies.
- ``wiki_list`` — enumerate pages (optionally by tag).
- ``wiki_links`` — a page's outgoing links, back-links, and broken links.
- ``wiki_delete`` — remove a page.
- ``wiki_stats`` — overview of total pages, tags, and recent updates.
- ``wiki_related`` — find pages related by shared links (co-citation).

The heavy logic lives in ``_llm_wiki_impl`` so tool discovery stays light. No
extra dependencies: storage is async ``anyio`` file IO and ``pyyaml``
frontmatter, both already core dependencies.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _llm_wiki_impl as _w


async def wiki_write(
    title: str,
    content: str,
    tags: str = "",
    aliases: str = "",
    overwrite: bool = True,
) -> str:
    """Create or update a Markdown wiki page in the knowledge base.

    Compile what you've learned into a durable page instead of re-deriving it
    later. Cross-reference other pages inline with ``[[Other Page Title]]`` (or
    ``[[Other Page Title|display text]]``) — those links are indexed so you can
    later traverse the wiki with ``wiki_links``. The page filename (slug) is
    derived from ``title``; writing the same title again updates that page and
    preserves its original creation time.

    Args:
        title: The page title. Its slug (lowercased, dash-joined) is the filename.
        content: The page body in Markdown. Use ``[[Title]]`` to link other pages.
        tags: Comma- or space-separated tags for filtering/search (e.g. "transformers, attention").
        aliases: Comma- or space-separated alternate names that ``wiki_search`` also matches.
        overwrite: When False, refuse to replace an existing page (default True).

    Returns:
        JSON with ok, slug, path, created (bool), title, tags, links — or
        ok=false with a message on failure.
    """
    result = await _w.wiki_write_impl(
        title=title,
        content=content,
        tags=tags,
        aliases=aliases,
        overwrite=overwrite,
    )
    return _w.dumps_result(result)


async def wiki_read(title_or_slug: str) -> str:
    """Read one wiki page's full body and metadata.

    Accepts either the page title or its slug. Returns the Markdown body plus
    parsed frontmatter (tags, aliases, timestamps) and the list of pages this
    page links to.

    Args:
        title_or_slug: The page title (e.g. "Rotary Positional Embeddings") or slug ("rotary-positional-embeddings").

    Returns:
        JSON with ok, slug, title, tags, aliases, created, updated, links,
        content — or ok=false with a message if the page doesn't exist.
    """
    result = await _w.wiki_read_impl(title_or_slug)
    return _w.dumps_result(result)


async def wiki_search(query: str, limit: int = 20) -> str:
    """Full-text search the wiki across page titles, tags, aliases, and bodies.

    Use this before writing to check whether a topic already has a page, or to
    find the page that answers a question. Title and tag matches rank above body
    matches; each result includes a short snippet around the match. If no exact
    matches, returns fuzzy suggestions.

    Args:
        query: The text to search for (case-insensitive).
        limit: Maximum number of results to return (default 20).

    Returns:
        JSON with ok, query, count, and a ranked ``results`` list ({slug, title,
        tags, score, snippet}) — plus ``suggestions`` and ``message`` for empty
        results.
    """
    result = await _w.wiki_search_impl(query, limit=limit)
    return _w.dumps_result(result)


async def wiki_list(tag: str = "") -> str:
    """List every page in the wiki, optionally filtered to one tag.

    Gives you a table of contents: each page's slug, title, tags, last-updated
    time, outgoing links, broken_count, and broken_preview. Also returns
    total_broken and wanted_pages globally.

    Args:
        tag: Optional tag to filter by (case-insensitive). Empty lists all pages.

    Returns:
        JSON with ok, count, pages (with broken info), total_broken, wanted_pages, and summary.
    """
    result = await _w.wiki_list_impl(tag=tag)
    return _w.dumps_result(result)


async def wiki_links(title_or_slug: str) -> str:
    """Show a page's link graph: outgoing links, back-links, and broken links.

    Use this to navigate the wiki and to spot gaps: ``broken`` lists ``[[links]]``
    that point at pages which don't exist yet (good candidates to write next),
    and ``backlinks`` lists the pages that reference this one.

    Args:
        title_or_slug: The page title or slug to inspect.

    Returns:
        JSON with ok, slug, outgoing, backlinks, broken — or ok=false with a
        message if the page doesn't exist.
    """
    result = await _w.wiki_links_impl(title_or_slug)
    return _w.dumps_result(result)


async def wiki_delete(title_or_slug: str) -> str:
    """Delete a wiki page.

    Reports which other pages linked to the deleted page (their ``[[links]]`` are
    now broken) so you can fix or repoint them.

    Args:
        title_or_slug: The page title or slug to delete.

    Returns:
        JSON with ok, slug, deleted, orphaned_backlinks — or ok=false with a
        message if the page doesn't exist.
    """
    result = await _w.wiki_delete_impl(title_or_slug)
    return _w.dumps_result(result)


async def wiki_stats() -> str:
    """Get an overview of the wiki: total pages, tags, and recent updates.

    Use this to quickly understand what's in the knowledge base, what's new,
    and what topics are covered.

    Returns:
        JSON with ok, total_pages, unique_tags, recent_pages (list), popular_tags
        (list), and a human-readable message.
    """
    result = await _w.wiki_stats_impl()
    return _w.dumps_result(result)


async def wiki_related(title_or_slug: str, limit: int = 5) -> str:
    """Find pages related to a given page by shared links (co-citation).

    Use this for exploratory browsing: given a page, find other pages that
    share at least one outgoing link with it. The more shared links, the more
    related.

    Args:
        title_or_slug: The page title or slug to find related pages for.
        limit: Maximum number of related pages to return (default 5).

    Returns:
        JSON with ok, slug, related (list of {slug, title, shared_links}),
        and a human-readable message.
    """
    result = await _w.wiki_related_impl(title_or_slug, limit=limit)
    return _w.dumps_result(result)
