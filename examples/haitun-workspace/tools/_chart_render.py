"""Private helper for the Feishu chart tools — render data charts to PNG.

Feishu's docx open API can't *draw* a native chart: block_type 21 (diagram) and 44
(board) are empty canvases the API can't populate, and the Sheets API exposes no
chart-creation endpoint. The one thing that lands a real, correct-looking chart in
a Feishu doc is an **image block** (block_type 27) whose media we upload. So this
module owns the "make the picture" half: matplotlib renders a PNG to disk and
``_feishu_impl`` uploads it into the document.

Design goals, in order:

1. **Legible in a doc.** Charts are read inline at ~700px wide on a white page, so
   everything is sized for that: 1600x900 @ 200 DPI (crisp on retina), 13-15pt
   labels, no cramped tick text, generous margins.
2. **Consistent house style.** One palette, one font stack, one grid treatment
   across every chart type, so five charts in one report look like a set instead of
   five different tools' output.
3. **Readable Chinese.** matplotlib's default font has no CJK glyphs and silently
   renders 中文 as tofu boxes (□□□). We resolve a real CJK family per platform and
   also fix the minus-sign glyph those fonts break.
4. **Annotated by default.** Percentages on pies, value labels on bars, unit-aware
   axis text. A chart the reader can quote numbers off of beats one they have to
   eyeball against gridlines.

All disk IO goes through ``anyio`` (never ``pathlib``/``asyncio``); matplotlib is
CPU-bound and thread-unsafe at module level, so rendering runs inside
``anyio.to_thread.run_sync`` under a lock rather than on the event loop.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from itertools import pairwise
from math import ceil, radians, sin, sqrt
from typing import Any

import anyio

# ── House style ───────────────────────────────────────────────────────────────
# A qualitative palette tuned for white-background business docs: distinct hues at
# similar perceived lightness, so no single series screams louder than the rest and
# the set still separates when printed greyscale. Feishu blue leads, since these
# charts live in Feishu docs.
PALETTE = (
    "#3370FF",  # Feishu blue
    "#FF8800",  # amber
    "#34C724",  # green
    "#F5222D",  # red
    "#7A5AF8",  # violet
    "#00B8D9",  # cyan
    "#FFAB00",  # gold
    "#8C6E4A",  # brown
    "#E75B9E",  # pink
    "#4E5969",  # slate
)
# Sequential ramp for heatmaps / single-variable intensity (light → Feishu blue).
SEQUENTIAL = ("#EAF1FF", "#C2D6FF", "#94B7FF", "#6595FF", "#3370FF", "#1D4ED8", "#12328F")

_INK = "#1F2329"  # primary text
_MUTED = "#646A73"  # secondary text / tick labels
_GRID = "#E5E6EB"  # gridlines, spines

# Tags for the texts the panel closing pass has to find again. Position and content can't
# identify them: a funnel's in-bar text and a heatmap's cell values are also axes texts,
# and touching one of those would delete data from the chart.
_GLYPH_KEY_GID = "psi-glyph-key"  # the "◇ 均值 — 中位数" key under a box plot
_DONUT_TOTAL_GID = "psi-donut-total"  # the total in a donut's hole
_DONUT_UNIT_GID = "psi-donut-unit"  # the "合计" caption under it

# Rendered at 8x4.5in @ 200 DPI = 1600x900 px. Wide enough for a dense time axis,
# 16:9 so it never dominates the page when Feishu scales it to column width.
_FIG_W, _FIG_H, _DPI = 8.0, 4.5, 200

# CJK families by platform, best first. matplotlib needs a family it can actually
# find installed; a missing family degrades to DejaVu Sans and every Chinese glyph
# becomes a tofu box, so we probe the real font list instead of trusting a name.
_CJK_CANDIDATES = (
    "Microsoft YaHei",  # Windows
    "PingFang SC",  # macOS
    "Hiragino Sans GB",  # macOS (older)
    "Noto Sans CJK SC",  # Linux (Noto)
    "Source Han Sans SC",  # Linux (Adobe)
    "WenQuanYi Zen Hei",  # Linux (fallback)
    "SimHei",  # Windows (fallback)
    "Heiti SC",
    "Arial Unicode MS",
)

_style_lock = anyio.Lock()
_style_ready = False


def _resolve_cjk_family() -> list[str]:
    """Font-family stack whose first entry is a CJK font actually installed here.

    Returns the candidates that matplotlib's font manager can resolve, followed by
    DejaVu Sans as the Latin/symbol backstop. An empty CJK result is not fatal —
    ASCII charts still render fine — but Chinese labels would show as boxes, which
    ``chart_font_warning()`` surfaces to the caller.
    """
    from matplotlib import font_manager  # noqa: PLC0415

    installed = {f.name for f in font_manager.fontManager.ttflist}
    found = [name for name in _CJK_CANDIDATES if name in installed]
    return [*found, "DejaVu Sans"]


def _apply_style() -> None:
    """Install the house style into matplotlib's global rcParams (idempotent).

    Called once per process from inside the render thread. Uses the non-interactive
    Agg backend — these charts are written to disk, never shown in a window, and a
    GUI backend would try to reach a display server and fail on a headless host.
    """
    global _style_ready
    if _style_ready:
        return
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg", force=True)
    from matplotlib import rcParams  # noqa: PLC0415

    rcParams["font.sans-serif"] = _resolve_cjk_family()
    rcParams["font.family"] = "sans-serif"
    # CJK fonts ship a full-width minus that matplotlib renders as tofu; ASCII
    # hyphen is the standard workaround for negative tick labels (-5, -12%).
    rcParams["axes.unicode_minus"] = False
    rcParams["figure.figsize"] = (_FIG_W, _FIG_H)
    rcParams["figure.dpi"] = _DPI
    rcParams["savefig.dpi"] = _DPI
    rcParams["figure.facecolor"] = "white"
    rcParams["axes.facecolor"] = "white"
    # NOT savefig.bbox="tight": Feishu shows an image block at the PNG's own pixel
    # size (verified against the live API — `replace_image` overwrites any width/height
    # we send with the file's real dimensions, and no later patch can change them). A
    # tight bbox crops to whatever the content happens to be, so every chart came out a
    # different size — 26 distinct sizes across 54 charts — and the narrow ones rendered
    # as thumbnails in the doc. A fixed canvas keeps every chart one predictable size.
    rcParams["savefig.bbox"] = "standard"
    rcParams["savefig.pad_inches"] = 0.0
    # Constrained layout replaces the cropping: instead of trimming the canvas to fit
    # the text, it shrinks the axes so titles, legends and tick labels fit inside a
    # canvas whose size never moves.
    rcParams["figure.constrained_layout.use"] = True
    rcParams["figure.constrained_layout.h_pad"] = 0.08
    rcParams["figure.constrained_layout.w_pad"] = 0.08
    rcParams["font.size"] = 13
    rcParams["axes.titlesize"] = 17
    rcParams["axes.titleweight"] = "bold"
    rcParams["axes.titlepad"] = 14
    rcParams["axes.labelsize"] = 13
    rcParams["axes.labelcolor"] = _MUTED
    rcParams["axes.edgecolor"] = _GRID
    rcParams["axes.titlecolor"] = _INK
    rcParams["xtick.color"] = _MUTED
    rcParams["ytick.color"] = _MUTED
    rcParams["xtick.labelsize"] = 12
    rcParams["ytick.labelsize"] = 12
    rcParams["legend.fontsize"] = 12
    rcParams["legend.frameon"] = False
    rcParams["grid.color"] = _GRID
    rcParams["grid.linewidth"] = 0.8
    rcParams["lines.linewidth"] = 2.4
    rcParams["lines.markersize"] = 6
    _style_ready = True


def chart_font_warning() -> str:
    """Non-empty when no CJK font is installed, so Chinese labels would be boxes."""
    stack = _resolve_cjk_family()
    if stack[:-1]:
        return ""
    return (
        "no CJK font found on this host — Chinese labels may render as boxes (□). "
        "Install one of: Microsoft YaHei / PingFang SC / Noto Sans CJK SC."
    )


# ── Shared axis / annotation treatment ─────────────────────────────────────────


class ChartDataError(ValueError):
    """Caller-facing data problem (bad JSON, empty series, mismatched lengths).

    Raised by the parse helpers and turned into a normal ``{"ok": false}`` result by
    the tool layer, so the agent gets a fixable message instead of a stack trace.
    """


def _settle_layout(fig: Any) -> None:
    """Run the layout engine so the next measurement sees final positions.

    Takes whatever figure it is handed, including a ``SubFigure``: in a combined figure the
    draw helpers are given one, and only the root figure owns a layout engine (a subfigure
    has no ``get_size_inches``, so calling ``execute`` on it raises). Walking up to the root
    is also correct rather than merely safe — a panel's space is decided by the whole grid.
    """
    root = getattr(fig, "figure", fig) or fig
    while getattr(root, "figure", root) is not root:
        root = root.figure
    engine = root.get_layout_engine()
    if engine is not None:
        engine.execute(root)


# ── Panel mode ─────────────────────────────────────────────────────────────────
# A combined figure (see ``render_panels_to_png``) draws several charts as subplots of
# one canvas. The 20 ``draw_*`` closures work unchanged in a subplot — except for the
# two annotations they place at *figure* level: ``_set_title`` promotes a title to
# ``fig.suptitle`` when a legend is present, and ``_source_note`` uses ``fig.supxlabel``.
# A figure has exactly one of each, so in a 2-panel figure the second panel's title and
# source silently overwrite the first panel's (verified: two panels with sources "S1"
# and "S2" leave only "S2" on the canvas).
#
# This flag tells those two helpers to stay axes-local. It's a ContextVar rather than a
# plain global because ``render_to_png`` renders under a lock but the flag is read from
# inside the draw closures, and a ContextVar keeps that state tied to the render that
# set it instead of leaking to whatever renders next after an exception.
_panel_mode: ContextVar[bool] = ContextVar("psi_chart_panel_mode", default=False)


def _source_note(ax: Any, source: str) -> None:
    """Footnote the data provenance, bottom-left, in muted small type.

    Every chart that makes a claim should say where the numbers came from; keeping it
    in one helper means the wording and placement stay identical across chart types.
    Registered as the figure's supxlabel rather than free-floating ``fig.text`` so
    constrained layout reserves a strip for it instead of letting the axes draw over it.

    Takes the *axes* rather than the figure because in panel mode the note has to land on
    the panel it describes: one ``supxlabel`` can't hold several panels' provenance, so
    a per-panel source becomes that panel's own xlabel and the figure-level slot is left
    for the figure's shared source.
    """
    if not source:
        return
    text = f"数据来源：{source}"  # noqa: RUF001
    if _panel_mode.get():
        ax.set_xlabel(text, fontsize=9, color=_MUTED, loc="left")
        return
    ax.figure.supxlabel(text, fontsize=10, color=_MUTED, ha="left", x=0.01)


def _set_title(ax: Any, title: str, *, has_legend: bool) -> None:
    """Place the chart title so a legend can never sit on top of it.

    ``ax.set_title`` draws just above the axes — exactly where a legend anchored at
    ``bbox_to_anchor=(0, 1.02)`` also goes, so with both present the two rendered on
    the same line and the title came out struck through by the legend swatches (seen
    on area, line, grouped/stacked bar, histogram, combo and gantt).

    With a legend, the title is promoted to a figure-level suptitle: constrained layout
    then stacks title row → legend row → axes and no two of them can occupy the same
    band. Without a legend, the plain axes title is still the right thing — it stays
    tied to the axes and needs no extra reserved space.

    Panel mode never promotes to the *root* figure's slot: that one holds the combined
    figure's own title, and a panel writing there would erase its neighbour's. It is left
    as a plain axes title here and moved into the panel's own subfigure title band by
    ``_promote_panel_titles`` once the tags are on — which is a band of its own, so unlike
    a pad it clears the legend without costing the plot box any height.
    """
    if not title:
        return
    if _panel_mode.get():
        # The pad that lifts this clear of the legend is applied in a closing pass over
        # the whole figure, not here: `_tag_panel` re-sets the title to prefix "(a)" and
        # would reset any pad set now.
        ax.set_title(title, loc="left", fontsize=13)
    elif has_legend:
        ax.figure.suptitle(title, x=0.01, ha="left", fontsize=17, fontweight="bold", color=_INK)
    else:
        ax.set_title(title, loc="left")


def _align_panel_plot_boxes(fig: Any) -> None:
    """Give every panel a plot box of identical size, so the panels read as one figure.

    Constrained layout sizes each panel's plot box around that panel's own decorations, so
    a chart with a two-line legend and a long y label ends up with a visibly smaller box
    than its neighbour (measured 1409x588 next to 1468x515). Side by side the charts then
    look like different sizes — which is what a reader notices first, before any single
    panel's internals.

    The common box is the *intersection*: the largest rectangle that fits inside every
    panel's own allocation, so no panel is grown into the space its labels need. Applied as
    a position in each panel's subfigure fraction, after the engine has run and been
    switched off; leaving the engine on would let the next draw reflow it all back.

    An axes with a fixed aspect (a pie, a heatmap) keeps its aspect: matplotlib re-derives
    its box from the aspect, so it is given the same allocation and stays square inside it.
    """
    # A heatmap's colourbar is an axes as well, but it isn't a panel: it has no subplotspec,
    # and pulling its narrow strip into the intersection would size every plot box to it.
    axes = [ax for ax in fig.get_axes() if ax.get_subplotspec() is not None]
    if len(axes) < 2:
        return
    _settle_layout(fig)
    fig.set_layout_engine("none")
    # `original=True`: on an aspect-locked axes the plain getter returns the square
    # matplotlib shrank the allocation into (a donut reported 0.36 wide inside a 0.98-wide
    # cell), and intersecting that would starve every other panel down to a pie's width.
    boxes = [ax.get_position(original=True) for ax in axes]
    # In subfigure fractions each panel spans its whole cell, so the fractions are directly
    # comparable across panels regardless of where the cell sits on the canvas.
    x0 = max(b.x0 for b in boxes)
    y0 = max(b.y0 for b in boxes)
    x1 = min(b.x1 for b in boxes)
    y1 = min(b.y1 for b in boxes)
    if x1 <= x0 or y1 <= y0:
        return
    for ax in axes:
        ax.set_position((x0, y0, x1 - x0, y1 - y0))


def _promote_panel_titles(fig: Any) -> None:
    """Move each panel's axes title up into its subfigure's own title band.

    A panel title and a legend anchored at ``bbox_to_anchor=(0, 1.02)`` both land just
    above the axes, so the legend swatches struck through the title (measured at 1600x900:
    legend 821-884px, title 836-874px, entirely inside it).

    Padding the title was the wrong instrument, and made the charts worse: ``pad`` moves
    the title but constrained layout answers by shrinking the axes to make room, so the
    loop chased its own tail and left a 1455x207 plot box out of an available 1455x589 —
    the panels came out flattened, and a square chart like a donut no longer matched its
    neighbour's shape.

    A subfigure has its own ``suptitle`` slot, which constrained layout stacks *above* the
    legend row as a separate band. The plot box keeps its full height, every panel's box
    ends up the same size, and no measurement or iteration is involved.

    Runs after ``_tag_panel`` because that re-sets the title to prefix its "(a)".
    """
    for ax in fig.get_axes():
        title = ax.get_title(loc="left")
        if not title:
            continue
        parent = ax.get_figure()
        # Only a subfigure's slot is free to take it; a top-level figure's suptitle holds
        # the combined figure's own title, which a panel must not overwrite.
        if parent is None or parent is fig:
            continue
        # With `loc`, or the left-aligned title stays put and the figure carries two
        # copies of it: `set_title("")` defaults to the centre slot and clears nothing.
        ax.set_title("", loc="left")
        parent.suptitle(title, x=0.0, ha="left", fontsize=13, color=_INK)


def _settle_panel_annotations(fig: Any) -> None:
    """Re-place, on the finished figure, the labels whose position depends on the layout.

    Two kinds of text are positioned during the draw from measurements of an axes that
    later changes size: the glyph key under a box plot (offset to clear the x tick labels)
    and a bar's value label (headroom above the tallest bar). A panel's axes is a fraction
    of the canvas the draw assumed, so both landed in the wrong place — the box key ran
    into its own tick labels, and a column's top value pushed into the title band.

    Rather than re-deriving each chart's own logic here, the fix is the two things that
    always work after the fact: give the data extra headroom so value labels stay inside
    the axes, and drop a glyph key that no longer has room below the ticks. Both are
    checked against measured pixels, not chart type.
    """
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:
        return
    _settle_layout(fig)
    for ax in fig.get_axes():
        _raise_ylim_for_top_labels(ax, renderer)
        _fit_donut_centre(ax, renderer)
    # Thinning has to come after the ylim work, which changes how many ticks the locator
    # emits, and it re-runs the same logic the draw already applied — the draw measured a
    # full-size axes, while a panel gets a fraction of that height, so ticks that cleared
    # each other there overlap here (measured: 25px of pitch for 35px-tall labels).
    _settle_layout(fig)
    for ax in fig.get_axes():
        _clip_ticks_to_view(ax)
        _thin_tick_labels(ax)
    _settle_layout(fig)
    for ax in fig.get_axes():
        _drop_note_colliding_with_ticks(ax, renderer)


def _raise_ylim_for_top_labels(ax: Any, renderer: Any) -> None:
    """Grow the y range until every value label sits inside the axes.

    A bar label is drawn a few points above its bar, so on a short panel the tallest bar's
    label ends up above the axes — in the title's band. Raising the top limit moves the
    bars down within the same axes instead of moving the text, which keeps the label
    attached to its bar. Only ever grows, and only for vertical bars: the y axis is what
    the labels stick out of.
    """
    # The glyph key hangs *below* the axes by design, so it must not drive headroom above.
    labels = [t for t in ax.texts if t.get_text().strip() and t.get_gid() != _GLYPH_KEY_GID]
    if not labels or ax.get_yscale() != "linear":
        return
    top = ax.get_window_extent().y1
    overflow = max((t.get_window_extent(renderer).y1 - top for t in labels), default=0.0)
    if overflow <= 0:
        return
    low, high = ax.get_ylim()
    span = high - low
    height = ax.get_window_extent().height
    if span <= 0 or height <= 0:
        return
    # Convert the overflow into data units and add it, plus a small margin.
    ax.set_ylim(low, high + span * (overflow + 8.0) / height)


def _drop_note_colliding_with_ticks(ax: Any, renderer: Any) -> None:
    """Hide a glyph key that has come to overlap the tick labels it was placed under.

    The key is a convenience ("◇ 均值 — 中位数"), while a tick label names the data, so if
    only one can be legible it must be the tick. The alternative — pushing the key further
    down — walks it off the panel, where a reader sees a clipped half-line.
    """
    keys = [t for t in ax.texts if t.get_gid() == _GLYPH_KEY_GID and t.get_visible()]
    ticks = [t for t in ax.get_xticklabels() if t.get_text().strip() and t.get_visible()]
    if not keys or not ticks:
        return
    boxes = [t.get_window_extent(renderer) for t in ticks]
    for note in keys:
        box = note.get_window_extent(renderer)
        if any(box.x0 < b.x1 and b.x0 < box.x1 and box.y0 < b.y1 and b.y0 < box.y1 for b in boxes):
            note.set_visible(False)


def _legend_note(ax: Any, note: str) -> None:
    """A right-aligned glyph key ("◇ 均值 — 中位数") under the axes.

    Placed as an axes-relative annotation rather than ``fig.text(y=0.005)``: a figure
    coordinate is fixed to the canvas, so constrained layout doesn't know to keep space
    for it and the bottom tick labels drew straight through it. Anchoring below the axes
    puts it in the layout's reserved margin instead.

    The offset clears whatever the x tick labels actually occupy, measured after they
    have been tilted and thinned. A fixed offset can't work for both: horizontal labels
    end ~35px below the axes, tilted ones reach past 120px and ran through the note.
    """
    if not note:
        return
    drop = 30.0
    fig = ax.figure
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:
        renderer = None
    if renderer is not None:
        floor = ax.get_window_extent().y0
        for text in ax.get_xticklabels():
            if text.get_text().strip() and text.get_visible():
                drop = max(drop, floor - text.get_window_extent(renderer=renderer).y0)
    placed = ax.annotate(
        note,
        xy=(1.0, 0),
        xycoords="axes fraction",
        xytext=(0, -(drop + 14.0) * 72.0 / _DPI),
        textcoords="offset points",
        fontsize=10,
        color=_MUTED,
        ha="right",
        va="top",
    )
    # Tagged so the panel closing pass can find this specific annotation. It can't be
    # identified by position or content: a funnel's in-bar text and a heatmap's cell values
    # are also axes texts, and hiding one of those would delete data from the chart.
    placed.set_gid(_GLYPH_KEY_GID)


def _clip_ticks_to_view(ax: Any) -> None:
    """Drop ticks the locator placed outside the visible range.

    A locator picks round numbers, so an axis whose data stops at 80.2 still gets a tick
    at 100. Matplotlib draws that label anyway, one full step *beyond* the axes — outside
    the box constrained layout reserved — where it lands on top of whatever is above,
    which is how a y label came to sit across the chart title.

    Only the label is dropped, never the limits: rescaling the axis to a round number
    would change what the chart claims about the data.
    """
    from matplotlib.ticker import FixedLocator  # noqa: PLC0415

    for axis, low, high in ((ax.xaxis, *sorted(ax.get_xlim())), (ax.yaxis, *sorted(ax.get_ylim()))):
        locs = [t for t in axis.get_ticklocs() if low - 1e-9 <= t <= high + 1e-9]
        if locs and len(locs) != len(axis.get_ticklocs()):
            axis.set_major_locator(FixedLocator(locs))


def _tilt_crowded_x_labels(ax: Any, renderer: Any) -> None:
    """Tilt x tick labels 30° when upright ones are wider than the gap between them.

    Each chart used to make this call from a character count — ``len(label) > 4``, ``> 5``
    or ``> 6`` depending on which function you were in — which asks the wrong question.
    What decides whether labels collide is the label's *rendered width* against the space
    actually available to it: a three-character CJK label like 渠道1 clears every one of
    those thresholds and still overlaps, while a longer label on a wide axis was tilted
    for no reason. Measuring both replaces all seven guesses.

    The spacing has to come from where the ticks really land, not from axes width divided
    by label count: ticks sit at data coordinates, so a heatmap with 6 columns over 31 rows
    draws its labels 42px apart inside a 1319px axes. Dividing would have called that a
    220px slot and left the labels overlapping.
    """
    labels = [t for t in ax.get_xticklabels() if t.get_text().strip()]
    if len(labels) < 2 or any(t.get_rotation() for t in labels):
        return
    boxes = [t.get_window_extent(renderer=renderer) for t in labels]
    centres = sorted((b.x0 + b.x1) / 2 for b in boxes)
    pitch = min(b - a for a, b in pairwise(centres))
    widest = max(b.width for b in boxes)
    if widest + 6.0 <= pitch:
        return
    for text in labels:
        text.set_rotation(30)
        text.set_ha("right")
        text.set_rotation_mode("anchor")


def _thin_tick_labels(ax: Any) -> None:
    """Drop every Nth tick label, on both axes, until the rest stop overlapping.

    A 31-day Gantt axis or a long month series asks for more labels than the axis is
    long enough to hold, and matplotlib happily overlaps them into an unreadable smear
    (measured: 16 colliding pairs on a one-month plan). Horizontal charts — bar, funnel,
    progress, heatmap rows — crowd the *vertical* axis the same way, so both are thinned:
    an earlier x-only version left every horizontal chart broken at 31 categories.

    Extents are *measured*, not estimated from character counts, so this behaves the same
    for two-character months, ISO dates and long CJK names, and doesn't shift when the
    CJK fallback font differs between machines.

    Measuring is a loop rather than a single pass because the two quantities involved
    depend on each other: dropping labels frees margin, constrained layout hands that
    space back to the axes, and a longer axis then has room for labels that were just
    removed. One pass computed its budget against the pre-layout box and left gantt and
    heatmap still overlapping. Iterating to a fixed point settles in 2-3 rounds; the cap
    is there so an oscillating case degrades to "slightly too sparse" instead of hanging.

    Only fixed (explicitly set) tick locations are thinned. A numeric auto-scaled axis
    picks its own non-crowding ticks, and re-spacing those would fight the locator.
    """
    from matplotlib.ticker import FixedLocator  # noqa: PLC0415

    fig = ax.figure
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:  # a backend with no renderer to ask; leave the ticks alone
        return
    _tilt_crowded_x_labels(ax, renderer)
    # Full label set per axis, so each round re-thins from the original rather than
    # compounding earlier strides (which overshoots to a handful of labels).
    full: dict[Any, tuple[list[float], list[str]]] = {}
    for axis, getter in ((ax.xaxis, ax.get_xticklabels), (ax.yaxis, ax.get_yticklabels)):
        if isinstance(axis.get_major_locator(), FixedLocator):
            ticks, labels = list(axis.get_ticklocs()), [t.get_text() for t in getter()]
            if len(ticks) == len(labels) >= 2:
                full[axis] = (ticks, labels)
    if not full:
        return
    setters = {ax.xaxis: ax.set_xticklabels, ax.yaxis: ax.set_yticklabels}
    getters = {ax.xaxis: ax.get_xticklabels, ax.yaxis: ax.get_yticklabels}
    strides = dict.fromkeys(full, 1)
    # `set_ticklabels` builds fresh Text objects at default rotation, so the 30° tilt a
    # chart applied to long labels would be silently dropped — the labels then measure
    # wider than the budget just computed and collide worse than before thinning.
    styles = {
        axis: (texts[0].get_rotation(), texts[0].get_ha(), texts[0].get_va())
        for axis in full
        if (texts := getters[axis]())
    }

    def restyle(axis: Any) -> None:
        rotation, ha, va = styles.get(axis, (0.0, "center", "center"))
        for text in getters[axis]():
            text.set_rotation(rotation)
            text.set_ha(ha)
            text.set_va(va)

    for _round in range(5):
        _settle_layout(fig)
        changed = False
        for axis, (ticks, labels) in full.items():
            shown = [t for t in getters[axis]() if t.get_text().strip()]
            if len(shown) < 2:
                continue
            boxes = [t.get_window_extent(renderer=renderer) for t in shown]
            horizontal = axis is ax.xaxis
            # Pitch is measured between the labels as drawn, because ticks sit at data
            # coordinates: 6 heatmap columns over 31 rows land 42px apart inside a 1319px
            # axes, and dividing axes length by label count would call that a 220px slot.
            centres = sorted(((b.x0 + b.x1) / 2 if horizontal else (b.y0 + b.y1) / 2) for b in boxes)
            pitch = min(b - a for a, b in pairwise(centres))
            # Tilted labels are parallel strips, so what they need along the axis is not
            # their diagonal extent but the spacing that keeps those strips apart: a strip
            # of text height h at angle θ clears its neighbour once the tick pitch exceeds
            # h/sin θ. That is why tilting buys room at all — at 30° a 36px-tall label
            # needs 72px of pitch instead of its full 174px width.
            angle = radians(shown[0].get_rotation() % 180)
            line_h = max(box.height for box in boxes)
            if horizontal and angle:
                need = line_h / sin(angle)
            elif horizontal:
                need = max(box.width for box in boxes) + 6.0
            else:
                need = line_h + 6.0
            if pitch <= 0 or need <= pitch:
                continue
            stride = max(1, min(len(labels) // 2, ceil(need / pitch) * strides[axis]))
            if stride > strides[axis]:
                strides[axis] = stride
                keep = list(range(0, len(labels), stride))
                axis.set_major_locator(FixedLocator([ticks[i] for i in keep]))
                setters[axis]([labels[i] for i in keep])
                restyle(axis)
                changed = True
        if not changed:
            break


def _finish_axes(
    ax: Any,
    *,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    grid_axis: str = "y",
    legend: bool = False,
    legend_cols: int = 0,
    note: str = "",
    source: str = "",
) -> None:
    """Apply the shared frame: title, axis labels, one-directional grid, legend, source note.

    Only the two spines that carry meaning are kept — a full box around a chart adds
    ink without information. The grid runs along a single axis (the one you read
    values off), sits *behind* the data, and stays light enough to not compete with it.
    """
    if x_label:
        ax.set_xlabel(x_label)
    if y_label:
        ax.set_ylabel(y_label)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRID)
    if grid_axis in ("x", "y", "both"):
        ax.grid(axis=grid_axis, linestyle="-", alpha=0.9)
        ax.set_axisbelow(True)
    drawn_legend = ax.get_legend() is not None
    if legend and not drawn_legend:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            # Legend above the plot in one row: a right-side legend steals width from
            # the data, and a boxed in-plot legend covers it.
            ncol = legend_cols if legend_cols > 0 else min(len(handles), 5)
            ax.legend(
                handles,
                labels,
                loc="lower left",
                bbox_to_anchor=(0, 1.02),
                ncol=ncol,
                borderaxespad=0,
                handlelength=1.6,
            )
            drawn_legend = True
    # After the legend, so the title knows whether it has to move out of its way.
    _set_title(ax, title, has_legend=drawn_legend)
    # Clip before thinning: an out-of-view tick would otherwise be counted in the
    # label budget and could survive as the one label kept from its stride.
    _clip_ticks_to_view(ax)
    _thin_tick_labels(ax)
    # After the tick work: the note is placed clear of the x labels, so it has to know
    # their final tilt and count.
    _legend_note(ax, note)
    _source_note(ax, source)


def _finish_bare_axes(ax: Any, *, title: str = "", source: str = "") -> None:
    """Closing pass for charts that build their own frame instead of using `_finish_axes`.

    Funnel, heatmap and progress draw their own spines, ticks and colourbar, so they
    can't take the shared frame — but they still need the parts that keep text apart.
    Without this they were the only charts left overlapping at high category counts,
    because the tick work lived solely in `_finish_axes`.
    """
    # Through _set_title (not ax.set_title) so panel mode gets its smaller panel type.
    _set_title(ax, title, has_legend=False)
    _clip_ticks_to_view(ax)
    _thin_tick_labels(ax)
    _source_note(ax, source)


def _fmt_number(value: float, unit: str = "", decimals: int | None = None) -> str:
    """Format a value for a data label: thousands separators, trimmed decimals, unit.

    ``decimals=None`` picks a sensible precision from magnitude — big numbers read
    better as integers (12,480), small ones need a digit or two (0.85) or the label
    collapses to a meaningless "1".
    """
    if decimals is None:
        magnitude = abs(value)
        if magnitude >= 100 or float(value).is_integer():
            decimals = 0
        elif magnitude >= 1:
            decimals = 1
        else:
            decimals = 2
    text = f"{value:,.{decimals}f}"
    return f"{text}{unit}" if unit else text


def _row_label_size(ax: Any, rows: int, base: float = 11.0) -> float:
    """Font size for one-label-per-row charts, shrunk to the row pitch when rows are many.

    Funnel and progress charts write a value label inside or beside every row, so the
    crowding limit is the *row count*, not the label text: 31 rows in an 790px axes
    leaves 25px of pitch while an 11pt line box is 34px tall, and the labels overlap no
    matter how short they are. Thinning is not an option here — a skipped row would look
    like a row with no value — so the type scales down to fit instead.

    Clamped at 6pt: below that the label is unreadable anyway, and the caller is better
    off having been told the chart has too many rows.
    """
    height = ax.get_window_extent().height
    if rows < 2 or height <= 0:
        return base
    pitch = height / rows
    # A text line box runs ~1.35x its point size in pixels at this dpi; leave a little
    # air between rows on top of that.
    fits = pitch / (1.45 * (_DPI / 72.0))
    return max(6.0, min(base, fits))


def _fit_column_labels(ax: Any, labels: list[Any]) -> None:
    """Shrink side-by-side value labels until each fits its own column.

    These sit above vertical bars, so unlike the row case the limit is label *width*
    against column pitch, and the text is wider than it is tall: 31 waterfall steps in a
    1300px axes give 42px of pitch for labels like "+59" that measure ~50px, and adjacent
    steps collided. Shrinking keeps every step labelled, which thinning would not.
    """
    fig = ax.figure
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:
        return
    shown = [t for t in labels if t.get_text().strip()]
    if len(shown) < 2:
        return
    for _round in range(6):
        _settle_layout(fig)
        boxes = [t.get_window_extent(renderer=renderer) for t in shown]
        centres = sorted((box.x0 + box.x1) / 2 for box in boxes)
        pitch = min(b - a for a, b in pairwise(centres))
        widest = max(box.width for box in boxes)
        size = shown[0].get_fontsize()
        if pitch <= 0 or widest + 4.0 <= pitch or size <= 6.0:
            return
        for text in shown:
            text.set_fontsize(max(6.0, size - 1.0))


def _label_bars(
    ax: Any, containers: Any, unit: str = "", decimals: int | None = None, horizontal: bool = False
) -> None:
    """Write each bar's value at its tip so the reader can quote numbers directly."""
    for container in containers:
        labels = [_fmt_number(bar.get_width() if horizontal else bar.get_height(), unit, decimals) for bar in container]
        ax.bar_label(container, labels=labels, padding=3, fontsize=11, color=_MUTED)


