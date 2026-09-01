"""Private glue for the Feishu chart tools — render a chart, then place it.

Every ``feishu_chart_*`` tool is the same three steps with a different drawing:
build the ``draw`` closure, render it to a PNG under the workspace, and either append
it to a Feishu doc as an image block or leave the file on disk. That shape lives here
so the 21 tool functions stay thin — each one is its own argument contract plus one
``place()`` call — and so a fix to the placement path fixes all of them at once.
``place_figure()`` is the same path for a multi-panel figure.

Captions are numbered here rather than by the caller: ``resolve_caption`` derives "图 N"
from the document's own contents, which is what keeps a report's figure numbers in
sequence when several tool calls (or several turns) each add one.

Charts are written under ``<workspace>/charts/`` with a timestamped name: the agent
may need to hand the same PNG to Word/PPT or send it to a chat, and a stable on-disk
artifact makes that possible without re-rendering.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _chart_caption as _cap  # noqa: E402
import _chart_render as _cr  # noqa: E402
import _feishu_impl as _f  # noqa: E402
import _runtime_paths as _paths  # noqa: E402
import anyio  # noqa: E402


def _slug(text: str, fallback: str) -> str:
    """A short filesystem-safe stem from a chart title (CJK kept, separators dropped)."""
    keep = [ch for ch in text.strip() if ch.isalnum() or ch in "-_"]
    return ("".join(keep)[:32] or fallback).strip("-_") or fallback


async def _chart_path(kind: str, title: str) -> str:
    """``<workspace>/charts/<kind>-<title>-<timestamp>.png``, directory ensured."""
    base = anyio.Path(_paths.workspace_dir()) / "charts"
    await base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return str(base / f"{kind}-{_slug(title, kind)}-{stamp}.png")


async def resolve_caption(
    caption: str,
    *,
    document_id: str,
    kind: str = _cap.FIGURE,
    auto_number: bool = True,
    panel_titles: list[str] | None = None,
    user_key: str = "",
    identity: str = "",
) -> tuple[str, dict[str, Any]]:
    """Turn a caption body into a numbered caption, plus fields describing what happened.

    Returns the caption text and its number, e.g. 图 3 plus ``{"caption_number": 3}``.
    When numbering is off, or the document can't be read to count what's already there,
    the caption goes in unnumbered and the reason is reported — a chart is still worth
    having without its number, and silently writing a *guessed* number is the bug this
    replaces.
    """
    body = _cap.strip_own_number(caption, kind)
    if not body and not panel_titles:
        return "", {}
    if not auto_number:
        return _cap.format_caption(kind, 0, body, panel_titles), {}
    numbered = await _cap.next_number(document_id, kind, user_key, identity)
    if not numbered.get("ok"):
        return (
            _cap.format_caption(kind, 0, body, panel_titles),
            {"caption_number_skipped": numbered.get("reason", "could not read the document")},
        )
    number = int(numbered["number"])
    return _cap.format_caption(kind, number, body, panel_titles), {"caption_number": number}


async def place(
    draw: Any,
    *,
    kind: str,
    title: str,
    document_id: str = "",
    caption: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """Render ``draw`` to a PNG and, when a document is given, append it as an image block.

    Returns the JSON string every tool hands back. An empty ``document_id`` is a
    legitimate mode, not an error: the caller may want the chart file to attach to a
    Word report, drop into a PPT, or send to a chat via ``[SEND:path]``.

    Data problems (bad JSON, mismatched series) come back as ``{"ok": false}`` with a
    fixable message rather than a traceback — the agent can correct the arguments and
    retry. Anything else propagates, since a broken renderer shouldn't look like bad
    user input.

    The caption's "图 N" is derived from the document (see ``resolve_caption``), not from
    the caller's text, so numbers stay in sequence across separate tool calls.
    """
    try:
        path = await _chart_path(kind, title)
        rendered = await _cr.render_to_png(draw, path)
    except _cr.ChartDataError as exc:
        return _f.dumps_result(_f.error_result(str(exc)))
    return await _finish(
        rendered,
        kind=kind,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
        extra=extra,
    )


async def place_figure(
    draws: list[Any],
    *,
    panel_titles: list[str],
    layout: str = "horizontal",
    figure_title: str = "",
    source: str = "",
    document_id: str = "",
    caption: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Render several charts as panels of one figure and place it with one numbered caption.

    The combined-figure counterpart to ``place``: identical upload/caption path, so the
    two can't drift apart, differing only in that the PNG holds several panels and the
    caption names them "(a) … ; (b) …".
    """
    try:
        path = await _chart_path("figure", figure_title or (panel_titles[0] if panel_titles else "figure"))
        rendered = await _cr.render_panels_to_png(draws, path, layout=layout, figure_title=figure_title, source=source)
    except _cr.ChartDataError as exc:
        return _f.dumps_result(_f.error_result(str(exc)))
    return await _finish(
        rendered,
        kind="figure",
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        panel_titles=panel_titles,
        user_key=user_key,
        identity=identity,
        extra={"panels": len(draws), "layout": layout},
    )


async def _finish(
    rendered: str,
    *,
    kind: str,
    document_id: str,
    caption: str,
    auto_number: bool,
    panel_titles: list[str] | None = None,
    user_key: str = "",
    identity: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """Shared tail for both placement paths: number the caption, upload, report."""
    result: dict[str, Any] = {"ok": True, "chart_type": kind, "image_path": rendered}
    warning = _cr.chart_font_warning()
    if warning:
        result["warning"] = warning
    if extra:
        result.update(extra)
    if document_id.strip():
        text, caption_fields = await resolve_caption(
            caption,
            document_id=document_id,
            auto_number=auto_number,
            panel_titles=panel_titles,
            user_key=user_key,
            identity=identity,
        )
        # Report the caption actually written: the caller passed a body without a number,
        # so the numbered text is only knowable from here. Without it the caller can see
        # ``caption_number`` but not the line it ended up under. Empty means nothing was
        # written, and an empty ``caption`` field would read as if something was.
        if text:
            result["caption"] = text
        result.update(caption_fields)
        placed = await _f.append_doc_image_impl(document_id, rendered, text, user_key, identity)
        if not placed.get("ok"):
            # The PNG is still on disk and usable, so say so instead of implying the
            # whole operation produced nothing.
            placed["image_path"] = rendered
            placed["hint"] = "the chart rendered fine but couldn't be placed in the doc; the PNG path is usable."
            return _f.dumps_result(placed)
        result.update({k: v for k, v in placed.items() if k != "ok"})
    else:
        result["note"] = "no document_id given — the PNG is on disk only (use it for Word/PPT or [SEND:path])."
    return _f.dumps_result(result)


def fail(message: str) -> str:
    """A tool-level argument error, in the same shape as every other tool result."""
    return _f.dumps_result(_f.error_result(message))
