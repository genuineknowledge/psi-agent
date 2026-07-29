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
from itertools import pairwise
from math import ceil, radians, sin
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


def _source_note(fig: Any, source: str) -> None:
    """Footnote the data provenance, bottom-left, in muted small type.

    Every chart that makes a claim should say where the numbers came from; keeping it
    in one helper means the wording and placement stay identical across chart types.
    Registered as the figure's supxlabel rather than free-floating ``fig.text`` so
    constrained layout reserves a strip for it instead of letting the axes draw over it.
    """
    if source:
        fig.supxlabel(f"数据来源：{source}", fontsize=10, color=_MUTED, ha="left", x=0.01)  # noqa: RUF001


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
    """
    if not title:
        return
    if has_legend:
        ax.figure.suptitle(title, x=0.01, ha="left", fontsize=17, fontweight="bold", color=_INK)
    else:
        ax.set_title(title, loc="left")


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
    ax.annotate(
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
        if fig.get_layout_engine() is not None:
            fig.get_layout_engine().execute(fig)
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
    _source_note(ax.figure, source)


def _finish_bare_axes(ax: Any, *, title: str = "", source: str = "") -> None:
    """Closing pass for charts that build their own frame instead of using `_finish_axes`.

    Funnel, heatmap and progress draw their own spines, ticks and colourbar, so they
    can't take the shared frame — but they still need the parts that keep text apart.
    Without this they were the only charts left overlapping at high category counts,
    because the tick work lived solely in `_finish_axes`.
    """
    if title:
        ax.set_title(title, loc="left")
    _clip_ticks_to_view(ax)
    _thin_tick_labels(ax)
    _source_note(ax.figure, source)


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
        if fig.get_layout_engine() is not None:
            fig.get_layout_engine().execute(fig)
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
        if fig.get_layout_engine() is not None:
            fig.get_layout_engine().execute(fig)
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
            ax.text(
                0, 0.08, _fmt_number(total, unit), ha="center", va="center", fontsize=20, color=_INK, fontweight="bold"
            )
            ax.text(0, -0.16, "合计", ha="center", va="center", fontsize=12, color=_MUTED)
        ax.set_aspect("equal")
        if title:
            ax.set_title(title, loc="left")
        _fit_pie_pcts(ax, list(autotexts))
        _source_note(fig, source)

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

        # A radar needs polar axes; the caller's cartesian ax is replaced in place.
        ax.remove()
        polar = fig.add_subplot(111, polar=True)
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
        if title:
            polar.set_title(title, loc="left", pad=24)
        if len(series) > 1:
            polar.legend(loc="lower left", bbox_to_anchor=(1.02, 0), frameon=False)
        _source_note(fig, source)

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