# ── Input parsing ──────────────────────────────────────────────────────────────
# Tool arguments arrive as JSON strings (the tool ABI is plain scalars), so every
# chart tool funnels through these. Error messages name the expected shape and show
# a literal example: an agent that got the shape wrong can fix it from the message
# alone without re-reading the docstring.


def _loads(raw: str, what: str, example: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ChartDataError(f"{what} must be valid JSON, e.g. {example}. Parse error: {exc}") from exc


def _as_float(value: Any, where: str) -> float:
    """Coerce one cell to float, accepting the string forms models tend to emit.

    "1,234", "85%" and "￥1200" are all things an LLM writes when transcribing a
    table; rejecting them would push a formatting fight onto the caller for no gain.
    A percent sign is stripped, not divided — a pie of [30%, 70%] means [30, 70].
    """
    if isinstance(value, bool):
        raise ChartDataError(f"{where} must be a number, got a boolean.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "").replace("￥", "").replace("$", "")
        try:
            return float(text)
        except ValueError as exc:
            raise ChartDataError(f"{where} must be a number, got {value!r}.") from exc
    raise ChartDataError(f"{where} must be a number, got {type(value).__name__}.")


def parse_labels(raw: str, what: str = "labels") -> list[str]:
    """A JSON array of category labels → list[str]."""
    data = _loads(raw, what, '\'["研发","市场","销售"]\'')
    if not isinstance(data, list) or not data:
        raise ChartDataError(f"{what} must be a non-empty JSON array of strings.")
    return ["" if item is None else str(item) for item in data]


def parse_values(raw: str, what: str = "values") -> list[float]:
    """A JSON array of numbers → list[float]."""
    data = _loads(raw, what, "'[12,34,56]'")
    if not isinstance(data, list) or not data:
        raise ChartDataError(f"{what} must be a non-empty JSON array of numbers.")
    return [_as_float(item, f"{what}[{i}]") for i, item in enumerate(data)]


def parse_series(raw: str, what: str = "series") -> list[tuple[str, list[float]]]:
    """Multi-series input → ordered [(series_name, values)].

    Accepts an object mapping name→values (the natural form, and the one that keeps
    legend names attached to their data) or a bare array of value-arrays (auto-named
    系列1, 系列2…). Series lengths are validated against the shared category axis by
    ``check_series_length``, not here, so the error can name the axis length.
    """
    data = _loads(raw, what, '\'{"2025":[10,20],"2026":[14,25]}\'')
    pairs: list[tuple[str, list[float]]] = []
    if isinstance(data, dict):
        if not data:
            raise ChartDataError(f"{what} object is empty.")
        for name, values in data.items():
            if not isinstance(values, list) or not values:
                raise ChartDataError(f"{what}[{name!r}] must be a non-empty array of numbers.")
            pairs.append((str(name), [_as_float(v, f"{what}[{name!r}][{i}]") for i, v in enumerate(values)]))
        return pairs
    if isinstance(data, list):
        if not data:
            raise ChartDataError(f"{what} array is empty.")
        for idx, values in enumerate(data):
            if not isinstance(values, list) or not values:
                raise ChartDataError(f"{what}[{idx}] must be a non-empty array of numbers.")
            pairs.append((f"系列{idx + 1}", [_as_float(v, f"{what}[{idx}][{i}]") for i, v in enumerate(values)]))
        return pairs
    raise ChartDataError(f"{what} must be a JSON object of name→values or an array of arrays, e.g. '{{\"A\":[1,2]}}'.")


def check_series_length(series: list[tuple[str, list[float]]], labels: list[str], what: str = "series") -> None:
    """Every series must align with the category axis, or the chart silently lies."""
    for name, values in series:
        if len(values) != len(labels):
            raise ChartDataError(
                f"{what}[{name!r}] has {len(values)} values but there are {len(labels)} labels — "
                "each series must have exactly one value per label."
            )


def parse_matrix(raw: str, rows: int, cols: int, what: str = "values") -> list[list[float]]:
    """A JSON 2-D numeric array validated against an expected shape."""
    data = _loads(raw, what, "'[[1,2],[3,4]]'")
    if not isinstance(data, list) or not data:
        raise ChartDataError(f"{what} must be a non-empty JSON 2-D array of numbers.")
    if len(data) != rows:
        raise ChartDataError(f"{what} has {len(data)} rows but {rows} row labels were given.")
    matrix: list[list[float]] = []
    for r, row in enumerate(data):
        if not isinstance(row, list):
            raise ChartDataError(f"{what}[{r}] must be an array of numbers.")
        if len(row) != cols:
            raise ChartDataError(f"{what}[{r}] has {len(row)} values but {cols} column labels were given.")
        matrix.append([_as_float(v, f"{what}[{r}][{c}]") for c, v in enumerate(row)])
    return matrix


def parse_pairs(raw: str, what: str = "items") -> list[tuple[str, float]]:
    """A JSON object of name→number → ordered [(name, value)].

    Insertion order is preserved rather than sorted: the caller's order is often
    meaningful (region hierarchy, OKR priority), and a tool that silently reorders
    makes a doc's prose disagree with its chart.
    """
    data = _loads(raw, what, '\'{"华东":118,"华北":92}\'')
    if not isinstance(data, dict) or not data:
        raise ChartDataError(f"{what} must be a non-empty JSON object of name→number, e.g. '{{\"华东\":118}}'.")
    return [(str(name), _as_float(value, f"{what}[{name!r}]")) for name, value in data.items()]


def _parse_date(text: str, where: str) -> Any:
    """YYYY-MM-DD → ``datetime.date``, with a message naming the offending field."""
    from datetime import date  # noqa: PLC0415

    parts = str(text).strip().replace("/", "-").split("-")
    if len(parts) != 3:
        raise ChartDataError(f"{where} must be a date like 2026-08-01, got {text!r}.")
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise ChartDataError(f"{where} is not a valid date: {text!r} ({exc}).") from exc


def parse_gantt_tasks(
    raw: str, start_date: str = "", today: str = ""
) -> tuple[list[tuple[str, float, float, str]], list[str], float]:
    """Gantt task objects with real dates → (tasks, tick_labels, today_offset).

    Dates are converted to integer day offsets from the earliest start (or from
    ``start_date`` when given), so the renderer stays date-free. ``end`` is treated as
    inclusive, which is how people write a schedule — "8月1日到8月4日" is four days, not
    three. Returns axis tick labels as MM-DD and the "today" offset (-1 when absent).
    """
    from datetime import timedelta  # noqa: PLC0415

    data = _loads(raw, "tasks", '\'[{"name":"开发","start":"2026-08-05","days":10,"group":"研发"}]\'')
    if not isinstance(data, list) or not data:
        raise ChartDataError("tasks must be a non-empty JSON array of task objects.")
    parsed: list[tuple[Any, Any, str, str]] = []  # (start_date, end_date, name, group)
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ChartDataError(f'tasks[{i}] must be an object with "name" and "start".')
        name = str(item.get("name", "")).strip()
        if not name:
            raise ChartDataError(f'tasks[{i}] needs a non-empty "name".')
        start_raw = str(item.get("start", "")).strip()
        if not start_raw:
            raise ChartDataError(f'tasks[{i}] ({name}) needs a "start" date like 2026-08-01.')
        begin = _parse_date(start_raw, f"tasks[{i}].start")
        end_raw = str(item.get("end", "")).strip()
        days_raw = item.get("days")
        if end_raw:
            finish = _parse_date(end_raw, f"tasks[{i}].end")
            if finish < begin:
                raise ChartDataError(f"tasks[{i}] ({name}) ends before it starts.")
        elif days_raw is not None:
            days = _as_float(days_raw, f"tasks[{i}].days")
            if days <= 0:
                raise ChartDataError(f"tasks[{i}] ({name}) needs days greater than 0.")
            finish = begin + timedelta(days=int(days) - 1)
        else:
            raise ChartDataError(f'tasks[{i}] ({name}) needs either an "end" date or "days".')
        parsed.append((begin, finish, name, str(item.get("group", "")).strip()))

    origin = _parse_date(start_date, "start_date") if start_date.strip() else min(p[0] for p in parsed)
    last = max(p[1] for p in parsed)
    span = (last - origin).days + 1
    if span <= 0:
        raise ChartDataError("start_date is after every task's end date.")
    tasks = [
        (name, float((begin - origin).days), float((finish - begin).days + 1), group)
        for begin, finish, name, group in parsed
    ]
    tick_labels = [(origin + timedelta(days=d)).strftime("%m-%d") for d in range(span + 1)]
    today_offset = -1.0
    if today.strip():
        today_offset = float((_parse_date(today, "today") - origin).days)
    return tasks, tick_labels, today_offset


def parse_point_groups(raw: str, what: str = "points", dims: int = 2) -> list[tuple[str, list[list[float]]]]:
    """Scatter/bubble input as ordered [(group_name, points)].

    Accepts a bare array of tuples (one unnamed group) or an object of
    group→tuples (several named groups), so a caller comparing 直营 vs 加盟 doesn't
    need a different tool than one plotting a single cloud.
    """
    data = _loads(raw, what, "'[[10,22],[15,30]]'")
    if isinstance(data, dict):
        if not data:
            raise ChartDataError(f"{what} object is empty.")
        return [(str(name), _parse_point_rows(pairs, f"{what}[{name!r}]", dims)) for name, pairs in data.items()]
    return [("", _parse_point_rows(data, what, dims))]


def _parse_point_rows(data: Any, what: str, dims: int) -> list[list[float]]:
    """Validate an already-decoded array of numeric tuples."""
    if not isinstance(data, list) or not data:
        raise ChartDataError(f"{what} must be a non-empty JSON array of arrays.")
    out: list[list[float]] = []
    for i, row in enumerate(data):
        if not isinstance(row, list) or len(row) < dims:
            raise ChartDataError(f"{what}[{i}] must be an array of at least {dims} numbers.")
        out.append([_as_float(v, f"{what}[{i}][{j}]") for j, v in enumerate(row[:dims])])
    return out


def parse_points(raw: str, what: str = "points", dims: int = 2) -> list[list[float]]:
    """A JSON array of numeric tuples → list of ``dims``-length rows (x,y[,size])."""
    data = _loads(raw, what, "'[[1,2],[3,4]]'" if dims == 2 else "'[[1,2,30],[3,4,50]]'")
    return _parse_point_rows(data, what, dims)


# ── Render entry point ─────────────────────────────────────────────────────────


async def render_to_png(draw: Any, out_path: str) -> str:
    """Run ``draw(fig, ax)`` in a worker thread and save the figure to ``out_path``.

    matplotlib's pyplot state machine and the global rcParams are process-wide
    mutable state, so concurrent renders (two Feishu users asking for a chart at the
    same time) would interleave figures. The lock serialises them; ``to_thread`` keeps
    the CPU-bound draw off the event loop so the agent stays responsive. Parent
    directories are created with ``anyio.Path`` per the all-async IO rule.
    """
    target = anyio.Path(out_path)
    await target.parent.mkdir(parents=True, exist_ok=True)
    async with _style_lock:
        await anyio.to_thread.run_sync(_render_sync, draw, os.fspath(target))  # ty: ignore
    return os.fspath(target)


def _render_sync(draw: Any, out_path: str) -> None:
    """Thread body: style, figure, draw, save, and always close the figure.

    Every chart is saved at exactly ``_FIG_W x _FIG_H`` inches — see the
    ``savefig.bbox`` note in ``_apply_style`` for why a fixed canvas matters in a Feishu
    doc. Constrained layout does the fitting inside that canvas.

    The ``finally: close(fig)`` matters — a figure left open leaks its canvas, and a
    long-lived agent process rendering hundreds of charts would grow without bound.
    """
    _apply_style()
    import matplotlib.pyplot as plt  # noqa: PLC0415

    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H), layout="constrained")
    try:
        draw(fig, ax)
        fig.savefig(out_path, format="png", facecolor="white")
    finally:
        plt.close(fig)


