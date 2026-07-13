"""Private helpers for the ``llm_wiki`` toolset.

Implements Karpathy's "LLM wiki" pattern: instead of re-searching raw documents
on every question, the agent incrementally compiles knowledge into a persistent,
interlinked collection of Markdown pages that live under ``<workspace>/wiki/``.
Each page is a Markdown file with a small YAML frontmatter block (title, tags,
timestamps, aliases) and a body that cross-references other pages with
``[[wikilink]]`` syntax. Over time the wiki compounds into a browsable knowledge
base the agent can read, extend, and traverse by its links.

The heavy logic lives here so the tool-discovery import of ``llm_wiki`` stays
light. File IO is async via ``anyio.Path``; frontmatter is parsed/emitted with
``pyyaml`` (both already core dependencies).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import _background_process_registry as _bg
import anyio
import yaml

WIKI_DIRNAME = "wiki"
MAX_CONTENT_BYTES = 512 * 1024  # 512 KiB cap per page body
DEFAULT_SEARCH_LIMIT = 20
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]*)?\]\]")


def dumps_result(result: dict[str, Any]) -> str:
    """Serialize a result dict to compact JSON for the tool return value."""
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def slugify(title: str) -> str:
    """Turn a page title into a stable, filesystem-safe slug (the filename stem)."""
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


def wiki_dir(workspace: anyio.Path) -> anyio.Path:
    return workspace / WIKI_DIRNAME


def _page_path(workspace: anyio.Path, slug: str) -> anyio.Path:
    return wiki_dir(workspace) / f"{slug}.md"


def extract_links(body: str) -> list[str]:
    """Return the slugs a body links to via ``[[Target]]`` / ``[[Target|label]]``."""
    seen: dict[str, None] = {}
    for match in _WIKILINK_RE.finditer(body):
        seen.setdefault(slugify(match.group(1)), None)
    return list(seen)


def _serialize_page(meta: dict[str, Any], body: str) -> str:
    """Emit a page as YAML frontmatter + Markdown body."""
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{front}---\n\n{body.strip()}\n"


def _parse_page(text: str) -> tuple[dict[str, Any], str]:
    """Split stored text into (frontmatter dict, body). Tolerant of a missing block."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            raw_front = text[4:end]
            body = text[end + 4 :].strip("\n")
            try:
                meta = yaml.safe_load(raw_front) or {}
            except yaml.YAMLError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, body
    return {}, text.strip("\n")


async def _atomic_write(path: anyio.Path, text: str) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(text, encoding="utf-8")
    if await path.exists():
        await path.unlink()
    await tmp.rename(path)


def _normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        parts = re.split(r"[,\s]+", tags.strip())
        return [p for p in parts if p]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


async def _read_page(path: anyio.Path) -> tuple[dict[str, Any], str] | None:
    if not await path.exists():
        return None
    try:
        text = await path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_page(text)


async def wiki_write_impl(
    title: str,
    content: str,
    *,
    tags: Any = None,
    aliases: Any = None,
    overwrite: bool = True,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """Create or update a wiki page. Returns the saved page's metadata + links."""
    if not title or not isinstance(title, str) or not title.strip():
        return _error("A non-empty page title is required.")
    if not isinstance(content, str):
        return _error("content must be a string.")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        return _error(f"content exceeds the {MAX_CONTENT_BYTES // 1024} KiB per-page limit.")

    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title)
    path = _page_path(workspace, slug)

    existing = await _read_page(path)
    if existing is not None and not overwrite:
        return _error(
            f"Page {slug!r} already exists; pass overwrite=true to replace it.",
            slug=slug,
        )

    now = _iso_now()
    created = now
    if existing is not None:
        prev_meta, _ = existing
        created = str(prev_meta.get("created", now)) or now

    meta: dict[str, Any] = {
        "title": title.strip(),
        "slug": slug,
        "tags": _normalize_tags(tags),
        "aliases": _normalize_tags(aliases),
        "created": created,
        "updated": now,
    }
    links = extract_links(content)
    if links:
        meta["links"] = links

    try:
        await _atomic_write(path, _serialize_page(meta, content))
    except OSError as exc:
        return _error(f"Failed to write page: {exc}", slug=slug)

    return {
        "ok": True,
        "slug": slug,
        "path": str(path),
        "created": existing is None,
        "title": meta["title"],
        "tags": meta["tags"],
        "links": links,
        "workspace": str(workspace),
    }


async def wiki_read_impl(title_or_slug: str, *, workspace_raw: str = "") -> dict[str, Any]:
    """Read one page's full Markdown (frontmatter + body) plus its parsed metadata."""
    if not title_or_slug or not title_or_slug.strip():
        return _error("A page title or slug is required.")
    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title_or_slug)
    path = _page_path(workspace, slug)
    page = await _read_page(path)
    if page is None:
        return _error(f"No wiki page named {slug!r}.", slug=slug)
    meta, body = page
    return {
        "ok": True,
        "slug": slug,
        "path": str(path),
        "title": str(meta.get("title", slug)),
        "tags": _normalize_tags(meta.get("tags")),
        "aliases": _normalize_tags(meta.get("aliases")),
        "created": meta.get("created"),
        "updated": meta.get("updated"),
        "links": extract_links(body),
        "content": body,
    }


