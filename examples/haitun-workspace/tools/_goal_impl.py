"""Private helpers for the ``goal`` toolset — define and track high-level goals.

Haitun has no goal-tracking system: the agent can plan a single session
(``todo``) or keep a task board (``taskflow`` skill), but nothing holds the
*high-level intent* that outlives any one task — "ship v2", "reach 90% test
coverage", "learn the payments codebase". This toolset fills that gap.

Each goal is a Markdown file under ``<workspace>/goals/`` with a small YAML
frontmatter block (title, slug, status, priority, progress 0-100, target date,
tags, timestamps) plus an append-only ``log`` of dated progress entries, and a
body holding the goal's description / definition of done. Goals cross-reference
each other with ``[[slug]]`` so a goal can name the sub-goals it depends on.

The heavy logic lives here so the tool-discovery import of ``goal`` stays light.
File IO is async via ``anyio.Path``; frontmatter is parsed/emitted with
``pyyaml`` (both already core dependencies) — no extra packages.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import _background_process_registry as _bg
import anyio
import yaml

GOALS_DIRNAME = "goals"
MAX_CONTENT_BYTES = 256 * 1024  # 256 KiB cap per goal description
DEFAULT_LIST_LIMIT = 100
MAX_LOG_ENTRIES = 500  # keep the progress log bounded

# Allowed lifecycle states and priorities. ``active`` goals are the ones the
# agent is currently pursuing; the terminal states are ``achieved``/``abandoned``.
STATUSES = ("active", "paused", "achieved", "abandoned")
PRIORITIES = ("low", "medium", "high")
DEFAULT_STATUS = "active"
DEFAULT_PRIORITY = "medium"

# Collapse runs of non-"word" characters into a single dash. Under Python's
# default Unicode matching, ``\w`` covers letters/digits of ANY script (CJK,
# Cyrillic, …) plus underscore — so non-Latin titles like Chinese "上线" get a
# real, distinct slug instead of all collapsing to "untitled".
_SLUG_RE = re.compile(r"\W+")
_LINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]*)?\]\]")


def dumps_result(result: dict[str, Any]) -> str:
    """Serialize a result dict to compact JSON for the tool return value."""
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def slugify(title: str) -> str:
    """Turn a goal title into a stable, filesystem-safe slug (the filename stem)."""
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


def goals_dir(workspace: anyio.Path) -> anyio.Path:
    return workspace / GOALS_DIRNAME


def _goal_path(workspace: anyio.Path, slug: str) -> anyio.Path:
    return goals_dir(workspace) / f"{slug}.md"


def extract_links(body: str) -> list[str]:
    """Return the slugs a body links to via ``[[Target]]`` / ``[[Target|label]]``."""
    seen: dict[str, None] = {}
    for match in _LINK_RE.finditer(body):
        seen.setdefault(slugify(match.group(1)), None)
    return list(seen)


def _normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        parts = re.split(r"[,\s]+", tags.strip())
        return [p for p in parts if p]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def _normalize_status(status: Any, *, default: str = DEFAULT_STATUS) -> str | None:
    """Coerce a status to one of ``STATUSES``. Returns None on an invalid value."""
    if status is None or (isinstance(status, str) and not status.strip()):
        return default
    s = str(status).strip().lower()
    return s if s in STATUSES else None


def _normalize_priority(priority: Any, *, default: str = DEFAULT_PRIORITY) -> str | None:
    if priority is None or (isinstance(priority, str) and not priority.strip()):
        return default
    p = str(priority).strip().lower()
    return p if p in PRIORITIES else None


def _clamp_progress(progress: Any, *, default: int = 0) -> int | None:
    """Coerce progress to an int in [0, 100]. Returns None if not a number."""
    if progress is None or (isinstance(progress, str) and not str(progress).strip()):
        return default
    try:
        value = round(float(progress))
    except TypeError, ValueError:
        return None
    return max(0, min(100, value))


def _serialize_goal(meta: dict[str, Any], body: str) -> str:
    """Emit a goal as YAML frontmatter + Markdown body."""
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{front}---\n\n{body.strip()}\n"


def _parse_goal(text: str) -> tuple[dict[str, Any], str]:
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


async def _read_goal(path: anyio.Path) -> tuple[dict[str, Any], str] | None:
    if not await path.exists():
        return None
    try:
        text = await path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_goal(text)


def _coerce_log(log: Any) -> list[dict[str, Any]]:
    """Return a clean list of progress-log entries ({at, progress, status, note})."""
    if not isinstance(log, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in log:
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _public_view(slug: str, meta: dict[str, Any], body: str) -> dict[str, Any]:
    """Full public representation of a goal (used by get + as write echo)."""
    return {
        "slug": slug,
        "title": str(meta.get("title", slug)),
        "status": str(meta.get("status", DEFAULT_STATUS)),
        "priority": str(meta.get("priority", DEFAULT_PRIORITY)),
        "progress": _clamp_progress(meta.get("progress"), default=0),
        "target_date": meta.get("target_date") or "",
        "tags": _normalize_tags(meta.get("tags")),
        "created": meta.get("created"),
        "updated": meta.get("updated"),
        "links": extract_links(body),
        "log": _coerce_log(meta.get("log")),
        "description": body,
    }


def _summary_view(slug: str, meta: dict[str, Any], body: str) -> dict[str, Any]:
    """Compact representation for listings (omits body + full log)."""
    log = _coerce_log(meta.get("log"))
    return {
        "slug": slug,
        "title": str(meta.get("title", slug)),
        "status": str(meta.get("status", DEFAULT_STATUS)),
        "priority": str(meta.get("priority", DEFAULT_PRIORITY)),
        "progress": _clamp_progress(meta.get("progress"), default=0),
        "target_date": meta.get("target_date") or "",
        "tags": _normalize_tags(meta.get("tags")),
        "updated": meta.get("updated"),
        "links": extract_links(body),
        "last_note": (log[-1].get("note") if log else "") or "",
    }


async def goal_set_impl(
    title: str,
    description: str = "",
    *,
    status: Any = None,
    priority: Any = None,
    progress: Any = None,
    target_date: str = "",
    tags: Any = None,
    overwrite: bool = True,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """Create or update a high-level goal. Returns the saved goal's full view.

    Updating an existing goal preserves its ``created`` time and progress ``log``;
    fields left as None/empty keep their prior value rather than being wiped.
    """
    if not title or not isinstance(title, str) or not title.strip():
        return _error("A non-empty goal title is required.")
    if not isinstance(description, str):
        return _error("description must be a string.")
    if len(description.encode("utf-8")) > MAX_CONTENT_BYTES:
        return _error(f"description exceeds the {MAX_CONTENT_BYTES // 1024} KiB limit.")

    norm_status = _normalize_status(status, default="")
    if norm_status is None:
        return _error(f"status must be one of {', '.join(STATUSES)}.")
    norm_priority = _normalize_priority(priority, default="")
    if norm_priority is None:
        return _error(f"priority must be one of {', '.join(PRIORITIES)}.")
    norm_progress = _clamp_progress(progress, default=-1)
    if norm_progress is None:
        return _error("progress must be a number between 0 and 100.")

    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title)
    path = _goal_path(workspace, slug)

    existing = await _read_goal(path)
    if existing is not None and not overwrite:
        return _error(
            f"Goal {slug!r} already exists; pass overwrite=true to update it.",
            slug=slug,
        )

    now = _iso_now()
    prev_meta: dict[str, Any] = existing[0] if existing else {}
    prev_body = existing[1] if existing else ""

    meta: dict[str, Any] = {
        "title": title.strip(),
        "slug": slug,
        "status": norm_status or str(prev_meta.get("status", DEFAULT_STATUS)),
        "priority": norm_priority or str(prev_meta.get("priority", DEFAULT_PRIORITY)),
        "progress": (norm_progress if norm_progress >= 0 else _clamp_progress(prev_meta.get("progress"), default=0)),
        "target_date": target_date.strip() or (prev_meta.get("target_date") or ""),
        "tags": (
            _normalize_tags(tags)
            if (tags is not None and str(tags).strip())
            else _normalize_tags(prev_meta.get("tags"))
        ),
        "created": str(prev_meta.get("created", now)) if existing else now,
        "updated": now,
        "log": _coerce_log(prev_meta.get("log")),
    }
    body = description if description.strip() else prev_body

    try:
        await _atomic_write(path, _serialize_goal(meta, body))
    except OSError as exc:
        return _error(f"Failed to write goal: {exc}", slug=slug)

    view = _public_view(slug, meta, body)
    view.update(ok=True, path=str(path), created=existing is None, workspace=str(workspace))
    return view


async def goal_progress_impl(
    title_or_slug: str,
    note: str = "",
    *,
    progress: Any = None,
    status: Any = None,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """Append a dated progress entry to a goal and optionally move its % / status.

    This is the tracking half of the toolset: every call records what happened
    (``note``) plus the new ``progress`` / ``status`` if given, into an
    append-only ``log`` so a goal carries its own history. Setting progress to
    100 or status to ``achieved`` marks the goal done.
    """
    if not title_or_slug or not title_or_slug.strip():
        return _error("A goal title or slug is required.")
    if not isinstance(note, str):
        return _error("note must be a string.")

    norm_progress = _clamp_progress(progress, default=-1)
    if norm_progress is None:
        return _error("progress must be a number between 0 and 100.")
    norm_status = _normalize_status(status, default="")
    if norm_status is None:
        return _error(f"status must be one of {', '.join(STATUSES)}.")
    if not note.strip() and norm_progress < 0 and not norm_status:
        return _error("Provide at least one of note, progress, or status to record.")

    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title_or_slug)
    path = _goal_path(workspace, slug)
    existing = await _read_goal(path)
    if existing is None:
        return _error(f"No goal named {slug!r}.", slug=slug)
    meta, body = existing

    now = _iso_now()
    new_progress = norm_progress if norm_progress >= 0 else _clamp_progress(meta.get("progress"), default=0)
    new_status = norm_status or str(meta.get("status", DEFAULT_STATUS))
    # Reaching 100% implies the goal is achieved unless the caller said otherwise.
    if new_progress == 100 and not norm_status and new_status == "active":
        new_status = "achieved"

    entry: dict[str, Any] = {"at": now, "progress": new_progress, "status": new_status}
    if note.strip():
        entry["note"] = note.strip()

    log = _coerce_log(meta.get("log"))
    log.append(entry)
    if len(log) > MAX_LOG_ENTRIES:
        log = log[-MAX_LOG_ENTRIES:]

    meta.update(progress=new_progress, status=new_status, updated=now, log=log)
    meta.setdefault("title", title_or_slug.strip())
    meta.setdefault("slug", slug)
    meta.setdefault("priority", DEFAULT_PRIORITY)
    meta.setdefault("created", now)

    try:
        await _atomic_write(path, _serialize_goal(meta, body))
    except OSError as exc:
        return _error(f"Failed to update goal: {exc}", slug=slug)

    view = _public_view(slug, meta, body)
    view.update(ok=True, path=str(path), workspace=str(workspace), entry=entry)
    return view


async def goal_get_impl(title_or_slug: str, *, workspace_raw: str = "") -> dict[str, Any]:
    """Read one goal's full description, metadata, links, and progress log."""
    if not title_or_slug or not title_or_slug.strip():
        return _error("A goal title or slug is required.")
    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title_or_slug)
    goal = await _read_goal(_goal_path(workspace, slug))
    if goal is None:
        return _error(f"No goal named {slug!r}.", slug=slug)
    meta, body = goal
    view = _public_view(slug, meta, body)
    view.update(ok=True, path=str(_goal_path(workspace, slug)), workspace=str(workspace))
    return view


