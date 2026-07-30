"""Feishu/Lark data-chart tools — real pie/line/bar/… charts inside a Feishu doc.

Feishu's docx API has no chart block, and the Sheets API can't create charts, so the
only way to get a genuine data chart into a Feishu document is to render an image and
append it as an image block (block_type 27). These tools do exactly that: each one
takes tidy data, renders a house-styled PNG, and places it in the doc with a caption.

One tool per chart type, because picking the right chart is the hard part and a tool
name that says ``pie`` / ``line`` / ``pareto`` makes the choice explicit — as do the
"use this when" lines in each docstring. Pair with the ``feishu-charts`` skill, which
carries the full chart-selection rules.

Part-of-whole:  ``pie`` ``donut`` ``funnel``
Trend:          ``line`` ``area`` ``stacked_area``
Comparison:     ``column`` ``bar`` ``grouped_column`` ``stacked_column`` ``waterfall``
Distribution:   ``histogram`` ``box`` ``scatter`` ``bubble`` ``heatmap``
Purpose-built:  ``radar`` ``pareto`` ``combo`` ``gantt`` ``progress``

``feishu_chart_figure`` is the odd one out: it takes a list of panel specs and renders
2-6 of them into ONE image with ``(a)`` ``(b)`` ``(c)`` tags and a single caption, the way
a paper presents a multi-part figure.

Every tool shares four arguments: ``document_id`` (empty = render the PNG only, e.g.
to embed in Word/PPT or send with ``[SEND:path]``), ``caption`` (the caption text — the
"图 N" prefix is added by the tool, continuing the numbering already in the document, so
callers must NOT write their own "图N："), ``source`` (a data-provenance footnote), and
``user_key`` (the sender's open_id, needed when the doc is user-owned and the bot isn't a
collaborator).
"""

from __future__ import annotations

# ruff: noqa: E402
# RUF002: these docstrings are read by the agent (and by Chinese-speaking users) as
# prose, so full-width CJK punctuation is correct typography here, not an ASCII typo.
# ruff: noqa: RUF002
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _chart_place as _place
import _chart_render as _cr

# ── Part-of-whole ──────────────────────────────────────────────────────────────