# ── Combined figures: several panels, one caption ───────────────────────────────
# The academic-paper convention: related views share one numbered figure, each panel
# tagged (a) (b) (c) and named in the caption, e.g. 图 3 followed by the panel names.
# One PNG means one image block in the doc, so the panels can't drift apart from each
# other or from their caption the way separate charts do.
_PANEL_MIN, _PANEL_MAX = 2, 6
# Feishu renders an image block at the PNG's own pixel size, so a canvas that grows
# without limit comes back *smaller* on the page (scaled to column width) — the exact
# failure the fixed single-chart canvas exists to avoid. These caps keep a combined
# figure within a shape a doc column can still show legibly.
_FIGURE_MAX_W, _FIGURE_MAX_H = 20.0, 14.0
_PANEL_TAGS = "abcdefgh"


def panel_grid(count: int, layout: str) -> tuple[int, int]:
    """(rows, cols) for ``count`` panels under ``layout``.

    ``horizontal`` is one row (side-by-side, for comparing across panels), ``vertical``
    one column (stacked, for a sequence), ``grid`` a near-square block that keeps four
    or more panels from stretching the canvas past what a doc column can show.
    """
    mode = (layout or "horizontal").strip().lower()
    if mode in ("horizontal", "h", "row"):
        return 1, count
    if mode in ("vertical", "v", "column", "col"):
        return count, 1
    if mode in ("grid", "auto", "matrix"):
        cols = ceil(sqrt(count))
        return ceil(count / cols), cols
    raise ChartDataError(f"unknown layout {layout!r} — use 'horizontal', 'vertical' or 'grid'.")


