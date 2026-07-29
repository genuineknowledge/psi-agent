"""Private glue for the Feishu chart tools — render a chart, then place it.

Every ``feishu_chart_*`` tool is the same three steps with a different drawing:
build the ``draw`` closure, render it to a PNG under the workspace, and either append
it to a Feishu doc as an image block or leave the file on disk. That shape lives here
so the 20 tool functions stay thin — each one is its own argument contract plus one
``place()`` call — and so a fix to the placement path fixes all of them at once.

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


async def place(
    draw: Any,
    *,
    kind: str,
    title: str,
    document_id: str = "",
    caption: str = "",
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
    """
    try:
        path = await _chart_path(kind, title)
        rendered = await _cr.render_to_png(draw, path)
    except _cr.ChartDataError as exc:
        return _f.dumps_result(_f.error_result(str(exc)))
    result: dict[str, Any] = {"ok": True, "chart_type": kind, "image_path": rendered}
    warning = _cr.chart_font_warning()
    if warning:
        result["warning"] = warning
    if extra:
        result.update(extra)
    if document_id.strip():
        placed = await _f.append_doc_image_impl(document_id, rendered, caption, user_key, identity)
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