async def _iter_goals(workspace: anyio.Path) -> list[tuple[str, dict[str, Any], str]]:
    """Load every goal as (slug, meta, body), sorted by slug. Empty if none yet."""
    root = goals_dir(workspace)
    if not await root.exists():
        return []
    goals: list[tuple[str, dict[str, Any], str]] = []
    async for entry in root.glob("*.md"):
        goal = await _read_goal(entry)
        if goal is None:
            continue
        meta, body = goal
        slug = str(meta.get("slug") or entry.stem)
        goals.append((slug, meta, body))
    goals.sort(key=lambda g: g[0])
    return goals


# Sort listings by priority (high first) then most-recently-updated.
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


async def goal_list_impl(
    *,
    status: str = "",
    tag: str = "",
    limit: int = DEFAULT_LIST_LIMIT,
    workspace_raw: str = "",
) -> dict[str, Any]:
    """List goals, optionally filtered by status and/or tag, with a progress rollup."""
    if limit <= 0:
        limit = DEFAULT_LIST_LIMIT
    status_filter = _normalize_status(status, default="")
    if status_filter is None:
        return _error(f"status filter must be one of {', '.join(STATUSES)}.")
    tag_filter = tag.strip().lower()

    workspace = _bg.resolve_workspace(workspace_raw)
    goals = await _iter_goals(workspace)
    counts: dict[str, int] = dict.fromkeys(STATUSES, 0)
    rows: list[dict[str, Any]] = []
    for slug, meta, body in goals:
        summary = _summary_view(slug, meta, body)
        counts[summary["status"]] = counts.get(summary["status"], 0) + 1
        if status_filter and summary["status"] != status_filter:
            continue
        if tag_filter and tag_filter not in [t.lower() for t in summary["tags"]]:
            continue
        rows.append(summary)

    # Stable sort: most-recently-updated first, then bubble high priority up.
    rows.sort(key=lambda r: str(r.get("updated") or ""), reverse=True)
    rows.sort(key=lambda r: _PRIORITY_RANK.get(r["priority"], 1))
    return {
        "ok": True,
        "workspace": str(workspace),
        "count": len(rows[:limit]),
        "total": len(goals),
        "status_counts": counts,
        "goals": rows[:limit],
    }


async def goal_delete_impl(title_or_slug: str, *, workspace_raw: str = "") -> dict[str, Any]:
    """Delete a goal. Reports which other goals linked to it (now broken)."""
    if not title_or_slug or not title_or_slug.strip():
        return _error("A goal title or slug is required.")
    workspace = _bg.resolve_workspace(workspace_raw)
    slug = slugify(title_or_slug)
    path = _goal_path(workspace, slug)
    if not await path.exists():
        return _error(f"No goal named {slug!r}.", slug=slug)

    orphaned: list[str] = []
    for other_slug, _, body in await _iter_goals(workspace):
        if other_slug != slug and slug in extract_links(body):
            orphaned.append(other_slug)
    try:
        await path.unlink()
    except OSError as exc:
        return _error(f"Failed to delete goal: {exc}", slug=slug)
    return {
        "ok": True,
        "workspace": str(workspace),
        "slug": slug,
        "deleted": True,
        "orphaned_backlinks": sorted(orphaned),
    }