def figure_size(rows: int, cols: int) -> tuple[float, float]:
    """Canvas inches for a rows x cols panel grid, each panel the standard chart size.

    Every panel keeps the full single-chart footprint rather than being squeezed into a
    shared canvas: shrinking panels is what turns a combined figure into six unreadable
    thumbnails. Raises when the result would exceed what a doc column can display.
    """
    width, height = _FIG_W * cols, _FIG_H * rows
    if width > _FIGURE_MAX_W or height > _FIGURE_MAX_H:
        raise ChartDataError(
            f"{rows}x{cols} panels at this layout need a {width:.0f}x{height:.0f}in canvas, "
            f"over the {_FIGURE_MAX_W:.0f}x{_FIGURE_MAX_H:.0f}in limit a Feishu doc can show legibly — "
            "use fewer panels, layout='grid', or split into two figures."
        )
    return width, height


def _panel_tag(index: int) -> str:
    return f"({_PANEL_TAGS[index]})" if index < len(_PANEL_TAGS) else f"({index + 1})"


def _tag_panel(ax: Any, index: int) -> None:
    """Label the panel "(a)" / "(b)", centred beneath it, as a paper sets sub-figures.

    The tag is the panel's own subfigure ``supxlabel``: that is a real layout band, so
    constrained layout reserves the strip instead of letting the axes draw over it, and it
    centres on the panel's width so the tags across a row line up with each other.

    Earlier versions put the tag above the axes — first as a separate artist beside the
    title (it collided with the title, both being left-aligned in the same band), then
    prefixed into the title text. Prefixing avoided the collision but left the tags
    ragged: each sat wherever its title started, so they neither aligned with one another
    nor read as sub-figure keys.

    The panel's own descriptive title keeps its band above the axes (see
    ``_promote_panel_titles``); this is only the key the caption refers to.
    """
    parent = ax.get_figure()
    if parent is None:
        return
    tag = _panel_tag(index)
    # A per-panel source note is the axes' xlabel, so the tag can't share that slot; the
    # subfigure's supxlabel sits below it, which is also where a paper puts the key.
    parent.supxlabel(tag, fontsize=13, color=_INK, fontweight="bold")


async def render_panels_to_png(
    draws: list[Any],
    out_path: str,
    *,
    layout: str = "horizontal",
    figure_title: str = "",
    source: str = "",
) -> str:
    """Render several ``draw(fig, ax)`` closures as panels of one PNG.

    Same locking and threading contract as ``render_to_png`` — see that docstring for
    why matplotlib work is serialised off the event loop.
    """
    if len(draws) < _PANEL_MIN:
        raise ChartDataError(f"a combined figure needs at least {_PANEL_MIN} panels; use a single chart tool for one.")
    if len(draws) > _PANEL_MAX:
        raise ChartDataError(
            f"got {len(draws)} panels — more than {_PANEL_MAX} in one figure leaves each too small to read. "
            "Split them into separate figures."
        )
    rows, cols = panel_grid(len(draws), layout)
    size = figure_size(rows, cols)
    target = anyio.Path(out_path)
    await target.parent.mkdir(parents=True, exist_ok=True)
    async with _style_lock:
        await anyio.to_thread.run_sync(  # ty: ignore
            _render_panels_sync, draws, os.fspath(target), rows, cols, size, figure_title, source
        )
    return os.fspath(target)


def _render_panels_sync(
    draws: list[Any],
    out_path: str,
    rows: int,
    cols: int,
    size: tuple[float, float],
    figure_title: str,
    source: str,
) -> None:
    """Thread body for a combined figure: one subplot per draw, then the shared frame.

    ``_panel_mode`` is set for the whole draw pass so the per-chart helpers keep their
    titles and source notes axes-local; the figure's own title and source are written
    after, when they are the only claimants on those figure-level slots.
    """
    _apply_style()
    import matplotlib.pyplot as plt  # noqa: PLC0415

    token = _panel_mode.set(True)
    fig = plt.figure(figsize=size, layout="constrained")
    # One subfigure per cell rather than one axes per cell. A subfigure carries its own
    # suptitle/supxlabel bands, which is what lets a panel's title sit above its legend and
    # its "(a)" sit below its axes without either stealing height from the plot box — see
    # `_promote_panel_titles`. Every cell gets an equal share, so all the plot boxes come
    # out the same size whatever each chart puts around itself.
    cells = fig.subfigures(rows, cols, squeeze=False)
    flat_cells = [cell for row in cells for cell in row]
    try:
        for index, (draw, cell) in enumerate(zip(draws, flat_cells, strict=False)):
            ax = cell.subplots()
            before = set(cell.get_axes())
            draw(cell, ax)
            # A radar removes the axes it was handed and adds a polar one in the same
            # cell, so the tag belongs on whichever axes now holds that panel — tagging
            # the original would attach the label to a detached object that never draws.
            live = ax if ax in cell.get_axes() else next(iter(set(cell.get_axes()) - before), ax)
            _tag_panel(live, index)
        # Unused cells in a grid (5 panels in a 2x3) hold no axes, so nothing to hide.
        if figure_title:
            fig.suptitle(figure_title, x=0.01, ha="left", fontsize=18, fontweight="bold", color=_INK)
        if source:
            fig.supxlabel(f"数据来源：{source}", fontsize=10, color=_MUTED, ha="left", x=0.01)  # noqa: RUF001
        # After the tags and the figure-level text, so it measures the final layout.
        _promote_panel_titles(fig)
        _settle_panel_annotations(fig)
        # Last: it freezes the layout, and the passes above rely on the engine reflowing
        # after they change ylim or drop tick labels.
        _align_panel_plot_boxes(fig)
        fig.savefig(out_path, format="png", facecolor="white")
    finally:
        _panel_mode.reset(token)
        plt.close(fig)


def _colors(n: int) -> list[str]:
    """``n`` palette colours, cycling if a chart has more series than the palette."""
    return [PALETTE[i % len(PALETTE)] for i in range(n)]


# ── Part-of-whole: pie, donut, funnel ──────────────────────────────────────────
# A pie only works when slices are few and differ visibly. Past ~6 slices the small
# ones become unreadable slivers with colliding labels, so we fold the tail into
# "其他" and say so — a legible 6-slice pie plus a note beats a 20-slice pinwheel.
_PIE_MAX_SLICES = 6


def _fold_tail(
    labels: list[str], values: list[float], keep: int, other_name: str = "其他"
) -> tuple[list[str], list[float], int]:
    """Keep the ``keep`` largest slices, sum the rest into one. Returns (labels, values, folded_count)."""
    if len(values) <= keep:
        order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
        return [labels[i] for i in order], [values[i] for i in order], 0
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    head, tail = order[:keep], order[keep:]
    out_labels = [labels[i] for i in head] + [other_name]
    out_values = [values[i] for i in head] + [sum(values[i] for i in tail)]
    return out_labels, out_values, len(tail)


