"""goal toolset — define and track high-level goals for the agent.

Haitun has no goal-tracking system. ``todo`` covers a single session's steps and
the ``taskflow`` skill keeps a task/project board, but neither holds the durable
*high-level intent* the agent is working toward — "ship the payments rewrite",
"reach 90% coverage", "learn this codebase". This toolset gives the agent a
persistent place to declare those goals and record progress against them.

Each goal lives as a Markdown file under ``<workspace>/goals/`` with YAML
frontmatter (title, status, priority, progress 0-100, target date, tags,
timestamps) plus an append-only progress ``log`` and a description body. Goals
link related goals with ``[[slug]]`` (e.g. a goal naming its sub-goals).

- ``goal_set`` — create/update a goal (title, description, status, priority, %, target date, tags).
- ``goal_progress`` — record a dated progress entry and optionally move %/status.
- ``goal_get`` — read one goal's full state, links, and progress log.
- ``goal_list`` — list goals with a status rollup; filter by status/tag.
- ``goal_delete`` — remove a goal.

The heavy logic lives in ``_goal_impl`` so tool discovery stays light. Storage
is async ``anyio`` file IO + ``pyyaml`` frontmatter, both already core
dependencies — no extra packages.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _goal_impl as _g


async def goal_set(
    title: str,
    description: str = "",
    status: str = "",
    priority: str = "",
    progress: str = "",
    target_date: str = "",
    tags: str = "",
    overwrite: bool = True,
) -> str:
    """Create or update a high-level goal the agent is working toward.

    Use this to declare durable intent that outlives one task — what "done"
    looks like and why it matters — then track it over time with
    ``goal_progress``. The slug (filename) is derived from ``title``; setting
    the same title again updates that goal, preserving its creation time and
    progress log. Fields left empty keep their existing value on update.
    Cross-reference sub-goals or related goals inline with ``[[other-goal]]``.

    Args:
        title: The goal's title. Its slug (lowercased, dash-joined) is the filename.
        description: Markdown body: the definition of done, scope, rationale. Use ``[[slug]]`` to link related goals.
        status: One of active, paused, achieved, abandoned (default active on create).
        priority: One of low, medium, high (default medium on create).
        progress: Percent complete, 0-100 (default 0 on create). Empty keeps the current value.
        target_date: Optional target/deadline as free text (e.g. "2026-09-01" or "Q3").
        tags: Comma- or space-separated tags for filtering (e.g. "product, q3").
        overwrite: When False, refuse to update an existing goal (default True).

    Returns:
        JSON with ok, slug, path, created (bool), title, status, priority,
        progress, target_date, tags, links, log — or ok=false with a message.
    """
    result = await _g.goal_set_impl(
        title=title,
        description=description,
        status=status,
        priority=priority,
        progress=progress,
        target_date=target_date,
        tags=tags,
        overwrite=overwrite,
    )
    return _g.dumps_result(result)


async def goal_progress(
    title_or_slug: str,
    note: str = "",
    progress: str = "",
    status: str = "",
) -> str:
    """Record a dated progress entry against a goal and optionally move it forward.

    This is the tracking half of the toolset. Each call appends to the goal's
    append-only ``log`` (with a timestamp) so the goal carries its own history,
    and updates the goal's current progress/status when you pass them. Setting
    progress to 100 (or status to ``achieved``) marks the goal done. Provide at
    least one of note, progress, or status.

    Args:
        title_or_slug: The goal title or slug to update.
        note: What happened / what was done this step (recorded in the log).
        progress: New percent complete, 0-100. Empty keeps the current value.
        status: New status: active, paused, achieved, abandoned. Empty keeps current.

    Returns:
        JSON with ok, slug, progress, status, the appended ``entry``, and the
        full goal view — or ok=false with a message if the goal doesn't exist.
    """
    result = await _g.goal_progress_impl(
        title_or_slug,
        note=note,
        progress=progress,
        status=status,
    )
    return _g.dumps_result(result)


async def goal_get(title_or_slug: str) -> str:
    """Read one goal's full state: description, metadata, links, and progress log.

    Accepts either the goal title or its slug.

    Args:
        title_or_slug: The goal title (e.g. "Ship payments v2") or slug ("ship-payments-v2").

    Returns:
        JSON with ok, slug, title, status, priority, progress, target_date,
        tags, created, updated, links, log, description — or ok=false with a
        message if the goal doesn't exist.
    """
    result = await _g.goal_get_impl(title_or_slug)
    return _g.dumps_result(result)


async def goal_list(status: str = "", tag: str = "", limit: int = 100) -> str:
    """List goals with a status rollup, optionally filtered by status and/or tag.

    Gives you the agent's goal dashboard: each goal's slug, title, status,
    priority, progress %, target date, tags, and last note — ordered high
    priority and most-recently-updated first. ``status_counts`` rolls up how
    many goals sit in each status across the whole set.

    Args:
        status: Optional status filter: active, paused, achieved, abandoned. Empty lists all.
        tag: Optional tag to filter by (case-insensitive). Empty lists all.
        limit: Maximum number of goals to return (default 100).

    Returns:
        JSON with ok, count, total, status_counts, and a ``goals`` list
        ({slug, title, status, priority, progress, target_date, tags, updated,
        links, last_note}) — or ok=false with a message.
    """
    result = await _g.goal_list_impl(status=status, tag=tag, limit=limit)
    return _g.dumps_result(result)


async def goal_delete(title_or_slug: str) -> str:
    """Delete a goal.

    Reports which other goals linked to the deleted one (their ``[[links]]`` are
    now broken) so you can fix or repoint them.

    Args:
        title_or_slug: The goal title or slug to delete.

    Returns:
        JSON with ok, slug, deleted, orphaned_backlinks — or ok=false with a
        message if the goal doesn't exist.
    """
    result = await _g.goal_delete_impl(title_or_slug)
    return _g.dumps_result(result)