async def feishu_chart_pie(
    labels_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    unit: str = "",
    show_values: bool = False,
    highlight: int = -1,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a pie chart to a Feishu doc — shares of a single whole.

    Use when parts sum to a meaningful 100% and there are **2-6 categories**: budget
    split by department, headcount by function, traffic by channel. Slices are sorted
    largest-first with percentages on each; anything past the 6 biggest is folded into
    "其他" so labels stay legible (the response reports how many were folded). For more
    categories, or to compare magnitudes precisely, use ``feishu_chart_bar`` instead —
    people read bar length far more accurately than slice angle.

    Args:
        labels_json: JSON array of category names, e.g. '["研发","市场","销售"]'.
        values_json: JSON array of numbers, same length/order as labels, e.g. '[42,28,19]'.
        title: Chart title — state the takeaway ("研发占人力一半"), not just the dimension.
        document_id: Target docx document_id (or a wiki node's obj_token). Empty renders
            the PNG only and returns its path.
        unit: Value unit appended to numbers, e.g. "人" / "万元".
        show_values: Also print the raw value under each percentage (default false).
        highlight: 0-based index of the slice to pull out for emphasis; -1 for none.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote, e.g. "HR 系统 2026-07".
        user_key: The sender's open_id (from ``<feishu_context>``); needed when the doc
            is user-owned and the bot isn't a collaborator.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        values = _cr.parse_values(values_json)
        if len(labels) != len(values):
            return _place.fail(f"got {len(labels)} labels but {len(values)} values — they must match.")
        draw, folded = _cr.draw_pie(
            labels,
            values,
            title=title,
            unit=unit,
            show_values=show_values,
            highlight=highlight,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    extra = {"folded_into_other": folded} if folded else None
    return await _place.place(
        draw,
        kind="pie",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
        extra=extra,
    )


async def feishu_chart_donut(
    labels_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    unit: str = "",
    show_values: bool = False,
    highlight: int = -1,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a donut chart to a Feishu doc — shares of a whole, with the total in the middle.

    Same rules as ``feishu_chart_pie`` (2-6 categories, parts of one whole); prefer the
    donut when the **total itself matters** — "1,240 万营收，华东占 42%" — because the hole
    displays that total instead of wasting the centre. Also reads better than a pie at
    small sizes in a dense report.

    Args:
        labels_json: JSON array of category names, e.g. '["华东","华北","华南"]'.
        values_json: JSON array of numbers matching labels, e.g. '[520,310,240]'.
        title: Chart title stating the takeaway.
        document_id: Target docx document_id; empty renders the PNG only.
        unit: Value unit, also applied to the centre total, e.g. "万元".
        show_values: Also print the raw value under each percentage.
        highlight: 0-based slice index to pull out; -1 for none.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        values = _cr.parse_values(values_json)
        if len(labels) != len(values):
            return _place.fail(f"got {len(labels)} labels but {len(values)} values — they must match.")
        draw, folded = _cr.draw_pie(
            labels,
            values,
            title=title,
            donut=True,
            unit=unit,
            show_values=show_values,
            highlight=highlight,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    extra = {"folded_into_other": folded} if folded else None
    return await _place.place(
        draw,
        kind="donut",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
        extra=extra,
    )


async def feishu_chart_funnel(
    stages_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    unit: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a funnel chart to a Feishu doc — stage-by-stage drop-off in a fixed sequence.

    Use for a monotonically shrinking pipeline where **order is meaningful**:
    访问→注册→试用→付费, 投递→面试→录用→入职, 线索→商机→合同. Each stage shows its
    value, its share of the first stage, and its conversion from the previous stage, so
    the weakest link is obvious. Not for unordered categories — that's a bar chart.

    Args:
        stages_json: JSON array of stage names **in sequence**, e.g. '["访问","注册","付费"]'.
        values_json: JSON array of stage values, same order, normally descending.
        title: Chart title stating the takeaway ("注册→试用 是最大流失点").
        document_id: Target docx document_id; empty renders the PNG only.
        unit: Value unit, e.g. "人" / "单".
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        stages = _cr.parse_labels(stages_json, "stages")
        values = _cr.parse_values(values_json)
        draw = _cr.draw_funnel(stages, values, title=title, unit=unit, source=source)
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="funnel",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


# ── Trend over an ordered axis ─────────────────────────────────────────────────


async def feishu_chart_line(
    labels_json: str,
    series_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    zero_baseline: bool = False,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a line chart to a Feishu doc — how values move along an ordered axis.

    The default choice for **time series** (月度营收, 周活跃, 日缺陷数) and for comparing
    2-4 trends on one scale. Each series ends with its final value labelled. The y axis
    fits the data rather than starting at zero, so real movement is visible; pass
    ``zero_baseline=true`` when absolute magnitude matters more than the change.
    For unordered categories use a bar chart — a line implies continuity between points.

    Args:
        labels_json: JSON array of x-axis points **in order**, e.g. '["1月","2月","3月"]'.
        series_json: JSON object of name→values, e.g. '{"2025":[120,132,128],"2026":[140,155,149]}'.
            Each series needs exactly one value per label. A plain array of arrays also
            works and auto-names the series 系列1, 系列2…
        title: Chart title stating the trend ("营收连续三个月上行").
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: X axis label; omit when the labels already say it (月份).
        y_label: Y axis label, e.g. "营收（万元）".
        unit: Unit appended to axis ticks and the end-of-line value labels.
        zero_baseline: Force the y axis to start at 0 (default false).
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        series = _cr.parse_series(series_json)
        draw = _cr.draw_line(
            labels,
            series,
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            zero_baseline=zero_baseline,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="line",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_area(
    labels_json: str,
    series_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append an area chart to a Feishu doc — a trend whose accumulated volume matters.

    A line chart with the region below it filled. Use for **one or two** series where
    the magnitude under the curve carries meaning (累计用户量, 库存水位, 带宽占用); the fill
    always sits on a zero baseline, since a filled area above a truncated axis
    misrepresents volume. Three or more overlapping fills get muddy — use
    ``feishu_chart_line`` for those, or ``feishu_chart_stacked_area`` for composition.

    Args:
        labels_json: JSON array of x-axis points in order.
        series_json: JSON object of name→values (1-2 series recommended).
        title: Chart title stating the takeaway.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: X axis label.
        y_label: Y axis label.
        unit: Unit appended to axis ticks and end labels.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        series = _cr.parse_series(series_json)
        draw = _cr.draw_line(
            labels,
            series,
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            smooth_area=True,
            zero_baseline=True,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="area",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_stacked_area(
    labels_json: str,
    series_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    percent: bool = False,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a stacked area chart to a Feishu doc — composition changing over time.

    Answers "who made up the total, and how did that shift?" — 收入构成按产品线, 工单来源
    按渠道. Absolute stacking shows total growth *and* contribution; ``percent=true``
    normalises every period to 100%, isolating the **mix shift** from total growth. Pick
    based on the question: both from one tool, but they answer different things.
    Values must be non-negative. Keep to ~6 series before the bands get too thin to read.

    Args:
        labels_json: JSON array of x-axis points in order, e.g. '["Q1","Q2","Q3","Q4"]'.
        series_json: JSON object of name→values, one value per label, e.g.
            '{"硬件":[30,32,35,40],"软件":[20,28,34,45]}'.
        title: Chart title stating the shift ("软件收入占比两年翻倍").
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: X axis label.
        y_label: Y axis label; defaults to "占比" when percent is true.
        unit: Unit appended to axis ticks (ignored when percent is true).
        percent: Normalise each period to 100% to show mix rather than volume.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        series = _cr.parse_series(series_json)
        draw = _cr.draw_stacked_area(
            labels,
            series,
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            percent=percent,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="stacked-area",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


# ── Comparison across categories ───────────────────────────────────────────────


async def feishu_chart_column(
    labels_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    sort_desc: bool = False,
    highlight: int = -1,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a vertical column chart to a Feishu doc — compare a value across categories.

    The safest default for comparing magnitudes: bar length is judged far more
    accurately than angle or area. Use for **up to ~8 categories with short names**
    (部门人数, 各月工单量); beyond that, or with long Chinese names, use
    ``feishu_chart_bar`` (horizontal) so labels stay readable. Every bar is labelled
    with its value and starts at zero. ``highlight`` greys the rest to spotlight one
    category — useful when the doc is arguing about that one.

    Args:
        labels_json: JSON array of category names, e.g. '["研发","市场","销售"]'.
        values_json: JSON array of numbers matching labels, e.g. '[42,28,19]'.
        title: Chart title stating the comparison's point.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: X axis label.
        y_label: Y axis label, e.g. "人数".
        unit: Unit appended to bar labels and axis ticks.
        sort_desc: Sort bars by value, largest first — do this for rankings.
        highlight: 0-based index to emphasise (others greyed); -1 for none.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        values = _cr.parse_values(values_json)
        if len(labels) != len(values):
            return _place.fail(f"got {len(labels)} labels but {len(values)} values — they must match.")
        draw = _cr.draw_bar(
            labels,
            [(y_label or "数值", values)],
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            sort_desc=sort_desc,
            highlight=highlight,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="column",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_bar(
    labels_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    sort_desc: bool = True,
    highlight: int = -1,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a horizontal bar chart to a Feishu doc — rankings and long category names.

    Prefer this over ``feishu_chart_column`` whenever there are **many categories
    (8+)** or the names are long Chinese phrases (区域名, 部门全称, 缺陷类型): horizontal
    bars give labels a full line each instead of rotating them into unreadable
    diagonals. Sorted largest-first by default, which is what makes a ranking scannable.

    Args:
        labels_json: JSON array of category names.
        values_json: JSON array of numbers matching labels.
        title: Chart title stating the ranking's point.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: Value axis label.
        y_label: Category axis label (usually omit — the labels speak for themselves).
        unit: Unit appended to bar labels and axis ticks.
        sort_desc: Sort by value, largest at top (default true).
        highlight: 0-based index to emphasise (others greyed); -1 for none.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        values = _cr.parse_values(values_json)
        if len(labels) != len(values):
            return _place.fail(f"got {len(labels)} labels but {len(values)} values — they must match.")
        draw = _cr.draw_bar(
            labels,
            [(x_label or "数值", values)],
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            horizontal=True,
            sort_desc=sort_desc,
            highlight=highlight,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="bar",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_grouped_column(
    labels_json: str,
    series_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    horizontal: bool = False,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a grouped (clustered) column chart — compare 2-4 series side by side.

    Use when each category needs **several values compared directly**: 计划 vs 实际,
    去年 vs 今年, 三个区域各自的四个季度. Bars sit adjacent so within-category
    comparison is exact — that's the difference from stacked, which compares totals
    instead. Keep to ~4 series and ~8 categories; past that the bars get too thin.

    Args:
        labels_json: JSON array of category names, e.g. '["Q1","Q2","Q3"]'.
        series_json: JSON object of name→values, one per label, e.g.
            '{"计划":[100,120,140],"实际":[95,130,128]}'.
        title: Chart title stating the comparison's conclusion.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: Category axis label.
        y_label: Value axis label.
        unit: Unit appended to bar labels and axis ticks.
        horizontal: Draw horizontally for long category names.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        series = _cr.parse_series(series_json)
        draw = _cr.draw_bar(
            labels,
            series,
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            horizontal=horizontal,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="grouped-column",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_stacked_column(
    labels_json: str,
    series_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    percent: bool = False,
    horizontal: bool = False,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a stacked column chart — category totals **and** their internal composition.

    Use when both the total per category and its breakdown matter: 各季度收入按产品线,
    各部门人数按职级. ``percent=true`` makes every column 100% tall, which compares
    **mix** across categories of very different sizes (that's the right choice when
    "大区之间结构差异" is the question, not "谁的总量大"). To compare individual components
    precisely, use ``feishu_chart_grouped_column`` — stacked segments don't share a
    baseline, so only the bottom one is easy to read. Values must be non-negative.

    Args:
        labels_json: JSON array of category names.
        series_json: JSON object of name→values, one per label.
        title: Chart title stating the takeaway.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: Category axis label.
        y_label: Value axis label.
        unit: Unit appended to axis ticks (ignored when percent is true).
        percent: Normalise each column to 100% to compare composition.
        horizontal: Draw horizontally for long category names.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        series = _cr.parse_series(series_json)
        draw = _cr.draw_bar(
            labels,
            series,
            title=title,
            x_label=x_label,
            y_label=y_label,
            unit=unit,
            stacked=True,
            percent=percent,
            horizontal=horizontal,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="stacked-column",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_waterfall(
    labels_json: str,
    deltas_json: str,
    title: str = "",
    document_id: str = "",
    y_label: str = "",
    unit: str = "",
    total_label: str = "合计",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a waterfall (bridge) chart — how a starting value becomes an ending value.

    The finance/ops explanation chart: 期初 ARR → 新签 → 续费 → 流失 → 期末, 预算差异归因,
    人力增减. Increases render green, decreases red, and the running total closes in blue,
    so the contribution of each step is legible without a legend. Pass the **changes**,
    not the running balances — the chart computes the cumulative line itself.

    Args:
        labels_json: JSON array of step names in order, e.g. '["期初","新签","续费","流失"]'.
        deltas_json: JSON array of signed changes, e.g. '[500,220,160,-90]'. The first
            value is normally the opening balance; negatives are drops.
        title: Chart title stating the net story.
        document_id: Target docx document_id; empty renders the PNG only.
        y_label: Value axis label.
        unit: Unit appended to labels and axis ticks, e.g. "万".
        total_label: Name of the final closing bar (default "合计").
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        deltas = _cr.parse_values(deltas_json, "deltas")
        draw = _cr.draw_waterfall(
            labels, deltas, title=title, y_label=y_label, unit=unit, total_label=total_label, source=source
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="waterfall",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


# ── Distribution & correlation ─────────────────────────────────────────────────


async def feishu_chart_histogram(
    values_json: str,
    title: str = "",
    document_id: str = "",
    bins: int = 0,
    x_label: str = "",
    y_label: str = "频数",
    unit: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a histogram — the **shape** of one variable's distribution.

    Use when the spread matters more than any single number: 工单处理时长分布, 薪酬分布,
    接口响应耗时. This is the chart that exposes what an average hides — a long tail, two
    clusters, a wall at a SLA boundary. Mean and median lines are drawn so skew is
    visible. Pass the **raw observations**, not pre-counted buckets; to compare
    distributions across groups use ``feishu_chart_box``.

    Args:
        values_json: JSON array of raw numeric observations (at least 2), e.g. '[3,4,4,5,7,12]'.
        title: Chart title stating what the shape shows.
        document_id: Target docx document_id; empty renders the PNG only.
        bins: Number of buckets; 0 auto-picks from the sample size (recommended).
        x_label: Measured-variable label, e.g. "处理时长（小时）".
        y_label: Count axis label (default "频数").
        unit: Unit appended to axis ticks and the mean/median labels.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        values = _cr.parse_values(values_json)
        draw = _cr.draw_histogram(
            values, bins=bins, title=title, x_label=x_label, y_label=y_label, unit=unit, source=source
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="histogram",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_box(
    groups_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    unit: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a box plot — compare **distributions** across groups, not just their averages.

    Use when "平均值差不多" would be misleading: 各部门响应时长, 各产线良率, 各校招批次评分.
    Each box shows median, quartiles, whiskers and outliers, so a group that looks
    average but is wildly inconsistent stands out. Every group needs at least 2
    observations; for a single group's shape use ``feishu_chart_histogram``.

    Args:
        groups_json: JSON object of group→raw observations, e.g.
            '{"研发":[3,4,5,5,6,12],"市场":[2,3,3,4,4,5]}'. Groups may differ in length.
        title: Chart title stating the comparison's point.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: Group axis label.
        y_label: Measured-variable label, e.g. "响应时长（小时）".
        unit: Unit appended to axis ticks.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        parsed = _cr.parse_series(groups_json, "groups")
        draw = _cr.draw_box(parsed, title=title, x_label=x_label, y_label=y_label, unit=unit, source=source)
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="box",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_scatter(
    points_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    trend: bool = True,
    point_labels_json: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a scatter plot — does one variable move with another?

    Use to test or show a **relationship** between two measures: 门店面积 vs 月销售额,
    投入工时 vs 缺陷修复数, 折扣率 vs 成交周期. A dashed least-squares line is drawn by
    default, which is what turns a dot cloud into a claim. Pass multiple groups to
    compare relationships across segments. This is about correlation, not composition
    or ranking — for those use pie/bar.

    Args:
        points_json: EITHER a JSON array of [x,y] pairs, e.g. '[[10,22],[15,30],[20,33]]',
            OR a JSON object of group→pairs for multiple series, e.g.
            '{"直营":[[10,22],[15,30]],"加盟":[[12,18],[18,26]]}'.
        title: Chart title stating the relationship ("面积与销售额正相关").
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: X variable label **with its unit** — a scatter is unreadable without it.
        y_label: Y variable label with its unit.
        trend: Overlay a least-squares trend line (default true).
        point_labels_json: Optional JSON array naming each point (single-group only),
            e.g. '["A店","B店","C店"]'. Only use for small sets, or labels overlap.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        groups = _cr.parse_point_groups(points_json)
        point_labels = _cr.parse_labels(point_labels_json, "point_labels") if point_labels_json.strip() else None
        draw = _cr.draw_scatter(
            groups,
            title=title,
            x_label=x_label,
            y_label=y_label,
            trend=trend,
            point_labels=point_labels,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="scatter",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_bubble(
    points_json: str,
    title: str = "",
    document_id: str = "",
    x_label: str = "",
    y_label: str = "",
    size_label: str = "",
    labels_json: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a bubble chart — three variables at once (x, y, and bubble size).

    Use for portfolio/quadrant analysis where a third magnitude matters: 增速 vs 利润率
    vs 营收规模, 优先级 vs 工作量 vs 影响用户数. Bubble **area** scales with the third value
    (area, not radius — that's what the eye actually judges). Keep to ~12 bubbles; more
    and they overlap into noise. If the third variable isn't important, use
    ``feishu_chart_scatter``.

    Args:
        points_json: JSON array of [x,y,size] triples, e.g. '[[10,22,300],[15,30,800]]'.
        title: Chart title stating the takeaway.
        document_id: Target docx document_id; empty renders the PNG only.
        x_label: X variable label with unit.
        y_label: Y variable label with unit.
        size_label: What bubble size means — shown as a footnote so the reader isn't guessing.
        labels_json: Optional JSON array naming each bubble, e.g. '["A","B","C"]'.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        points = _cr.parse_points(points_json, dims=3)
        labels = _cr.parse_labels(labels_json) if labels_json.strip() else None
        if labels and len(labels) != len(points):
            return _place.fail(f"got {len(labels)} labels but {len(points)} bubbles — they must match.")
        draw = _cr.draw_bubble(
            points,
            labels=labels,
            title=title,
            x_label=x_label,
            y_label=y_label,
            size_label=size_label,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="bubble",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_heatmap(
    row_labels_json: str,
    col_labels_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    unit: str = "",
    color_label: str = "",
    show_values: bool = True,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a heatmap — intensity across a two-dimensional grid.

    Use when the pattern lives in a **crossing of two dimensions**: 星期×时段的咨询量,
    区域×产品的销量, 人员×技能的熟练度. Reveals hot spots and gaps that a table of the same
    numbers buries. Cell values are printed on top by default, so it doubles as a
    readable table. For a single dimension use a bar chart instead.

    Args:
        row_labels_json: JSON array of row names, e.g. '["周一","周二","周三"]'.
        col_labels_json: JSON array of column names, e.g. '["9点","12点","15点"]'.
        values_json: JSON 2-D array shaped rows x columns, e.g. '[[12,45,38],[15,48,41]]'.
        title: Chart title stating the pattern ("咨询量集中在午间与工作日").
        document_id: Target docx document_id; empty renders the PNG only.
        unit: Unit appended to cell values.
        color_label: Colourbar label — what the intensity measures.
        show_values: Print each cell's number (default true; auto-skipped above 120 cells).
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        rows = _cr.parse_labels(row_labels_json, "row_labels")
        cols = _cr.parse_labels(col_labels_json, "col_labels")
        matrix = _cr.parse_matrix(values_json, len(rows), len(cols))
        draw = _cr.draw_heatmap(
            rows,
            cols,
            matrix,
            title=title,
            unit=unit,
            show_values=show_values,
            color_label=color_label,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="heatmap",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


# ── Purpose-built ──────────────────────────────────────────────────────────────


async def feishu_chart_radar(
    axes_json: str,
    series_json: str,
    title: str = "",
    document_id: str = "",
    max_value: float = 0,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a radar (spider) chart — a multi-dimension profile at a glance.

    Use for **capability/competency profiles on a shared scale**: 员工能力评估（技术/沟通/
    交付/协作）, 供应商多维打分, 产品与竞品对比. The value is the *shape* — where someone is
    strong and where they dip — and comparing 1-3 overlaid profiles. Requires 3-8 axes
    that share a comparable scale (all 1-5, all percentages); with mixed units or many
    axes it distorts, so use a bar chart there.

    Args:
        axes_json: JSON array of 3-8 dimension names, e.g. '["技术","沟通","交付","协作"]'.
        series_json: JSON object of name→scores, one score per axis, e.g.
            '{"张三":[4.5,3.8,4.2,4.0],"团队均值":[3.8,3.9,3.6,4.1]}'.
        title: Chart title stating the profile's point.
        document_id: Target docx document_id; empty renders the PNG only.
        max_value: Scale ceiling, e.g. 5 for a 1-5 rating; 0 derives it from the data.
            Set this explicitly for ratings so the shape isn't exaggerated by autoscaling.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        axes_labels = _cr.parse_labels(axes_json, "axes")
        series = _cr.parse_series(series_json)
        draw = _cr.draw_radar(axes_labels, series, title=title, max_value=max_value, source=source)
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="radar",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_pareto(
    labels_json: str,
    values_json: str,
    title: str = "",
    document_id: str = "",
    y_label: str = "",
    unit: str = "",
    threshold: float = 80.0,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a Pareto chart — which few causes drive most of the effect (80/20).

    Use for **root-cause prioritisation**: 缺陷类型分布, 客诉原因, 成本构成, 卡点归因. Bars
    are ranked descending with a cumulative-percentage line; everything up to the
    threshold stays coloured and the long tail greys out, so "fix these three and you've
    covered 80%" is visible rather than argued. This is the chart to use when a doc needs
    to justify what to work on first.

    Args:
        labels_json: JSON array of cause/category names.
        values_json: JSON array of non-negative magnitudes matching labels.
        title: Chart title stating the vital few.
        document_id: Target docx document_id; empty renders the PNG only.
        y_label: Value axis label, e.g. "工单数".
        unit: Unit appended to axis ticks.
        threshold: Cumulative-percentage cut to mark (default 80).
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        values = _cr.parse_values(values_json)
        draw = _cr.draw_pareto(
            labels, values, title=title, y_label=y_label, unit=unit, threshold=threshold, source=source
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="pareto",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_combo(
    labels_json: str,
    bar_series_json: str,
    line_series_json: str,
    title: str = "",
    document_id: str = "",
    y_label: str = "",
    y2_label: str = "",
    unit: str = "",
    line_unit: str = "",
    line_percent: bool = False,
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a combo chart — volume as bars plus a rate as a line on a second axis.

    The standard management-report chart, for when **two different units** belong
    together: 营收(万) + 毛利率(%), 招聘人数 + 到岁率, 工单量 + 一次解决率. Bars read on the
    left axis, lines on the right. Use this instead of forcing a percentage onto the same
    axis as a large absolute number, where the rate would flatten into an invisible line.

    Args:
        labels_json: JSON array of x-axis categories in order, e.g. '["1月","2月","3月"]'.
        bar_series_json: JSON object of name→values for the bars (left axis), e.g.
            '{"营收":[120,145,138]}'.
        line_series_json: JSON object of name→values for the lines (right axis), e.g.
            '{"毛利率":[32,35,33]}'.
        title: Chart title tying the two together ("营收上行但毛利率承压").
        document_id: Target docx document_id; empty renders the PNG only.
        y_label: Left (bar) axis label with unit.
        y2_label: Right (line) axis label with unit.
        unit: Unit for the bar axis ticks, e.g. "万".
        line_unit: Unit for the line axis ticks (ignored when line_percent is true).
        line_percent: Format the right axis as percentages (default false).
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        labels = _cr.parse_labels(labels_json)
        bars = _cr.parse_series(bar_series_json, "bar_series")
        lines = _cr.parse_series(line_series_json, "line_series")
        draw = _cr.draw_combo(
            labels,
            bars,
            lines,
            title=title,
            y_label=y_label,
            y2_label=y2_label,
            unit=unit,
            line_unit=line_unit,
            line_percent=line_percent,
            source=source,
        )
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="combo",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_gantt(
    tasks_json: str,
    title: str = "",
    document_id: str = "",
    start_date: str = "",
    today: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append a Gantt chart — project schedule as task bars along a time axis.

    Use for **plans and schedules**: 项目排期, 迭代计划, 上线窗口, 交接时间表. Tasks sharing a
    ``group`` (owner/phase) share a colour, and a red "今天" line marks the current date
    so slippage is visible. Accepts real dates (YYYY-MM-DD) and converts them to a day
    axis. For sequential process *steps* without dates, use ``feishu_doc_append_flowchart``.

    Args:
        tasks_json: JSON array of task objects with ``name``, ``start``, ``end`` (or
            ``days``), and optional ``group``, e.g.
            '[{"name":"需求评审","start":"2026-08-01","end":"2026-08-04","group":"产品"},
              {"name":"开发","start":"2026-08-05","days":10,"group":"研发"}]'.
            Dates are YYYY-MM-DD; ``end`` is inclusive.
        title: Chart title.
        document_id: Target docx document_id; empty renders the PNG only.
        start_date: Optional YYYY-MM-DD for day 0 of the axis; defaults to the earliest task.
        today: Optional YYYY-MM-DD to draw the "今天" line; empty draws no line.
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        tasks, tick_labels, today_offset = _cr.parse_gantt_tasks(tasks_json, start_date, today)
        draw = _cr.draw_gantt(tasks, title=title, tick_labels=tick_labels, today=today_offset, source=source)
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="gantt",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


async def feishu_chart_progress(
    items_json: str,
    title: str = "",
    document_id: str = "",
    target: float = 100.0,
    unit: str = "%",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append progress/attainment bars — actual against a target, per item.

    Use for **completion and attainment tracking**: OKR 进度, 各区域目标完成率, 培训覆盖率,
    整改闭环率. Each row shows the target as a track with the achieved portion filled;
    rows that clear the target turn green and overshoot extends past the track, while
    shortfalls spell out the remaining gap. Clearer than a bar chart here because the
    target is drawn, not implied.

    Args:
        items_json: JSON object of item→achieved value, e.g. '{"华东":118,"华北":92,"华南":76}'.
        title: Chart title stating overall status.
        document_id: Target docx document_id; empty renders the PNG only.
        target: The goal every item is measured against (default 100).
        unit: Unit for the values, e.g. "%" / "万" / "人" (default "%").
        caption: Caption text WITHOUT a number — write "人力分布", not "图1：人力分布".
            The "图 N" prefix is added automatically, continuing the document's sequence.
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        source: Data provenance footnote.
        user_key: The sender's open_id; needed for user-owned docs.
        identity: ``"user"`` / ``"bot"`` -- who owns the chart's document (see feishu_doc_create).
    """
    try:
        items = _cr.parse_pairs(items_json, "items")
        draw = _cr.draw_progress(items, title=title, target=target, unit=unit, source=source)
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place(
        draw,
        kind="progress",
        title=title,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )


# ── Combined figures ───────────────────────────────────────────────────────────


async def feishu_chart_figure(
    panels_json: str,
    layout: str = "horizontal",
    figure_title: str = "",
    document_id: str = "",
    caption: str = "",
    source: str = "",
    auto_number: bool = True,
    panel_sources: bool = False,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Append 2-6 related charts as ONE figure — panels side by side, under one caption.

    Use this when several views answer **one** question and belong together: 营收趋势 +
    渠道占比 + 区域排名 as "上半年经营概览", or 本期 vs 上期 of the same metric. The panels
    render into a single image, each tagged ``(a)`` ``(b)`` ``(c)``, with one numbered
    caption below naming them — the layout academic papers use for multi-part figures.
    Because it is one image block, the panels can never drift apart from each other or
    from their caption the way separately-inserted charts do.

    Use the single-chart tools instead when the charts answer *different* questions —
    unrelated charts crammed into one figure share a caption that can't describe either.

    Args:
        panels_json: JSON array of 2-6 panel objects. Each needs ``chart`` (one of: pie,
            donut, funnel, line, area, stacked_area, column, bar, grouped_column,
            stacked_column, waterfall, histogram, box, scatter, bubble, heatmap, radar,
            pareto, combo, gantt, progress) plus that chart's data fields, named like the
            single-chart tool's arguments without the ``_json`` suffix — ``labels``,
            ``values``, ``series``, ``points``, ``tasks``, ``items``… Each panel also takes
            its own ``title`` (shown above that panel and reused in the caption), and the
            same optional knobs as its tool (``unit``, ``x_label``, ``percent``, …). E.g.
            '[{"chart":"line","title":"营收趋势","labels":["1月","2月"],"series":{"营收":[120,145]}},
              {"chart":"pie","title":"渠道占比","labels":["直销","线上"],"values":[62,38]}]'
        layout: ``"horizontal"`` (one row, for comparing panels), ``"vertical"`` (one
            column, for a sequence), or ``"grid"`` (near-square; use for 4+ panels).
        figure_title: Optional title for the whole figure, stating the combined takeaway.
        document_id: Target docx document_id (or a wiki node's obj_token). Empty renders
            the PNG only and returns its path.
        caption: Caption text WITHOUT a number — write "各区域经营概况", not "图3：…". The
            "图 N" prefix is added automatically, continuing the document's own sequence,
            and the panel names are appended as "(a) …；(b) …".
        source: Data provenance footnote for the whole figure, e.g. "财务台账 2026-07".
        auto_number: Number the caption from the document's existing 图 captions
            (default true). Set false only when the caller manages numbering itself.
        panel_sources: Also print each panel's own ``source`` under that panel (default
            false — one shared ``source`` for the figure is usually right, and repeating
            it per panel is noise). Use when panels genuinely come from different systems.
        user_key: The sender's open_id (from ``<feishu_context>``); needed when the doc
            is user-owned and the bot isn't a collaborator.
        identity: ``"user"`` / ``"bot"`` -- who owns the figure's document (see feishu_doc_create).
    """
    try:
        draws, panel_titles = _cr.parse_panels(panels_json, panel_source=panel_sources)
    except _cr.ChartDataError as exc:
        return _place.fail(str(exc))
    return await _place.place_figure(
        draws,
        panel_titles=panel_titles,
        layout=layout,
        figure_title=figure_title,
        source=source,
        document_id=document_id,
        caption=caption,
        auto_number=auto_number,
        user_key=user_key,
        identity=identity,
    )