def _fit_donut_centre(ax: Any, renderer: Any) -> None:
    """Shrink a donut's centre total until it fits inside the hole.

    The total is written at a fixed 20pt, which suits a full-size chart: at 1600x900 the
    hole is far wider than "1,010". In a panel the axes shrinks to a fraction of that (207px
    across, against a 156px-wide total), so the same string overflowed the hole and inked
    the slice percentages and its own "合计" label.

    Both centre labels scale by the same ratio so the pair keeps its proportions. The
    floor keeps the total legible; a donut squeezed below it is better reported as too
    small than silently made unreadable.

    The re-stack runs whether or not the shrink fired. Width is what decides the shrink,
    but the two labels sit at offsets in *data* units, which a short axes makes taller in
    pixels: a 2x2 grid gave a 1455x274 axes whose hole cleared "1,010" by 3px, so no
    shrink, while the original offsets put the 59px total straight through 合计.
    """
    total = next((t for t in ax.texts if t.get_gid() == _DONUT_TOTAL_GID), None)
    unit = next((t for t in ax.texts if t.get_gid() == _DONUT_UNIT_GID), None)
    if total is None or unit is None:
        return
    # The ring is 0.42 of the radius wide, so the hole spans the remaining 0.58 across the
    # centre. The pie is drawn to fill the axes, whose extent is known once laid out.
    axes_box = ax.get_window_extent()
    hole = min(axes_box.width, axes_box.height) * 0.58
    widest = max(t.get_window_extent(renderer).width for t in (total, unit))
    if hole > 0 and widest > hole:
        ratio = max(0.4, hole / widest)
        for text in (total, unit):
            text.set_fontsize(text.get_fontsize() * ratio)
    _stack_donut_centre(ax, renderer, total, unit)


def _stack_donut_centre(ax: Any, renderer: Any, total: Any, unit: Any) -> None:
    """Stack the donut's total over its "合计" using their measured heights.

    The two offsets can't be constants (nor constants scaled by the shrink ratio, which
    was the first attempt): the gap they need is a text height, and a height in *data*
    units depends on how many pixels tall the axes currently is. Scaling the offsets down
    alongside the font pulled the two labels into each other — a 45px-tall total centred
    0.08*ratio above the middle, over a "合计" only 0.16*ratio below it, left the pair
    overlapping by 22px.

    So the gap is derived: convert each label's rendered height into data units and place
    the pair symmetrically about the centre with a small margin between them.
    """
    origin = ax.transData.transform((0, 0))
    per_unit = ax.transData.transform((0, 1))[1] - origin[1]
    if per_unit <= 0:
        return
    total_h = total.get_window_extent(renderer).height / per_unit
    unit_h = unit.get_window_extent(renderer).height / per_unit
    margin = total_h * 0.12
    # Centre the block on the hole's middle, total above the divide and 合计 below it.
    half = (total_h + unit_h + margin) / 2
    total.set_position((0, half - total_h / 2))
    unit.set_position((0, -half + unit_h / 2))


def _fit_pie_pcts(ax: Any, autotexts: list[Any]) -> None:
    """Shrink, then drop, percentage labels that don't fit their own slice.

    A slice's label has only its own arc to sit in, and that arc is set by the share:
    six 5% slices side by side give each label ~60px of room for ~76px of text, so
    neighbours ran into each other (seen on any pie whose tail folds into a big
    "其他" and leaves the rest near-equal).

    Text is shrunk to fit first, since a smaller percentage is still readable. Only a
    label that can't fit even at the floor size is hidden — the wedge and its category
    label remain, so nothing about the slice becomes unidentifiable.
    """
    fig = ax.figure
    try:
        renderer = fig.canvas.get_renderer()
    except AttributeError:
        return
    shown = [t for t in autotexts if t.get_text().strip()]
    for _round in range(6):
        # An equal-aspect pie is squared up during the draw, not when the wedges are
        # added: before this settles, the labels report positions from a full-width axes
        # and sit ~65px away from where they will land.
        _settle_layout(fig)
        ax.apply_aspect()
        boxes = [(t, t.get_window_extent(renderer=renderer)) for t in shown if t.get_visible()]
        clashing = {id(a) for (a, box_a), (b, box_b) in pairwise(boxes) if box_a.overlaps(box_b) for a in (a, b)}
        if not clashing:
            return
        for text, _box in boxes:
            if id(text) in clashing:
                size = text.get_fontsize()
                if size > 8.0:
                    text.set_fontsize(max(8.0, size - 1.0))
                else:
                    text.set_visible(False)


def draw_pie(
    labels: list[str],
    values: list[float],
    *,
    title: str = "",
    donut: bool = False,
    unit: str = "",
    show_values: bool = False,
    highlight: int = -1,
    source: str = "",
) -> tuple[Any, int]:
    """Return a ``draw(fig, ax)`` for a pie/donut plus the number of folded slices.

    Slices are sorted largest-first and drawn clockwise from 12 o'clock, which is how
    people expect to read a share breakdown. Each label carries its percentage (and
    optionally the raw value) so the chart is quotable without a legend lookup.
    ``highlight`` explodes one slice to point at the slice under discussion.
    """
    if any(v < 0 for v in values):
        raise ChartDataError("a pie/donut can't show negative values — use a bar chart instead.")
    total = sum(values)
    if total <= 0:
        raise ChartDataError("values sum to 0 — nothing to show as shares.")
    plot_labels, plot_values, folded = _fold_tail(labels, values, _PIE_MAX_SLICES)
    colors = _colors(len(plot_values))
    explode = [0.0] * len(plot_values)
    if 0 <= highlight < len(plot_values):
        explode[highlight] = 0.06

    def _auto(pct: float) -> str:
        if show_values:
            return f"{pct:.1f}%\n{_fmt_number(pct * total / 100, unit)}"
        return f"{pct:.1f}%"

    def draw(fig: Any, ax: Any) -> None:
        _wedges, _texts, autotexts = ax.pie(
            plot_values,
            labels=plot_labels,
            colors=colors,
            explode=explode,
            autopct=_auto,
            startangle=90,
            counterclock=False,  # clockwise: the reading order for shares
            pctdistance=0.72 if donut else 0.62,
            wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2}
            if donut
            else {"edgecolor": "white", "linewidth": 2},
            textprops={"fontsize": 12, "color": _INK},
        )
        for label in autotexts:
            # White on the saturated slice fill is the only reliably legible option
            # across the whole palette.
            label.set_color("white")
            label.set_fontsize(11)
            label.set_fontweight("bold")
        if donut:
            # The hole is prime real estate: put the total there instead of leaving a
            # blank circle the reader has to mentally sum.
            total_text = ax.text(
                0, 0.08, _fmt_number(total, unit), ha="center", va="center", fontsize=20, color=_INK, fontweight="bold"
            )
            unit_text = ax.text(0, -0.16, "合计", ha="center", va="center", fontsize=12, color=_MUTED)
            # Tagged for the closing pass to resize. Fitting it here would measure an axes
            # that constrained layout has not sized yet (it reports the full canvas, then
            # shrinks to ~207px in a panel), so the check would always pass and never fire.
            total_text.set_gid(_DONUT_TOTAL_GID)
            unit_text.set_gid(_DONUT_UNIT_GID)
        ax.set_aspect("equal")
        if title:
            ax.set_title(title, loc="left")
        _fit_pie_pcts(ax, list(autotexts))
        _source_note(ax, source)

    return draw, folded


def draw_funnel(
    stages: list[str],
    values: list[float],
    *,
    title: str = "",
    unit: str = "",
    source: str = "",
) -> Any:
    """Return a ``draw`` for a conversion funnel (centred tapering bars).

    Each stage shows its absolute value, its conversion from the previous stage, and
    its share of the top — the three numbers anyone reading a funnel actually asks
    for. Stages are drawn in the given order (not sorted): a funnel's order is its
    meaning.
    """
    if len(stages) != len(values):
        raise ChartDataError(f"got {len(stages)} stage labels but {len(values)} values — they must match.")
    if any(v < 0 for v in values):
        raise ChartDataError("funnel values can't be negative.")
    top = values[0] if values else 0
    if top <= 0:
        raise ChartDataError("the first funnel stage must be greater than 0 (it's the 100% baseline).")
    colors = _colors(len(values))

    def draw(fig: Any, ax: Any) -> None:
        widths = [v / top for v in values]
        y = list(range(len(values) - 1, -1, -1))  # first stage on top
        # Centre each bar on x=0 so the shape actually tapers like a funnel; a
        # left-aligned version is just a bar chart and loses the drop-off metaphor.
        ax.barh(y, widths, height=0.62, left=[-w / 2 for w in widths], color=colors, edgecolor="white", linewidth=1.5)
        size = _row_label_size(ax, len(values))
        for idx, (value, width) in enumerate(zip(values, widths, strict=True)):
            row = len(values) - 1 - idx
            share = width * 100
            step = "" if idx == 0 else f"　转化 {value / values[idx - 1] * 100:.1f}%" if values[idx - 1] else ""
            text = f"{_fmt_number(value, unit)}（占首层 {share:.1f}%）{step}"  # noqa: RUF001
            # A narrow tail bar can't hold the label; park it to the right in muted
            # ink instead of letting white text spill over the white background.
            if width >= 0.5:
                ax.text(0, row, text, ha="center", va="center", fontsize=size, color="white", fontweight="bold")
            else:
                ax.text(width / 2 + 0.02, row, text, ha="left", va="center", fontsize=size, color=_MUTED)
        ax.set_yticks(y, stages)
        ax.set_xlim(-0.56, 0.86)
        ax.set_xticks([])
        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(False)
        _finish_bare_axes(ax, title=title, source=source)

    return draw


# ── Trend over an ordered axis: line, area, stacked area ───────────────────────


