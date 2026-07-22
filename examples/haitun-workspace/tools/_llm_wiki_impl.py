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

import difflib
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
# Collapse runs of non-"word" characters into a single dash. Under Python's
# default Unicode matching, ``\w`` covers letters/digits of ANY script (CJK,
# Cyrillic, …) plus underscore — so non-Latin titles like Chinese "校训" get a
# real, distinct slug instead of all collapsing to "untitled". Underscore is
# kept so intentional names like "_schema" survive.
_SLUG_RE = re.compile(r"\W+")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]*)?\]\]")


def dumps_result(result: dict[str, Any]) -> str:
    """Serialize a result dict to compact JSON for the tool return value."""
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, hint: str = "", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, "hint": hint, **extra}


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


async def _iter_pages(workspace: anyio.Path) -> list[tuple[str, dict[str, Any], str]]:
    """Load every page as (slug, meta, body), sorted by slug. Empty if no wiki yet."""
    root = wiki_dir(workspace)
    if not await root.exists():
        return []
    pages: list[tuple[str, dict[str, Any], str]] = []
    async for entry in root.glob("*.md"):
        # 跳过 CHANGELOG.md（特殊页面，不作为知识内容）
        if entry.name == "CHANGELOG.md":
            continue
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


def _snippet_around_match(body: str, needle: str, width: int = 160) -> str:
    """Extract a snippet centered on the first occurrence of needle in body."""
    idx = body.lower().find(needle.lower())
    if idx < 0:
        return body[:width].strip()
    start = max(0, idx - width // 2)
    end = min(len(body), idx + len(needle) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


async def _append_changelog(workspace: anyio.Path, slug: str, title: str, created: bool, links: list[str]) -> None:
    """Append a change entry to CHANGELOG.md in the wiki root."""
    changelog_path = wiki_dir(workspace) / "CHANGELOG.md"
    timestamp = _iso_now()
    action = "Created" if created else "Updated"
    # 截断过长的链接列表用于日志
    links_str = ", ".join(links[:5])
    if len(links) > 5:
        links_str += f" 等{len(links)}个"
    entry = f"- {timestamp} | **{action}** [{title}]({slug}) | 链接: {links_str}\n"

    # 原子写入：如果已存在则追加，否则新建
    if await changelog_path.exists():
        existing = await changelog_path.read_text(encoding="utf-8")
        await _atomic_write(changelog_path, existing + entry)
    else:
        header = "# Wiki 变更日志\n\n自动记录每次页面创建/更新。\n\n"
        await _atomic_write(changelog_path, header + entry)


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

    # 变更日志（新增）
    is_created = existing is None
    await _append_changelog(workspace, slug, meta["title"], is_created, links)

    return {
        "ok": True,
        "slug": slug,
        "path": str(path),
        "created": is_created,
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
        return _error(
            f"No wiki page named {slug!r}.", slug=slug, hint="尝试 `wiki_search` 查找相似页面，或 `wiki_write` 创建它。"
        )
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


async def wiki_list_impl(*, tag: str = "", workspace_raw: str = "") -> dict[str, Any]:
    """List every page with broken link counts and global wanted pages."""
    workspace = _bg.resolve_workspace(workspace_raw)
    tag_filter = tag.strip().lower()
    pages = await _iter_pages(workspace)

    # 收集所有 slug 用于断链检测
    all_slugs = {slug for slug, _, _ in pages}
    all_broken: set[str] = set()
    out: list[dict[str, Any]] = []

    for slug, meta, body in pages:
        links = extract_links(body)
        broken = [l for l in links if l not in all_slugs]
        all_broken.update(broken)
        summary = _page_summary(slug, meta, body)
        if tag_filter and tag_filter not in [t.lower() for t in summary["tags"]]:
            continue
        summary["broken_count"] = len(broken)
        summary["broken_preview"] = broken[:3]
        out.append(summary)

    # 按断链数降序排列，方便用户优先处理断链多的页面
    out.sort(key=lambda p: (-p["broken_count"], p["slug"]))

    # 生成 wanted 列表（按被引用次数排序）
    wanted_list = sorted(all_broken)

    page_titles = [f"「{p['title']}」" for p in out]
    summary_msg = f"共 {len(out)} 个页面，{len(all_broken)} 个断链待补。"
    if out:
        summary_msg += f" 页面：{', '.join(page_titles[:3])}"
        if len(out) > 3:
            summary_msg += f" 等 {len(out)} 个。"
    if wanted_list:
        summary_msg += f" 建议优先创建：{', '.join(wanted_list[:5])}"
        if len(wanted_list) > 5:
            summary_msg += f" 等 {len(wanted_list)} 个。"

    return {
        "ok": True,
        "workspace": str(workspace),
        "count": len(out),
        "pages": out,
        "total_broken": len(all_broken),
        "wanted_pages": wanted_list,
        "summary": summary_msg,
    }


async def wiki_search_impl(
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """Full-text search with fuzzy fallback and centered snippets."""
    if not query or not query.strip():
        return _error("A non-empty search query is required.")
    if limit <= 0:
        limit = DEFAULT_SEARCH_LIMIT
    workspace = _bg.resolve_workspace(workspace_raw)
    needle = query.strip().lower()

    all_pages = await _iter_pages(workspace)
    all_titles = [str(meta.get("title", slug)) for slug, meta, _ in all_pages]
    matches: list[dict[str, Any]] = []

    for slug, meta, body in all_pages:
        title = str(meta.get("title", slug))
        tags = _normalize_tags(meta.get("tags"))
        aliases = _normalize_tags(meta.get("aliases"))
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
                "snippet": _snippet_around_match(body, query),
            }
        )

    matches.sort(key=lambda m: (-m["score"], m["slug"]))
    matched = matches[:limit]

    # 无精确匹配 -> 模糊建议
    if not matched:
        suggestions_raw = difflib.get_close_matches(query, all_titles, n=3, cutoff=0.6)
        suggestions = []
        for s in suggestions_raw:
            # 找到对应 slug
            for slug, meta, _ in all_pages:
                if str(meta.get("title", slug)) == s:
                    suggestions.append({"title": s, "slug": slug})
                    break
        if suggestions:
            msg = f"未找到与 '{query}' 相关的页面。您是否想找：{', '.join([s['title'] for s in suggestions])}？"
        else:
            msg = f"未找到与 '{query}' 相关的页面。您可以尝试其他关键词，或使用 `wiki_write` 创建新页面。"

        return {
            "ok": True,
            "workspace": str(workspace),
            "query": query,
            "count": 0,
            "results": [],
            "suggestions": suggestions if suggestions else None,
            "message": msg,
        }

    # 有结果 -> 生成友好摘要
    top_titles = [m["title"] for m in matched[:3]]
    msg = f"找到 {len(matched)} 个相关页面，最匹配的是：{', '.join(top_titles)}"
    if len(matched) > 3:
        msg += f" 等 {len(matched)} 个结果。"

    return {
        "ok": True,
        "workspace": str(workspace),
        "query": query,
        "count": len(matched),
        "results": matched,
        "message": msg,
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
        return _error(
            f"No wiki page named {target!r}.", slug=target, hint="检查拼写，或使用 `wiki_list` 查看所有页面。"
        )

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
        return _error(f"No wiki page named {slug!r}.", slug=slug, hint="检查拼写，或使用 `wiki_list` 查看所有页面。")

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


async def wiki_stats_impl(workspace_raw: str = "") -> dict[str, Any]:
    """Get an overview of the wiki: total pages, tags, and recent updates."""
    workspace = _bg.resolve_workspace(workspace_raw)
    pages = await _iter_pages(workspace)

    if not pages:
        return {
            "ok": True,
            "workspace": str(workspace),
            "total_pages": 0,
            "unique_tags": 0,
            "recent_pages": [],
            "message": "Wiki 是空的。使用 `wiki_write` 创建第一篇文章吧！",
        }

    sorted_by_updated = sorted(pages, key=lambda p: p[1].get("updated", ""), reverse=True)
    recent = [
        {
            "slug": slug,
            "title": str(meta.get("title", slug)),
            "updated": meta.get("updated"),
        }
        for slug, meta, _ in sorted_by_updated[:5]
    ]

    all_tags = set()
    for _, meta, _ in pages:
        all_tags.update(_normalize_tags(meta.get("tags")))

    tag_list = sorted(all_tags)[:10]
    recent_titles = [str(p["title"]) for p in recent]

    msg = f"知识库共 {len(pages)} 个页面，涵盖 {len(all_tags)} 个标签。"
    if recent_titles:
        msg += f" 最近更新：{', '.join(recent_titles)}。"
    if tag_list:
        msg += f" 热门标签：{', '.join(tag_list)}。"

    return {
        "ok": True,
        "workspace": str(workspace),
        "total_pages": len(pages),
        "unique_tags": len(all_tags),
        "recent_pages": recent,
        "popular_tags": tag_list,
        "message": msg,
    }


async def wiki_related_impl(
    title_or_slug: str,
    *,
    limit: int = 5,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """Find pages related to a given page by shared links (co-citation)."""
    if not title_or_slug or not title_or_slug.strip():
        return _error("A page title or slug is required.")
    workspace = _bg.resolve_workspace(workspace_raw)
    target = slugify(title_or_slug)
    pages = await _iter_pages(workspace)
    all_slugs = {slug for slug, _, _ in pages}
    if target not in all_slugs:
        return _error(f"No wiki page named {target!r}.", slug=target)

    # 获取目标页面的出链
    target_links = []
    for slug, meta, body in pages:
        if slug == target:
            target_links = extract_links(body)
            break

    if not target_links:
        return {
            "ok": True,
            "slug": target,
            "related": [],
            "message": f"页面「{target}」没有出链，无法推荐相关页面。",
        }

    # 统计每个页面的共同引用数量
    related_scores: list[tuple[str, int, str]] = []  # (slug, score, title)
    for slug, meta, body in pages:
        if slug == target:
            continue
        links = extract_links(body)
        common = set(target_links) & set(links)
        score = len(common)
        if score > 0:
            title = str(meta.get("title", slug))
            related_scores.append((slug, score, title))

    related_scores.sort(key=lambda x: (-x[1], x[0]))
    top = related_scores[:limit]

    if not top:
        return {
            "ok": True,
            "slug": target,
            "related": [],
            "message": f"没有页面与「{target}」共享相同的链接。",
        }

    related_list = [{"slug": s, "title": t, "shared_links": score} for s, score, t in top]
    titles = [str(r["title"]) for r in related_list]
    msg = f"与「{target}」相关的页面：{', '.join(titles)}。"

    return {
        "ok": True,
        "slug": target,
        "related": related_list,
        "message": msg,
    }