async def _iter_pages(workspace: anyio.Path) -> list[tuple[str, dict[str, Any], str]]:
    """Load every page as (slug, meta, body), sorted by slug. Empty if no wiki yet."""
    root = wiki_dir(workspace)
    if not await root.exists():
        return []
    pages: list[tuple[str, dict[str, Any], str]] = []
    async for entry in root.glob("*.md"):
        page = await _read_page(entry)
        if page is None:
            continue
        meta, body = page
        slug = str(meta.get("slug") or entry.stem)
        pages.append((slug, meta, body))
    pages.sort(key=lambda p: p[0])
    return pages


def _page_summary(slug: str, meta: dict[str, Any], body: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "title": str(meta.get("title", slug)),
        "tags": _normalize_tags(meta.get("tags")),
        "updated": meta.get("updated"),
        "links": extract_links(body),
    }


async def wiki_list_impl(*, tag: str = "", workspace_raw: str = "") -> dict[str, Any]:
    """List every page (slug/title/tags/updated/links), optionally filtered by tag."""
    workspace = _bg.resolve_workspace(workspace_raw)
    tag_filter = tag.strip().lower()
    pages = await _iter_pages(workspace)
    out: list[dict[str, Any]] = []
    for slug, meta, body in pages:
        summary = _page_summary(slug, meta, body)
        if tag_filter and tag_filter not in [t.lower() for t in summary["tags"]]:
            continue
        out.append(summary)
    return {
        "ok": True,
        "workspace": str(workspace),
        "count": len(out),
        "pages": out,
    }


def _snippet(body: str, needle: str, width: int = 160) -> str:
    idx = body.lower().find(needle.lower())
    if idx < 0:
        return body[:width].strip()
    start = max(0, idx - width // 2)
    end = min(len(body), idx + len(needle) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


async def wiki_search_impl(
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """Full-text search across page titles, tags, aliases, and bodies."""
    if not query or not query.strip():
        return _error("A non-empty search query is required.")
    if limit <= 0:
        limit = DEFAULT_SEARCH_LIMIT
    workspace = _bg.resolve_workspace(workspace_raw)
    needle = query.strip().lower()
    matches: list[dict[str, Any]] = []
    for slug, meta, body in await _iter_pages(workspace):
        title = str(meta.get("title", slug))
        tags = _normalize_tags(meta.get("tags"))
        aliases = _normalize_tags(meta.get("aliases"))
        # Weight title/tag hits above body hits so the best pages sort first.
        score = 0
        if needle in title.lower():
            score += 10
        if any(needle in t.lower() for t in tags + aliases):
            score += 5
        body_hits = body.lower().count(needle)
        score += body_hits
        if score == 0:
            continue
        matches.append(
            {
                "slug": slug,
                "title": title,
                "tags": tags,
                "score": score,
                "snippet": _snippet(body, query),
            }
        )
    matches.sort(key=lambda m: (-m["score"], m["slug"]))
    return {
        "ok": True,
        "workspace": str(workspace),
        "query": query,
        "count": len(matches),
        "results": matches[:limit],
    }


async def wiki_links_impl(title_or_slug: str, *, workspace_raw: str = "") -> dict[str, Any]:
    """Report a page's outgoing links, back-links, and broken (missing-target) links."""
    if not title_or_slug or not title_or_slug.strip():
        return _error("A page title or slug is required.")
    workspace = _bg.resolve_workspace(workspace_raw)
    target = slugify(title_or_slug)
    pages = await _iter_pages(workspace)
    known = {slug for slug, _, _ in pages}
    if target not in known:
        return _error(f"No wiki page named {target!r}.", slug=target)

    outgoing: list[str] = []
    backlinks: list[str] = []
    for slug, _, body in pages:
        links = extract_links(body)
        if slug == target:
            outgoing = links
        elif target in links:
            backlinks.append(slug)
    broken = [link for link in outgoing if link not in known]
    return {
        "ok": True,
        "workspace": str(workspace),
        "slug": target,
        "outgoing": outgoing,
        "backlinks": sorted(backlinks),
        "broken": broken,
    }


async def wiki_delete_impl(title_or_slug: str, *, workspace_raw: str = "") -> dict[str, Any]:
    """Delete a page. Reports which other pages had links pointing at it (now broken)."""
    if not title_or_slug or not title_or_slug.strip():
        return _error("A page title or slug is required.")
    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title_or_slug)
    path = _page_path(workspace, slug)
    if not await path.exists():
        return _error(f"No wiki page named {slug!r}.", slug=slug)

    orphaned: list[str] = []
    for other_slug, _, body in await _iter_pages(workspace):
        if other_slug != slug and slug in extract_links(body):
            orphaned.append(other_slug)
    try:
        await path.unlink()
    except OSError as exc:
        return _error(f"Failed to delete page: {exc}", slug=slug)
    return {
        "ok": True,
        "workspace": str(workspace),
        "slug": slug,
        "deleted": True,
        "orphaned_backlinks": sorted(orphaned),
    }