def _thin_ticks(ax: Any, labels: list[str]) -> None:
    """Keep at most ~12 x tick labels so a long time axis stays readable.

    A 52-week series draws 52 overlapping labels into a grey smear; showing every
    n-th label keeps the axis scannable while the line itself still shows all points.
    """
    step = max(1, len(labels) // 12)
    positions = list(range(0, len(labels), step))
    ax.set_xticks(positions, [labels[i] for i in positions])


def _annotate_last(ax: Any, labels: list[str], values: list[float], color: str, unit: str) -> None:
    """Label the final point of a series — the "where did we end up" number."""
    if not values:
        return
    ax.annotate(
        _fmt_number(values[-1], unit),
        xy=(len(values) - 1, values[-1]),
        xytext=(6, 0),
        textcoords="offset points",
        va="center",
        fontsize=11,
        color=color,
        fontweight="bold",
    )


def draw_line(
    labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    markers: bool = True,
    smooth_area: bool = False,
    annotate_last: bool = True,
    zero_baseline: bool = False,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a line chart (optionally filled as an area chart).

    Markers are on by default because a line with ~12 or fewer points reads as a
    trend *and* as discrete observations; the y axis starts wherever the data does
    (not forced to zero) so real variation isn't flattened into a straight line —
    ``zero_baseline`` opts into the honest-magnitude framing when the absolute level
    matters more than the change.
    """
    check_series_length(series, labels)
    colors = _colors(len(series))

    def draw(fig: Any, ax: Any) -> None:
        for (name, values), color in zip(series, colors, strict=True):
            ax.plot(
                range(len(values)),
                values,
                label=name,
                color=color,
                marker="o" if markers and len(values) <= 24 else None,
                markerfacecolor="white",
                markeredgewidth=1.8,
            )
            if smooth_area:
                ax.fill_between(range(len(values)), values, alpha=0.16, color=color)
            if annotate_last and len(series) <= 4:
                _annotate_last(ax, labels, values, color, unit)
        _thin_ticks(ax, labels)
        if zero_baseline:
            ax.set_ylim(bottom=0)
        if unit:
            ax.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        _finish_axes(
            ax,
            title=title,
            x_label=x_label,
            y_label=y_label,
            grid_axis="y",
            legend=len(series) > 1,
            source=source,
        )

    return draw


def draw_stacked_area(
    labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    percent: bool = False,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a stacked area chart — composition changing over time.

    ``percent=True`` normalises each period to 100%, which answers "how did the *mix*
    shift" independently of whether the total grew. Absolute stacking answers "how did
    the total grow, and who contributed". They're different questions; the flag keeps
    both available from one tool.
    """
    check_series_length(series, labels)
    if any(v < 0 for _n, values in series for v in values):
        raise ChartDataError("stacked areas can't show negative values — use a line chart instead.")
    colors = _colors(len(series))
    stacks = [values for _name, values in series]
    names = [name for name, _values in series]
    if percent:
        totals = [sum(col) for col in zip(*stacks, strict=True)]
        if any(t <= 0 for t in totals):
            raise ChartDataError("every period must total more than 0 to show a 100% composition.")
        stacks = [[v / totals[i] * 100 for i, v in enumerate(values)] for values in stacks]

    def draw(fig: Any, ax: Any) -> None:
        ax.stackplot(range(len(labels)), *stacks, labels=names, colors=colors, alpha=0.9, edgecolor="white")
        _thin_ticks(ax, labels)
        ax.set_ylim(bottom=0)
        if percent:
            ax.set_ylim(0, 100)
            ax.yaxis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
        elif unit:
            ax.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        _finish_axes(
            ax,
            title=title,
            x_label=x_label,
            y_label=y_label or ("占比" if percent else ""),
            grid_axis="y",
            legend=True,
            source=source,
        )

    return draw


# ── Comparison across categories: column, bar, grouped, stacked ────────────────


def draw_bar(
    labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    horizontal: bool = False,
    stacked: bool = False,
    percent: bool = False,
    sort_desc: bool = False,
    highlight: int = -1,
    source: str = "",
) -> Any:
    """Return a ``draw`` for column/bar charts — single, grouped, or stacked.

    One function covers all four because they share every axis decision and differ
    only in bar geometry; splitting them would duplicate the labelling logic four
    ways. Bars always start at zero (a truncated bar axis misrepresents ratios, which
    is the whole point of a bar chart). ``horizontal`` is the right call for long
    category names or many categories — vertical labels turn into unreadable
    diagonals past ~8 items.
    """
    check_series_length(series, labels)
    if stacked and any(v < 0 for _n, values in series for v in values):
        raise ChartDataError("stacked bars can't show negative values — use grouped bars instead.")
    names = [name for name, _v in series]
    stacks = [list(values) for _n, values in series]
    cats = list(labels)

    if sort_desc and len(stacks) >= 1:
        # Ranking reads instantly when sorted; sort by the row total so grouped and
        # stacked charts stay internally consistent.
        totals = [sum(col) for col in zip(*stacks, strict=True)]
        order = sorted(range(len(cats)), key=lambda i: totals[i], reverse=not horizontal)
        cats = [cats[i] for i in order]
        stacks = [[values[i] for i in order] for values in stacks]
    elif horizontal:
        # barh draws bottom-up; reverse so the first category sits at the top where a
        # reader starts.
        cats = cats[::-1]
        stacks = [values[::-1] for values in stacks]

    if percent:
        if not stacked:
            raise ChartDataError("percent=True only applies to stacked bars (a 100% composition).")
        totals = [sum(col) for col in zip(*stacks, strict=True)]
        if any(t <= 0 for t in totals):
            raise ChartDataError("every category must total more than 0 to show a 100% composition.")
        stacks = [[v / totals[i] * 100 for i, v in enumerate(values)] for values in stacks]

    colors = _colors(len(stacks))
    if len(stacks) == 1 and 0 <= highlight < len(cats):
        # Single series: grey everything except the bar under discussion, so the eye
        # lands on it without a legend or an arrow.
        idx = len(cats) - 1 - highlight if horizontal and not sort_desc else highlight
        bar_colors: Any = ["#C9CDD4"] * len(cats)
        if 0 <= idx < len(cats):
            bar_colors[idx] = PALETTE[0]
    else:
        bar_colors = None

    def draw(fig: Any, ax: Any) -> None:
        positions = list(range(len(cats)))
        containers = []
        if stacked:
            offsets = [0.0] * len(cats)
            for values, name, color in zip(stacks, names, colors, strict=True):
                plot = ax.barh if horizontal else ax.bar
                kwargs = {"left": list(offsets)} if horizontal else {"bottom": list(offsets)}
                containers.append(
                    plot(positions, values, 0.62, label=name, color=color, edgecolor="white", linewidth=1, **kwargs)
                )
                offsets = [o + v for o, v in zip(offsets, values, strict=True)]
        else:
            group = len(stacks)
            width = 0.72 / group
            for i, (values, name, color) in enumerate(zip(stacks, names, colors, strict=True)):
                shift = (i - (group - 1) / 2) * width
                offset_positions = [p + shift for p in positions]
                plot = ax.barh if horizontal else ax.bar
                containers.append(
                    plot(
                        offset_positions,
                        values,
                        width,
                        label=name,
                        color=bar_colors if bar_colors is not None else color,
                    )
                )
        if horizontal:
            ax.set_yticks(positions, cats)
            ax.set_xlim(left=0)
        else:
            ax.set_xticks(positions, cats)
            ax.set_ylim(bottom=0)
        value_axis = ax.xaxis if horizontal else ax.yaxis
        if percent:
            value_axis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
        elif unit:
            value_axis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        # Value labels on every bar get noisy on a stacked chart (they'd sit inside
        # segments) and on very dense charts; label only where they stay legible.
        if not stacked and len(cats) * len(stacks) <= 24:
            _label_bars(ax, containers, "" if percent else unit, horizontal=horizontal)
        _finish_axes(
            ax,
            title=title,
            x_label=x_label,
            y_label=y_label,
            grid_axis="x" if horizontal else "y",
            legend=len(stacks) > 1,
            source=source,
        )

    return draw


def draw_waterfall(
    labels: list[str],
    deltas: list[float],
    *,
    title: str = "",
    y_label: str = "",
    unit: str = "",
    total_label: str = "合计",
    source: str = "",
) -> Any:
    """Return a ``draw`` for a waterfall chart — how a start value becomes an end value.

    Increases are green, decreases red, and the final total is a full bar from zero in
    Feishu blue: the standard grammar for a bridge chart, so a finance reader needs no
    legend. Connector lines tie each step to the next so the running balance is visible.
    """
    if len(labels) != len(deltas):
        raise ChartDataError(f"got {len(labels)} labels but {len(deltas)} values — they must match.")

    def draw(fig: Any, ax: Any) -> None:
        running = 0.0
        bottoms: list[float] = []
        for delta in deltas:
            bottoms.append(running)
            running += delta
        positions = list(range(len(deltas) + 1))
        colors = ["#34C724" if d >= 0 else "#F5222D" for d in deltas]
        ax.bar(positions[:-1], deltas, 0.6, bottom=bottoms, color=colors, edgecolor="white", linewidth=1)
        ax.bar([positions[-1]], [running], 0.6, color=PALETTE[0], edgecolor="white", linewidth=1)
        step_labels = []
        for i, delta in enumerate(deltas):
            tip = bottoms[i] + delta
            step_labels.append(
                ax.text(
                    i,
                    tip + (abs(running) * 0.02 if delta >= 0 else -abs(running) * 0.02),
                    f"{'+' if delta >= 0 else ''}{_fmt_number(delta, unit)}",
                    ha="center",
                    va="bottom" if delta >= 0 else "top",
                    fontsize=11,
                    color=_MUTED,
                )
            )
            # Connector: the running balance carried into the next step.
            if i < len(deltas) - 1:
                ax.plot([i + 0.3, i + 0.7], [tip, tip], color=_GRID, linewidth=1.2, zorder=0)
        ax.text(
            len(deltas),
            running,
            _fmt_number(running, unit),
            ha="center",
            va="bottom",
            fontsize=11,
            color=_INK,
            fontweight="bold",
        )
        ax.axhline(0, color=_GRID, linewidth=1)
        # Each bar's value is written just outside its tip, but autoscaling stops exactly
        # at the lowest bar — so a decrease at the floor of the chart put its label below
        # the axes, straight on top of the x tick labels. Reserve a band at both ends.
        edges = [*bottoms, *(b + d for b, d in zip(bottoms, deltas, strict=False)), running, 0.0]
        low, high = min(edges), max(edges)
        span = (high - low) or abs(high) or 1.0
        ax.set_ylim(low - span * 0.12, high + span * 0.12)
        ax.set_xticks(positions, [*labels, total_label])
        if unit:
            ax.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        _finish_axes(ax, title=title, y_label=y_label, grid_axis="y", source=source)
        _fit_column_labels(ax, step_labels)

    return draw


# ── Distribution & correlation: scatter, bubble, histogram, box ────────────────


def draw_scatter(
    groups: list[tuple[str, list[list[float]]]],
    *,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    trend: bool = False,
    point_labels: list[str] | None = None,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a scatter plot — does x relate to y?

    ``trend=True`` overlays a least-squares fit line, which is what makes a scatter
    actionable ("as headcount rises, cost per ticket falls") rather than a cloud of
    dots. Point labels are only drawn for small sets, where they clarify instead of
    overlapping into mush.
    """
    colors = _colors(len(groups))

    def draw(fig: Any, ax: Any) -> None:
        for (name, points), color in zip(groups, colors, strict=True):
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            ax.scatter(xs, ys, s=90, color=color, alpha=0.75, edgecolor="white", linewidth=1.2, label=name, zorder=3)
            if trend and len(points) >= 2:
                slope, intercept = _linear_fit(xs, ys)
                span = [min(xs), max(xs)]
                ax.plot(
                    span,
                    [slope * x + intercept for x in span],
                    color=color,
                    linestyle="--",
                    linewidth=1.6,
                    alpha=0.8,
                    zorder=2,
                )
        if point_labels and len(groups) == 1 and len(point_labels) == len(groups[0][1]):
            for (x, y), text in zip(groups[0][1], point_labels, strict=True):
                ax.annotate(text, xy=(x, y), xytext=(7, 5), textcoords="offset points", fontsize=10, color=_MUTED)
        _finish_axes(
            ax, title=title, x_label=x_label, y_label=y_label, grid_axis="both", legend=len(groups) > 1, source=source
        )

    return draw


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares slope/intercept, computed directly to avoid a numpy import here.

    Returns a flat line through the mean when x has no spread (all points share an x),
    which keeps a degenerate input from raising instead of drawing.
    """
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom
    return slope, mean_y - slope * mean_x


def draw_bubble(
    points: list[list[float]],
    *,
    labels: list[str] | None = None,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    size_label: str = "",
    source: str = "",
) -> Any:
    """Return a ``draw`` for a bubble chart — three variables at once (x, y, size).

    Bubble *area* (not radius) is scaled to the third value, because area is what the
    eye judges; scaling radius linearly would exaggerate large values roughly
    quadratically and mislead the reader.
    """
    sizes = [p[2] for p in points]
    smax = max(sizes) if sizes else 0
    if smax <= 0:
        raise ChartDataError("bubble sizes must include at least one value greater than 0.")

    def draw(fig: Any, ax: Any) -> None:
        areas = [140 + 2400 * (s / smax) for s in sizes]
        colors = _colors(len(points))
        ax.scatter(
            [p[0] for p in points],
            [p[1] for p in points],
            s=areas,
            color=colors,
            alpha=0.62,
            edgecolor="white",
            linewidth=1.5,
            zorder=3,
        )
        if labels and len(labels) == len(points):
            for point, text in zip(points, labels, strict=True):
                ax.annotate(
                    text,
                    xy=(point[0], point[1]),
                    ha="center",
                    va="center",
                    fontsize=10,
                    color=_INK,
                    fontweight="bold",
                    zorder=4,
                )
        note = f"气泡大小 = {size_label}" if size_label else ""
        _finish_axes(ax, title=title, x_label=x_label, y_label=y_label, grid_axis="both", note=note, source=source)

    return draw


def draw_histogram(
    values: list[float],
    *,
    bins: int = 0,
    title: str = "",
    x_label: str = "",
    y_label: str = "频数",
    unit: str = "",
    show_mean: bool = True,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a histogram — the shape of one variable's distribution.

    Bin count defaults to the Sturges-style ``ceil(sqrt(n))``, capped at 20: too few
    bins hide bimodality, too many turn the distribution into noise. The mean and
    median lines are what turn "a shape" into "and here's the centre, and it's skewed".
    """
    if len(values) < 2:
        raise ChartDataError("a histogram needs at least 2 values.")
    count = bins if bins > 0 else min(20, max(5, int(len(values) ** 0.5 + 0.999)))
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    mean = sum(values) / len(values)

    def draw(fig: Any, ax: Any) -> None:
        ax.hist(values, bins=count, color=PALETTE[0], alpha=0.85, edgecolor="white", linewidth=1.2)
        if show_mean:
            ax.axvline(mean, color="#F5222D", linestyle="--", linewidth=1.8, label=f"均值 {_fmt_number(mean, unit)}")
            ax.axvline(
                median, color="#FF8800", linestyle=":", linewidth=1.8, label=f"中位数 {_fmt_number(median, unit)}"
            )
        if unit:
            ax.xaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        _finish_axes(ax, title=title, x_label=x_label, y_label=y_label, grid_axis="y", legend=show_mean, source=source)

    return draw


def draw_box(
    groups: list[tuple[str, list[float]]],
    *,
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    source: str = "",
) -> Any:
    """Return a ``draw`` for a box plot — compare distributions, not just averages.

    Means are marked alongside the median so a skewed group is obvious (mean pulled
    away from the median line), and outliers stay visible as individual points rather
    than being clipped — they're usually the interesting part.
    """
    if not groups:
        raise ChartDataError("box plot needs at least one group.")
    for name, values in groups:
        if len(values) < 2:
            raise ChartDataError(f"group {name!r} needs at least 2 values for a box plot.")

    def draw(fig: Any, ax: Any) -> None:
        data = [values for _n, values in groups]
        names = [name for name, _v in groups]
        bp = ax.boxplot(
            data,
            tick_labels=names,
            patch_artist=True,
            showmeans=True,
            widths=0.55,
            medianprops={"color": _INK, "linewidth": 2},
            meanprops={"marker": "D", "markerfacecolor": "white", "markeredgecolor": _INK, "markersize": 6},
            flierprops={
                "marker": "o",
                "markersize": 5,
                "markerfacecolor": "#F5222D",
                "alpha": 0.6,
                "markeredgecolor": "none",
            },
            whiskerprops={"color": _MUTED},
            capprops={"color": _MUTED},
        )
        for patch, color in zip(bp["boxes"], _colors(len(data)), strict=True):
            patch.set_facecolor(color)
            patch.set_alpha(0.55)
            patch.set_edgecolor(color)
        if unit:
            ax.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        _finish_axes(
            ax,
            title=title,
            x_label=x_label,
            y_label=y_label,
            grid_axis="y",
            note="◇ 均值　— 中位数　• 离群点",
            source=source,
        )

    return draw


def draw_heatmap(
    row_labels: list[str],
    col_labels: list[str],
    matrix: list[list[float]],
    *,
    title: str = "",
    unit: str = "",
    show_values: bool = True,
    color_label: str = "",
    source: str = "",
) -> Any:
    """Return a ``draw`` for a heatmap — a 2-D grid where colour encodes intensity.

    Cell values are printed on top by default (a heatmap without numbers forces the
    reader to eyeball the colourbar), and each label flips to white on dark cells so it
    stays legible at both ends of the ramp.
    """
    from matplotlib.colors import LinearSegmentedColormap  # noqa: PLC0415

    flat = [v for row in matrix for v in row]
    if not flat:
        raise ChartDataError("heatmap matrix is empty.")
    vmin, vmax = min(flat), max(flat)

    def draw(fig: Any, ax: Any) -> None:
        cmap = LinearSegmentedColormap.from_list("psi_seq", SEQUENTIAL)
        image = ax.imshow(matrix, cmap=cmap, aspect="auto", vmin=vmin, vmax=vmax)
        ax.set_xticks(range(len(col_labels)), col_labels)
        ax.set_yticks(range(len(row_labels)), row_labels)
        if show_values and len(row_labels) * len(col_labels) <= 120:
            span = (vmax - vmin) or 1
            for r, row in enumerate(matrix):
                for c, value in enumerate(row):
                    # Dark cells need light text; the 62% cut matches where the ramp
                    # gets dark enough that dark ink stops reading.
                    dark = (value - vmin) / span > 0.62
                    ax.text(
                        c,
                        r,
                        _fmt_number(value, unit),
                        ha="center",
                        va="center",
                        fontsize=10,
                        color="white" if dark else _INK,
                    )
        bar = fig.colorbar(image, ax=ax, shrink=0.85)
        if color_label:
            bar.set_label(color_label, fontsize=11, color=_MUTED)
        bar.outline.set_visible(False)
        ax.set_xticks([x - 0.5 for x in range(1, len(col_labels))], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, len(row_labels))], minor=True)
        ax.grid(which="minor", color="white", linewidth=2)
        ax.tick_params(which="minor", length=0)
        for side in ("top", "right", "left", "bottom"):
            ax.spines[side].set_visible(False)
        _finish_bare_axes(ax, title=title, source=source)

    return draw


# ── Purpose-built: radar, pareto, combo, gantt, progress ───────────────────────


def draw_radar(
    axes_labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    title: str = "",
    max_value: float = 0,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a radar/spider chart — multi-dimension capability profiles.

    Radar works when every axis shares a comparable scale (all 1-5 ratings, all
    percentages) and there are 3-8 axes; outside that it distorts. Series are filled
    at low alpha so overlaps stay readable, and the polygon closes back to the first
    axis so the shape is continuous.
    """
    if len(axes_labels) < 3:
        raise ChartDataError("a radar chart needs at least 3 axes (fewer is better shown as a bar chart).")
    check_series_length(series, axes_labels, "series")
    top = max_value if max_value > 0 else max(v for _n, values in series for v in values)

    def draw(fig: Any, ax: Any) -> None:
        import math  # noqa: PLC0415

        # A radar needs polar axes, so the caller's cartesian ax is swapped for one.
        # The replacement is built from the original's own subplot slot, not `111`:
        # inside a combined figure `111` means "the whole canvas", so a radar panel
        # would cover every other panel instead of taking its own cell.
        spec = ax.get_subplotspec()
        ax.remove()
        polar = fig.add_subplot(spec, polar=True) if spec is not None else fig.add_subplot(111, polar=True)
        count = len(axes_labels)
        angles = [n / count * 2 * math.pi for n in range(count)]
        closed = [*angles, angles[0]]
        for (name, values), color in zip(series, _colors(len(series)), strict=True):
            ring = [*values, values[0]]
            polar.plot(closed, ring, color=color, linewidth=2.2, label=name)
            polar.fill(closed, ring, color=color, alpha=0.16)
        polar.set_xticks(angles, axes_labels)
        polar.set_ylim(0, top * 1.05)
        polar.set_rlabel_position(180 / count)
        polar.tick_params(colors=_MUTED, labelsize=12)
        polar.spines["polar"].set_color(_GRID)
        polar.grid(color=_GRID)
        if title and _panel_mode.get():
            # No pad: `_promote_panel_titles` lifts this into the subfigure's title band,
            # and a pad here would only shrink the plot box before that happens.
            polar.set_title(title, loc="left", fontsize=13)
        elif title:
            polar.set_title(title, loc="left", pad=24)
        if len(series) > 1:
            polar.legend(loc="lower left", bbox_to_anchor=(1.02, 0), frameon=False)
        # On `polar`, not the original `ax`: that one was removed above, and a note set
        # on a detached axes never reaches the canvas.
        _source_note(polar, source)

    return draw


def draw_pareto(
    labels: list[str],
    values: list[float],
    *,
    title: str = "",
    y_label: str = "",
    unit: str = "",
    threshold: float = 80.0,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a Pareto chart — bars sorted desc + cumulative % line.

    This is the "which few causes drive most of the effect" chart: bars ranked by
    magnitude, a cumulative line on a right-hand 0-100% axis, and a marker where the
    line crosses ``threshold`` so the vital-few cut is explicit instead of implied.
    """
    if len(labels) != len(values):
        raise ChartDataError(f"got {len(labels)} labels but {len(values)} values — they must match.")
    if any(v < 0 for v in values):
        raise ChartDataError("Pareto values can't be negative.")
    total = sum(values)
    if total <= 0:
        raise ChartDataError("values sum to 0 — nothing to rank.")
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    cats = [labels[i] for i in order]
    vals = [values[i] for i in order]
    cumulative: list[float] = []
    running = 0.0
    for value in vals:
        running += value
        cumulative.append(running / total * 100)
    # First category whose cumulative share reaches the threshold — the cut point.
    cut = next((i for i, pct in enumerate(cumulative) if pct >= threshold), len(cumulative) - 1)

    def draw(fig: Any, ax: Any) -> None:
        positions = list(range(len(cats)))
        # Within-threshold bars in full colour, the long tail greyed: the ranking and
        # the cut are then legible from colour alone.
        bar_colors = [PALETTE[0] if i <= cut else "#C9CDD4" for i in positions]
        ax.bar(positions, vals, 0.62, color=bar_colors)
        ax.set_xticks(positions, cats)
        ax.set_ylim(bottom=0)
        if unit:
            ax.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        right = ax.twinx()
        right.plot(positions, cumulative, color="#FF8800", marker="o", markerfacecolor="white", linewidth=2.2)
        right.set_ylim(0, 105)
        right.yaxis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
        right.axhline(threshold, color=_MUTED, linestyle="--", linewidth=1.2)
        # The callout normally reads left-to-right from the cut point, but a cut in the
        # right-hand half pushes it off the axes and onto the percent tick labels. Past
        # the midpoint, anchor it to the left of the point instead so it grows inward.
        rightward = cut < len(cats) / 2
        right.annotate(
            f"{cats[cut]} 起累计达 {threshold:.0f}%",
            xy=(cut, cumulative[cut]),
            xytext=(10 if rightward else -10, -18),
            textcoords="offset points",
            ha="left" if rightward else "right",
            fontsize=11,
            color="#FF8800",
            fontweight="bold",
        )
        for side in ("top", "left"):
            right.spines[side].set_visible(False)
        right.spines["right"].set_color(_GRID)
        right.tick_params(colors=_MUTED)
        _finish_axes(ax, title=title, y_label=y_label, grid_axis="y", source=source)

    return draw


def draw_combo(
    labels: list[str],
    bar_series: list[tuple[str, list[float]]],
    line_series: list[tuple[str, list[float]]],
    *,
    title: str = "",
    y_label: str = "",
    y2_label: str = "",
    unit: str = "",
    line_unit: str = "",
    line_percent: bool = False,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a combo chart — bars on the left axis, lines on the right.

    The canonical business chart: volume as bars, rate as a line (revenue + margin %,
    headcount + attrition %). Two different units genuinely need two axes; the line
    colours continue after the bar colours so nothing is ambiguous, and both legends
    merge into one row above the plot.
    """
    check_series_length(bar_series, labels, "bar_series")
    check_series_length(line_series, labels, "line_series")
    if not bar_series or not line_series:
        raise ChartDataError("a combo chart needs at least one bar series and one line series.")
    bar_colors = _colors(len(bar_series))
    line_colors = [PALETTE[(len(bar_series) + i) % len(PALETTE)] for i in range(len(line_series))]

    def draw(fig: Any, ax: Any) -> None:
        positions = list(range(len(labels)))
        group = len(bar_series)
        width = 0.62 / group
        for i, ((name, values), color) in enumerate(zip(bar_series, bar_colors, strict=True)):
            shift = (i - (group - 1) / 2) * width
            ax.bar([p + shift for p in positions], values, width, label=name, color=color)
        ax.set_xticks(positions, labels)
        ax.set_ylim(bottom=0)
        if unit:
            ax.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, unit))
        right = ax.twinx()
        for (name, values), color in zip(line_series, line_colors, strict=True):
            right.plot(
                positions,
                values,
                label=name,
                color=color,
                marker="o",
                markerfacecolor="white",
                markeredgewidth=1.8,
                linewidth=2.4,
            )
        if line_percent:
            right.yaxis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
        elif line_unit:
            right.yaxis.set_major_formatter(lambda v, _pos: _fmt_number(v, line_unit))
        if y2_label:
            right.set_ylabel(y2_label, color=_MUTED)
        for side in ("top", "left"):
            right.spines[side].set_visible(False)
        right.spines["right"].set_color(_GRID)
        right.tick_params(colors=_MUTED)
        bar_handles, bar_names = ax.get_legend_handles_labels()
        line_handles, line_names = right.get_legend_handles_labels()
        handles = bar_handles + line_handles
        ax.legend(
            handles,
            bar_names + line_names,
            loc="lower left",
            bbox_to_anchor=(0, 1.02),
            ncol=min(len(handles), 5),
            borderaxespad=0,
            frameon=False,
        )
        _finish_axes(ax, title=title, y_label=y_label, grid_axis="y", source=source)

    return draw


def draw_gantt(
    tasks: list[tuple[str, float, float, str]],
    *,
    title: str = "",
    x_label: str = "",
    tick_labels: list[str] | None = None,
    today: float = -1,
    source: str = "",
) -> Any:
    """Return a ``draw`` for a Gantt chart — task bars along a numeric time axis.

    ``tasks`` is [(name, start, duration, group)]; time is numeric (day/week index) so
    this stays free of date parsing and timezone questions — the tool layer converts
    real dates to day offsets and passes ``tick_labels`` for the axis. Tasks sharing a
    ``group`` share a colour, which is what makes an owner- or phase-coloured plan
    readable. ``today`` draws a "now" line so slippage is visible.
    """
    if not tasks:
        raise ChartDataError("a Gantt chart needs at least one task.")
    if any(duration <= 0 for _n, _s, duration, _g in tasks):
        raise ChartDataError("every task needs a duration greater than 0.")
    groups: list[str] = []
    for _name, _start, _dur, group in tasks:
        if group and group not in groups:
            groups.append(group)
    group_color = {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(groups)}

    def draw(fig: Any, ax: Any) -> None:
        # Reverse so the first task sits at the top, the way a plan is read.
        rows = list(range(len(tasks) - 1, -1, -1))
        bar_label_size = _row_label_size(ax, len(tasks))
        for row, (name, start, duration, group) in zip(rows, tasks, strict=True):
            color = group_color.get(group, PALETTE[0])
            ax.barh([row], [duration], left=[start], height=0.56, color=color, edgecolor="white", linewidth=1.2)
            ax.text(
                start + duration / 2,
                row,
                name,
                ha="center",
                va="center",
                fontsize=bar_label_size,
                color="white",
                fontweight="bold",
            )
        ax.set_yticks(rows, [name for name, _s, _d, _g in tasks])
        ax.set_xlim(left=min(start for _n, start, _d, _g in tasks))
        if tick_labels:
            step = max(1, len(tick_labels) // 12)
            spots = list(range(0, len(tick_labels), step))
            ax.set_xticks(spots, [tick_labels[i] for i in spots])
        if today >= 0:
            ax.axvline(today, color="#F5222D", linestyle="--", linewidth=1.8)
            ax.annotate(
                "今天",
                xy=(today, len(tasks) - 0.4),
                xytext=(4, 0),
                textcoords="offset points",
                fontsize=11,
                color="#F5222D",
                fontweight="bold",
            )
        if groups:
            from matplotlib.patches import Patch  # noqa: PLC0415

            ax.legend(
                handles=[Patch(facecolor=group_color[name], label=name) for name in groups],
                loc="lower left",
                bbox_to_anchor=(0, 1.02),
                ncol=min(len(groups), 5),
                borderaxespad=0,
                frameon=False,
            )
        _finish_axes(ax, title=title, x_label=x_label, grid_axis="x", source=source)

    return draw


def draw_progress(
    items: list[tuple[str, float]],
    *,
    title: str = "",
    target: float = 100.0,
    unit: str = "%",
    source: str = "",
) -> Any:
    """Return a ``draw`` for progress/attainment bars — actual against a target.

    Each row shows the full target as a light track with the achieved portion filled,
    so under- and over-attainment are both visible at a glance; bars that clear the
    target turn green, and shortfalls stay blue with the gap spelled out in the label.
    This is the OKR / quota / completion-rate chart.
    """
    if not items:
        raise ChartDataError("progress chart needs at least one item.")
    if target <= 0:
        raise ChartDataError("target must be greater than 0.")

    def draw(fig: Any, ax: Any) -> None:
        rows = list(range(len(items) - 1, -1, -1))
        size = _row_label_size(ax, len(items))
        for row, (_name, value) in zip(rows, items, strict=True):
            done = value >= target
            ax.barh([row], [target], height=0.5, color="#F2F3F5")
            ax.barh([row], [min(value, target)], height=0.5, color="#34C724" if done else PALETTE[0])
            if value > target:
                # Over-attainment continues past the track in a lighter green so the
                # overshoot is visible rather than silently clipped at 100%.
                ax.barh([row], [value - target], left=[target], height=0.5, color="#7BDA6E")
            pct = value / target * 100
            gap = "" if done else f"（差 {_fmt_number(target - value, unit)}）"  # noqa: RUF001
            ax.text(
                max(value, target) + target * 0.02,
                row,
                f"{_fmt_number(value, unit)} · {pct:.0f}%{gap}",
                va="center",
                fontsize=size,
                color="#34C724" if done else _MUTED,
                fontweight="bold" if done else "normal",
            )
        ax.set_yticks(rows, [name for name, _v in items])
        ax.set_xlim(0, target * 1.42)
        ax.set_xticks([])
        for side in ("top", "right", "bottom"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(_GRID)
        _finish_bare_axes(ax, title=title, source=source)

    return draw


# ── Panel specs: one dict per panel of a combined figure ────────────────────────
# A combined figure can't take 21 tools' worth of flat arguments, so a panel is a dict:
# ``{"chart": "line", "title": …, "labels": [...], "series": {...}}``. The field names are
# the single-chart tools' argument names minus the ``_json`` suffix, so an agent that
# knows ``chart_type="line"(labels_json=…, series_json=…)`` already knows the panel form.
#
# Values arrive already decoded (the whole ``panels_json`` was one JSON document), but
# validation still goes through the same ``parse_*`` helpers by re-encoding each field.
# That costs a trivial round-trip and buys identical validation and identical error
# wording between a panel and the equivalent standalone tool — two code paths that
# disagree about what "series" means is exactly how a combined figure would start
# silently drawing something other than what the tools draw.

# What each chart kind needs from a panel dict, for the "you're missing a field" message.
_PANEL_REQUIRED: dict[str, tuple[str, ...]] = {
    "pie": ("labels", "values"),
    "donut": ("labels", "values"),
    "funnel": ("stages", "values"),
    "line": ("labels", "series"),
    "area": ("labels", "series"),
    "stacked_area": ("labels", "series"),
    "column": ("labels", "values"),
    "bar": ("labels", "values"),
    "grouped_column": ("labels", "series"),
    "stacked_column": ("labels", "series"),
    "waterfall": ("labels", "deltas"),
    "histogram": ("values",),
    "box": ("groups",),
    "scatter": ("points",),
    "bubble": ("points",),
    "heatmap": ("row_labels", "col_labels", "values"),
    "radar": ("axes", "series"),
    "pareto": ("labels", "values"),
    "combo": ("labels", "bar_series", "line_series"),
    "gantt": ("tasks",),
    "progress": ("items",),
}

PANEL_CHARTS = tuple(_PANEL_REQUIRED)


class _Panel:
    """Typed reads off one panel dict, with the panel's index in every error message.

    An agent sending six panels needs to know *which* one it got wrong; "panel 3
    (chart='pie')" is actionable where a bare "values must be an array" is not.
    """

    def __init__(self, spec: Any, index: int) -> None:
        if not isinstance(spec, dict):
            raise ChartDataError(f"panel {index + 1} must be a JSON object, got {type(spec).__name__}.")
        self.index = index
        self.spec = spec
        kind = str(spec.get("chart", "")).strip().lower()
        if not kind:
            raise ChartDataError(f'panel {index + 1} has no "chart" field — one of: {", ".join(PANEL_CHARTS)}.')
        if kind not in _PANEL_REQUIRED:
            raise ChartDataError(f"panel {index + 1}: unknown chart {kind!r} — use one of: {', '.join(PANEL_CHARTS)}.")
        self.kind = kind
        missing = [f for f in _PANEL_REQUIRED[kind] if spec.get(f) in (None, "", [], {})]
        if missing:
            raise ChartDataError(f"panel {index + 1} (chart={kind!r}) is missing: {', '.join(missing)}.")

    @property
    def where(self) -> str:
        return f"panel {self.index + 1} ({self.kind})"

    def raw(self, field: str) -> str:
        """A panel field re-encoded as JSON, so the shared ``parse_*`` helpers can read it."""
        return json.dumps(self.spec.get(field), ensure_ascii=False)

    def text(self, field: str, default: str = "") -> str:
        value = self.spec.get(field, default)
        return default if value is None else str(value)

    def number(self, field: str, default: float) -> float:
        value = self.spec.get(field)
        return default if value in (None, "") else _as_float(value, f"{self.where}.{field}")

    def flag(self, field: str, default: bool = False) -> bool:
        value = self.spec.get(field, default)
        return bool(default if value is None else value)

    def labels(self, field: str, what: str = "") -> list[str]:
        return parse_labels(self.raw(field), f"{self.where}.{what or field}")

    def values(self, field: str, what: str = "") -> list[float]:
        return parse_values(self.raw(field), f"{self.where}.{what or field}")

    def series(self, field: str, what: str = "") -> list[tuple[str, list[float]]]:
        return parse_series(self.raw(field), f"{self.where}.{what or field}")

    def matched(self, labels: list[str], values: list[float], what: str = "values") -> None:
        if len(labels) != len(values):
            raise ChartDataError(f"{self.where}: got {len(labels)} labels but {len(values)} {what} — they must match.")


def _panel_part_of_whole(p: _Panel, title: str, source: str) -> Any:
    """pie / donut / funnel."""
    if p.kind in ("pie", "donut"):
        labels, values = p.labels("labels"), p.values("values")
        p.matched(labels, values)
        draw, _folded = draw_pie(
            labels,
            values,
            title=title,
            donut=p.kind == "donut",
            unit=p.text("unit"),
            show_values=p.flag("show_values"),
            highlight=int(p.number("highlight", -1)),
            source=source,
        )
        return draw
    return draw_funnel(p.labels("stages"), p.values("values"), title=title, unit=p.text("unit"), source=source)


def _panel_trend(p: _Panel, title: str, source: str) -> Any:
    """line / area / stacked_area."""
    labels, series = p.labels("labels"), p.series("series")
    # Spelled out rather than passed as **kwargs: a dict unpack hides which keyword each
    # value lands on, so a typo would reach the chart instead of the type checker.
    x_label, y_label, unit = p.text("x_label"), p.text("y_label"), p.text("unit")
    if p.kind == "stacked_area":
        return draw_stacked_area(
            labels,
            series,
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            percent=p.flag("percent"),
            source=source,
        )
    # `area` is the same builder as `line`, with the fill and zero baseline it implies.
    area = p.kind == "area"
    return draw_line(
        labels,
        series,
        title=title,
        x_label=x_label,
        y_label=y_label,
        unit=unit,
        smooth_area=area,
        zero_baseline=True if area else p.flag("zero_baseline"),
        source=source,
    )


def _panel_comparison(p: _Panel, title: str, source: str) -> Any:
    """column / bar / grouped_column / stacked_column / waterfall."""
    if p.kind == "waterfall":
        return draw_waterfall(
            p.labels("labels"),
            p.values("deltas"),
            title=title,
            y_label=p.text("y_label"),
            unit=p.text("unit"),
            total_label=p.text("total_label", "合计"),
            source=source,
        )
    labels = p.labels("labels")
    # Spelled out rather than passed as **kwargs: a dict unpack hides which keyword each
    # value lands on, so a typo would reach the chart instead of the type checker.
    x_label, y_label, unit = p.text("x_label"), p.text("y_label"), p.text("unit")
    if p.kind in ("column", "bar"):
        values = p.values("values")
        p.matched(labels, values)
        horizontal = p.kind == "bar"
        # Single-series: the axis label doubles as the series name, matching the
        # standalone column/bar tools.
        name = (x_label if horizontal else y_label) or "数值"
        return draw_bar(
            labels,
            [(name, values)],
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            horizontal=horizontal,
            sort_desc=p.flag("sort_desc"),
            highlight=int(p.number("highlight", -1)),
            source=source,
        )
    series = p.series("series")
    return draw_bar(
        labels,
        series,
        title=title,
        x_label=x_label,
        y_label=y_label,
        unit=unit,
        horizontal=p.flag("horizontal"),
        stacked=p.kind == "stacked_column",
        percent=p.flag("percent") if p.kind == "stacked_column" else False,
        source=source,
    )


def _panel_distribution(p: _Panel, title: str, source: str) -> Any:
    """histogram / box / scatter / bubble / heatmap."""
    if p.kind == "histogram":
        return draw_histogram(
            p.values("values"),
            bins=int(p.number("bins", 0)),
            title=title,
            x_label=p.text("x_label"),
            y_label=p.text("y_label", "频数"),
            unit=p.text("unit"),
            source=source,
        )
    if p.kind == "box":
        return draw_box(
            p.series("groups"),
            title=title,
            x_label=p.text("x_label"),
            y_label=p.text("y_label"),
            unit=p.text("unit"),
            source=source,
        )
    if p.kind == "scatter":
        point_labels = p.labels("point_labels") if p.spec.get("point_labels") else None
        return draw_scatter(
            parse_point_groups(p.raw("points"), f"{p.where}.points"),
            title=title,
            x_label=p.text("x_label"),
            y_label=p.text("y_label"),
            trend=p.flag("trend"),
            point_labels=point_labels,
            source=source,
        )
    if p.kind == "bubble":
        points = parse_points(p.raw("points"), f"{p.where}.points", dims=3)
        labels = p.labels("labels") if p.spec.get("labels") else None
        if labels and len(labels) != len(points):
            raise ChartDataError(f"{p.where}: got {len(labels)} labels but {len(points)} bubbles — they must match.")
        return draw_bubble(
            points,
            labels=labels,
            title=title,
            x_label=p.text("x_label"),
            y_label=p.text("y_label"),
            size_label=p.text("size_label"),
            source=source,
        )
    rows, cols = p.labels("row_labels"), p.labels("col_labels")
    return draw_heatmap(
        rows,
        cols,
        parse_matrix(p.raw("values"), len(rows), len(cols), f"{p.where}.values"),
        title=title,
        unit=p.text("unit"),
        show_values=p.flag("show_values", True),
        color_label=p.text("color_label"),
        source=source,
    )


def _panel_purpose_built(p: _Panel, title: str, source: str) -> Any:
    """radar / pareto / combo / gantt / progress."""
    if p.kind == "radar":
        return draw_radar(
            p.labels("axes"), p.series("series"), title=title, max_value=p.number("max_value", 0), source=source
        )
    if p.kind == "pareto":
        labels, values = p.labels("labels"), p.values("values")
        p.matched(labels, values)
        return draw_pareto(
            labels,
            values,
            title=title,
            y_label=p.text("y_label"),
            unit=p.text("unit"),
            threshold=p.number("threshold", 80.0),
            source=source,
        )
    if p.kind == "combo":
        return draw_combo(
            p.labels("labels"),
            p.series("bar_series"),
            p.series("line_series"),
            title=title,
            y_label=p.text("y_label"),
            y2_label=p.text("y2_label"),
            unit=p.text("unit"),
            line_unit=p.text("line_unit"),
            line_percent=p.flag("line_percent"),
            source=source,
        )
    if p.kind == "gantt":
        tasks, tick_labels, today_offset = parse_gantt_tasks(p.raw("tasks"), p.text("start_date"), p.text("today"))
        return draw_gantt(tasks, title=title, tick_labels=tick_labels, today=today_offset, source=source)
    return draw_progress(
        parse_pairs(p.raw("items"), f"{p.where}.items"),
        title=title,
        target=p.number("target", 100.0),
        unit=p.text("unit", "%"),
        source=source,
    )


_PANEL_FAMILIES = (
    (("pie", "donut", "funnel"), _panel_part_of_whole),
    (("line", "area", "stacked_area"), _panel_trend),
    (("column", "bar", "grouped_column", "stacked_column", "waterfall"), _panel_comparison),
    (("histogram", "box", "scatter", "bubble", "heatmap"), _panel_distribution),
    (("radar", "pareto", "combo", "gantt", "progress"), _panel_purpose_built),
)


def build_panel_draw(spec: Any, index: int, *, panel_source: bool = False) -> tuple[Any, str]:
    """One panel dict → (draw closure, panel title) for ``render_panels_to_png``.

    ``panel_source`` decides whether a panel's own ``source`` is drawn under that panel.
    It is off by default because a combined figure normally shares one provenance line
    for the whole figure; repeating it under every panel is noise.
    """
    panel = _Panel(spec, index)
    title = panel.text("title")
    source = panel.text("source") if panel_source else ""
    for kinds, builder in _PANEL_FAMILIES:
        if panel.kind in kinds:
            return builder(panel, title, source), title
    raise ChartDataError(f"panel {index + 1}: unknown chart {panel.kind!r}.")  # unreachable; _Panel validated it


def parse_panels(raw: str, *, panel_source: bool = False) -> tuple[list[Any], list[str]]:
    """``panels_json`` → (draw closures, panel titles), validated panel by panel."""
    data = _loads(raw, "panels", '\'[{"chart":"line","labels":["1月"],"series":{"营收":[10]}}]\'')
    if not isinstance(data, list) or not data:
        raise ChartDataError('panels must be a non-empty JSON array of panel objects, e.g. [{"chart":"pie",…}].')
    draws: list[Any] = []
    titles: list[str] = []
    for index, spec in enumerate(data):
        draw, title = build_panel_draw(spec, index, panel_source=panel_source)
        draws.append(draw)
        titles.append(title)
    return draws, titles
