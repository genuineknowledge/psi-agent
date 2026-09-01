"""Tests for the Feishu chart tools — parsing, rendering, and doc placement.

Renders are real (matplotlib to a temp PNG) since the whole value of these tools is
that a legible file comes out; the Feishu API calls are faked, so nothing here needs
credentials or network.
"""

from __future__ import annotations

import importlib
import io
import itertools
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import matplotlib
import numpy as np
import pytest
from lark_channel.core.enum import AccessTokenType
from PIL import Image

# Before pyplot is imported: these tests render off-screen, and importing pyplot first
# would bind whatever interactive backend the host happens to have.
matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
from matplotlib.text import Text

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_cr: Any = importlib.import_module("_chart_render")
_place: Any = importlib.import_module("_chart_place")
_impl: Any = importlib.import_module("_feishu_impl")
_chart: Any = importlib.import_module("feishu_chart")
_doc: Any = importlib.import_module("feishu_doc")
_cap: Any = importlib.import_module("_chart_caption")


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point chart output at a temp dir so tests never write into the real workspace."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))


# ── Input parsing ──────────────────────────────────────────────────────────────


def test_parse_values_accepts_model_number_formats() -> None:
    assert _cr.parse_values('[1234, "1,234", "85%", "￥1200", 3.5]') == [1234.0, 1234.0, 85.0, 1200.0, 3.5]


def test_parse_values_rejects_non_numeric() -> None:
    with pytest.raises(_cr.ChartDataError, match="must be a number"):
        _cr.parse_values('["abc"]')


def test_parse_values_rejects_bool() -> None:
    with pytest.raises(_cr.ChartDataError, match="boolean"):
        _cr.parse_values("[true]")


def test_parse_labels_rejects_empty() -> None:
    with pytest.raises(_cr.ChartDataError, match="non-empty"):
        _cr.parse_labels("[]")


def test_parse_series_object_preserves_names_and_order() -> None:
    series = _cr.parse_series('{"2025":[1,2],"2026":[3,4]}')
    assert [name for name, _v in series] == ["2025", "2026"]
    assert series[0][1] == [1.0, 2.0]


def test_parse_series_array_autonames() -> None:
    series = _cr.parse_series("[[1,2],[3,4]]")
    assert [name for name, _v in series] == ["系列1", "系列2"]


def test_check_series_length_reports_mismatch() -> None:
    with pytest.raises(_cr.ChartDataError, match="one value per label"):
        _cr.check_series_length([("A", [1.0, 2.0, 3.0])], ["x", "y"])


def test_parse_matrix_validates_shape() -> None:
    with pytest.raises(_cr.ChartDataError, match="2 row labels"):
        _cr.parse_matrix("[[1,2]]", 2, 2)


def test_parse_matrix_validates_row_width() -> None:
    with pytest.raises(_cr.ChartDataError, match="3 column labels"):
        _cr.parse_matrix("[[1,2],[3,4]]", 2, 3)


def test_parse_pairs_keeps_insertion_order() -> None:
    assert _cr.parse_pairs('{"华东":118,"华北":92}') == [("华东", 118.0), ("华北", 92.0)]


def test_parse_pairs_rejects_array() -> None:
    with pytest.raises(_cr.ChartDataError, match="JSON object"):
        _cr.parse_pairs("[1,2]")


def test_parse_point_groups_single_and_named() -> None:
    single = _cr.parse_point_groups("[[1,2],[3,4]]")
    assert single == [("", [[1.0, 2.0], [3.0, 4.0]])]
    named = _cr.parse_point_groups('{"直营":[[1,2]],"加盟":[[3,4]]}')
    assert [name for name, _p in named] == ["直营", "加盟"]


def test_parse_points_requires_enough_dimensions() -> None:
    with pytest.raises(_cr.ChartDataError, match="at least 3 numbers"):
        _cr.parse_points("[[1,2]]", dims=3)


# ── Gantt date handling ────────────────────────────────────────────────────────


def test_parse_gantt_tasks_converts_dates_to_day_offsets() -> None:
    raw = json.dumps(
        [
            {"name": "评审", "start": "2026-08-01", "end": "2026-08-04", "group": "产品"},
            {"name": "开发", "start": "2026-08-05", "days": 10, "group": "研发"},
        ],
        ensure_ascii=False,
    )
    tasks, ticks, today = _parse_gantt(raw, today="2026-08-06")
    # end is inclusive: 08-01..08-04 is four days, not three
    assert tasks[0] == ("评审", 0.0, 4.0, "产品")
    assert tasks[1] == ("开发", 4.0, 10.0, "研发")
    assert ticks[0] == "08-01"
    assert today == 5.0


def _parse_gantt(raw: str, *, start: str = "", today: str = "") -> tuple[Any, Any, Any]:
    return _cr.parse_gantt_tasks(raw, start, today)


def test_parse_gantt_tasks_requires_end_or_days() -> None:
    with pytest.raises(_cr.ChartDataError, match='needs either an "end" date or "days"'):
        _cr.parse_gantt_tasks('[{"name":"a","start":"2026-08-01"}]', "", "")


def test_parse_gantt_tasks_rejects_reversed_range() -> None:
    with pytest.raises(_cr.ChartDataError, match="ends before it starts"):
        _cr.parse_gantt_tasks('[{"name":"a","start":"2026-08-05","end":"2026-08-01"}]', "", "")


def test_parse_gantt_tasks_rejects_bad_date() -> None:
    with pytest.raises(_cr.ChartDataError, match="not a valid date"):
        _cr.parse_gantt_tasks('[{"name":"a","start":"2026-13-40","days":2}]', "", "")


def test_parse_gantt_tasks_honours_explicit_origin() -> None:
    tasks, ticks, _today = _cr.parse_gantt_tasks('[{"name":"a","start":"2026-08-05","days":2}]', "2026-08-01", "")
    assert tasks[0][1] == 4.0
    assert ticks[0] == "08-01"


# ── Chart-type guardrails: refuse to draw a misleading chart ───────────────────


def test_pie_rejects_negative_values() -> None:
    with pytest.raises(_cr.ChartDataError, match="negative"):
        _cr.draw_pie(["a", "b"], [-1.0, 2.0])


def test_pie_rejects_zero_total() -> None:
    with pytest.raises(_cr.ChartDataError, match="sum to 0"):
        _cr.draw_pie(["a", "b"], [0.0, 0.0])


def test_pie_folds_tail_into_other() -> None:
    labels = [f"c{i}" for i in range(10)]
    values = [float(i + 1) for i in range(10)]
    _draw, folded = _cr.draw_pie(labels, values)
    assert folded == 4  # 10 categories, 6 kept


def test_pie_keeps_small_sets_unfolded() -> None:
    _draw, folded = _cr.draw_pie(["a", "b", "c"], [3.0, 2.0, 1.0])
    assert folded == 0


def test_stacked_area_rejects_negatives() -> None:
    with pytest.raises(_cr.ChartDataError, match="negative"):
        _cr.draw_stacked_area(["q1"], [("a", [-1.0])])


def test_stacked_bar_rejects_negatives() -> None:
    with pytest.raises(_cr.ChartDataError, match="negative"):
        _cr.draw_bar(["q1"], [("a", [-1.0])], stacked=True)


def test_percent_requires_stacked() -> None:
    with pytest.raises(_cr.ChartDataError, match="only applies to stacked"):
        _cr.draw_bar(["q1"], [("a", [1.0])], percent=True)


def test_radar_requires_three_axes() -> None:
    with pytest.raises(_cr.ChartDataError, match="at least 3 axes"):
        _cr.draw_radar(["a", "b"], [("x", [1.0, 2.0])])


def test_funnel_requires_positive_first_stage() -> None:
    with pytest.raises(_cr.ChartDataError, match="100% baseline"):
        _cr.draw_funnel(["a", "b"], [0.0, 0.0])


def test_histogram_requires_two_values() -> None:
    with pytest.raises(_cr.ChartDataError, match="at least 2 values"):
        _cr.draw_histogram([1.0])


def test_box_requires_two_observations_per_group() -> None:
    with pytest.raises(_cr.ChartDataError, match="at least 2 values"):
        _cr.draw_box([("a", [1.0])])


def test_combo_requires_both_kinds_of_series() -> None:
    with pytest.raises(_cr.ChartDataError, match="at least one bar series"):
        _cr.draw_combo(["q1"], [], [("rate", [1.0])])


# ── Formatting helpers ─────────────────────────────────────────────────────────


def test_fmt_number_thousands_and_unit() -> None:
    assert _cr._fmt_number(12480.0, "万") == "12,480万"


def test_fmt_number_picks_precision_by_magnitude() -> None:
    assert _cr._fmt_number(0.853) == "0.85"
    assert _cr._fmt_number(4.5) == "4.5"
    assert _cr._fmt_number(120.0) == "120"


def test_linear_fit_recovers_known_slope() -> None:
    slope, intercept = _cr._linear_fit([1.0, 2.0, 3.0], [3.0, 5.0, 7.0])
    assert round(slope, 6) == 2.0
    assert round(intercept, 6) == 1.0


def test_linear_fit_handles_zero_variance() -> None:
    slope, intercept = _cr._linear_fit([2.0, 2.0], [1.0, 3.0])
    assert slope == 0.0
    assert intercept == 2.0


def test_fold_tail_sorts_descending() -> None:
    labels, values, folded = _cr._fold_tail(["a", "b", "c"], [1.0, 3.0, 2.0], 6)
    assert labels == ["b", "c", "a"]
    assert values == [3.0, 2.0, 1.0]
    assert folded == 0


# ── Real rendering: a PNG that actually comes out ──────────────────────────────

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_render_to_png_writes_a_real_png(tmp_path: Path) -> None:
    draw, _folded = _cr.draw_pie(["研发", "市场"], [3.0, 1.0], title="中文标题")
    out = tmp_path / "nested" / "chart.png"
    path = await _cr.render_to_png(draw, str(out))
    data = await anyio.Path(path).read_bytes()
    assert data.startswith(_PNG_MAGIC)  # a real PNG, parent dirs created
    assert len(data) > 5000  # not a blank canvas


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "kwargs"),
    [
        (lambda: _chart._render_pie, ('["研发","市场"]', "[3,1]"), {"unit": "人"}),
        (lambda: _chart._render_donut, ('["华东","华北"]', "[520,310]"), {"unit": "万"}),
        (lambda: _chart._render_funnel, ('["访问","付费"]', "[100,20]"), {}),
        (lambda: _chart._render_line, ('["1月","2月"]', '{"A":[1,2]}'), {}),
        (lambda: _chart._render_area, ('["1月","2月"]', '{"A":[1,2]}'), {}),
        (lambda: _chart._render_stacked_area, ('["Q1","Q2"]', '{"a":[1,2],"b":[2,3]}'), {"percent": True}),
        (lambda: _chart._render_column, ('["研发","市场"]', "[42,28]"), {"highlight": 0}),
        (lambda: _chart._render_bar, ('["华东区域","华北区域"]', "[520,310]"), {}),
        (lambda: _chart._render_grouped_column, ('["Q1","Q2"]', '{"计划":[1,2],"实际":[2,3]}'), {}),
        (lambda: _chart._render_stacked_column, ('["Q1","Q2"]', '{"a":[1,2],"b":[2,3]}'), {}),
        (lambda: _chart._render_waterfall, ('["期初","新签","流失"]', "[500,220,-90]"), {}),
        (lambda: _chart._render_histogram, ("[1,2,2,3,4,5,6]",), {}),
        (lambda: _chart._render_box, ('{"研发":[1,2,3],"市场":[2,3,4]}',), {}),
        (lambda: _chart._render_scatter, ("[[1,2],[3,4],[5,7]]",), {}),
        (lambda: _chart._render_bubble, ("[[1,2,10],[3,4,20]]",), {"size_label": "规模"}),
        (lambda: _chart._render_heatmap, ('["周一","周二"]', '["上午","下午"]', "[[1,2],[3,4]]"), {}),
        (lambda: _chart._render_radar, ('["技术","沟通","交付"]', '{"张三":[4,3,5]}'), {"max_value": 5}),
        (lambda: _chart._render_pareto, ('["A","B","C"]', "[120,85,10]"), {}),
        (lambda: _chart._render_combo, ('["1月","2月"]', '{"营收":[1,2]}', '{"毛利率":[30,35]}'), {}),
        (
            lambda: _chart._render_gantt,
            ('[{"name":"开发","start":"2026-08-01","days":5,"group":"研发"}]',),
            {"today": "2026-08-03"},
        ),
        (lambda: _chart._render_progress, ('{"华东":118,"华北":92}',), {"target": 100}),
    ],
)
async def test_every_chart_tool_renders(tool: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    """Each tool, called with no document_id, must produce a real PNG on disk."""
    result = json.loads(await tool()(*args, **kwargs))
    assert result["ok"] is True, result.get("message")
    data = await anyio.Path(result["image_path"]).read_bytes()
    assert data.startswith(_PNG_MAGIC)
    assert "no document_id" in result["note"]


@pytest.mark.asyncio
async def test_tool_reports_data_error_as_result_not_exception() -> None:
    result = json.loads(await _chart._render_pie('["a","b"]', "[1,2,3]"))
    assert result["ok"] is False
    assert "2 labels but 3 values" in result["message"]


@pytest.mark.asyncio
async def test_scatter_accepts_grouped_input() -> None:
    result = json.loads(await _chart._render_scatter('{"直营":[[1,2],[3,4]],"加盟":[[2,1],[4,3]]}'))
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_bubble_rejects_label_count_mismatch() -> None:
    result = json.loads(await _chart._render_bubble("[[1,2,3]]", labels_json='["a","b"]'))
    assert result["ok"] is False
    assert "2 labels but 1 bubbles" in result["message"]


@pytest.mark.asyncio
async def test_chart_filename_includes_type_and_title() -> None:
    result = json.loads(await _chart._render_pie('["a"]', "[1]", title="人力占比"))
    name = Path(result["image_path"]).name
    assert name.startswith("pie-")
    assert "人力占比" in name
    assert name.endswith(".png")


# ── Layout: readable at the size Feishu will show it ───────────────────────────
#
# Feishu displays an image block at the PNG's intrinsic pixel size — verified against
# the live API: `replace_image` overwrites whatever width/height we send with the
# file's real dimensions, and every follow-up patch shape is rejected with 1770001.
# So the PNG's pixel size *is* the in-document display size, and any text that
# overlaps in the PNG overlaps in the doc. Both are checked by measurement here.


def _label_set(kind: str, n: int) -> list[str]:
    """Category labels of a given shape — the axis-crowding variable that actually bites."""
    if kind == "short":
        return [f"{i}月" for i in range(1, n + 1)]
    if kind == "date":
        return [f"2026-08-{i % 28 + 1:02d}" for i in range(n)]
    return [f"第{i}季度华东大区渠道{i}" for i in range(1, n + 1)]  # long


def _chart_matrix() -> dict[str, Any]:
    """Every chart type crossed with the conditions that produce layout defects.

    Rather than a handful of datasets chosen by hand, this sweeps the three variables
    the reported bugs came from — how many categories, how long their labels are, and
    how many series compete with the title — so a chart type can't pass by happening to
    suit the one example someone picked. Counts bracket the range the tools accept:
    2 (degenerate), 12 (a year), 31 (a month of days).
    """
    cases: dict[str, Any] = {}
    for n in (2, 12, 31):
        for label_kind in ("short", "date", "long"):
            labels = _label_set(label_kind, n)
            values = [float(10 + (i * 7) % 53) for i in range(n)]
            tag = f"{n}x{label_kind}"
            one = [("实际", values)]
            many = [(f"产品线{s}", [v + s * 3 for v in values]) for s in range(1, 6)]
            cases[f"line-{tag}"] = _cr.draw_line(labels, one, title=f"趋势 {tag}", y_label="营收")
            cases[f"line-multi-{tag}"] = _cr.draw_line(labels, many, title=f"多系列趋势 {tag}")
            cases[f"area-{tag}"] = _cr.draw_stacked_area(labels, many, title=f"构成变化 {tag}")
            cases[f"column-{tag}"] = _cr.draw_bar(labels, one, title=f"对比 {tag}", y_label="人数")
            cases[f"grouped-{tag}"] = _cr.draw_bar(labels, many, title=f"分组对比 {tag}")
            cases[f"stacked-{tag}"] = _cr.draw_bar(labels, many, title=f"堆叠构成 {tag}", stacked=True)
            cases[f"bar-{tag}"] = _cr.draw_bar(labels, one, title=f"横向对比 {tag}", horizontal=True)
            cases[f"combo-{tag}"] = _cr.draw_combo(
                labels, one, [("毛利率", [40.0 + i for i in range(n)])], title=f"营收与毛利率 {tag}"
            )
            cases[f"pareto-{tag}"] = _cr.draw_pareto(labels, sorted(values, reverse=True), title=f"帕累托 {tag}")
            cases[f"waterfall-{tag}"] = _cr.draw_waterfall(
                labels, [v if i % 3 else -v for i, v in enumerate(values)], title=f"瀑布 {tag}"
            )
            cases[f"pie-{tag}"] = _cr.draw_pie(labels, values, title=f"占比 {tag}", unit="人")[0]
            cases[f"donut-{tag}"] = _cr.draw_pie(labels, values, title=f"环形占比 {tag}", donut=True)[0]
            cases[f"funnel-{tag}"] = _cr.draw_funnel(labels, sorted(values, reverse=True), title=f"漏斗 {tag}")
            cases[f"progress-{tag}"] = _cr.draw_progress(
                list(zip(labels, values, strict=True)), title=f"进度 {tag}", target=100.0
            )
            cases[f"box-{tag}"] = _cr.draw_box(
                [(label, [v, v + 3, v + 5, v + 9]) for label, v in zip(labels, values, strict=True)],
                title=f"分布对比 {tag}",
            )
            if n >= 3:  # a radar with fewer than 3 axes is refused by design
                cases[f"radar-{tag}"] = _cr.draw_radar(
                    labels[:8], [("张三", values[:8])], title=f"能力雷达 {tag}", max_value=70.0
                )
            cases[f"heatmap-{tag}"] = _cr.draw_heatmap(
                labels,
                [f"渠道{j}" for j in range(1, 7)],
                [[v + j for v in values] for j in range(6)],
                title=f"热力 {tag}",
            )
            cases[f"gantt-{tag}"] = _cr.draw_gantt(
                [(label, float(i * 2), 5.0, f"组{i % 3}") for i, label in enumerate(labels)],
                title=f"排期 {tag}",
                tick_labels=_label_set("date", 31),
            )
    # Chart types whose crowding comes from point count, not category labels.
    for n in (2, 40, 200):
        pts = [(float(i), float((i * 13) % 67)) for i in range(n)]
        cases[f"scatter-{n}"] = _cr.draw_scatter([("样本", [[x, y] for x, y in pts])], title=f"散点 {n}")
        cases[f"bubble-{n}"] = _cr.draw_bubble(
            [(x, y, 5.0 + (i % 9)) for i, (x, y) in enumerate(pts)], size_label="规模", title=f"气泡 {n}"
        )
        cases[f"histogram-{n}"] = _cr.draw_histogram([y for _x, y in pts], title=f"直方 {n}")
    return cases


_MATRIX = _chart_matrix()


def _ink_of(fig: Any, artists: list[Any]) -> dict[int, Any]:
    """Map each artist to the set of pixels it actually inks.

    Hide all the text, rasterise once for a background, then reveal one artist at a time
    and diff: the changed pixels are exactly that label's glyphs. One draw per artist, so
    it is only used on pairs a cheap box test already flagged as suspicious.

    Relies on the caller having frozen the layout engine: hiding text shrinks the figure's
    tight bbox, so constrained layout would hand the freed space back to the axes and
    *move everything* between probe frames, measuring each label in a different layout
    than its neighbour and inventing overlaps that aren't on the canvas.
    """
    all_text = [t for t in fig.findobj(Text) if t.get_text().strip() and t.get_visible()]
    for artist in all_text:
        artist.set_visible(False)

    def frame() -> Any:
        fig.canvas.draw()
        return np.asarray(fig.canvas.buffer_rgba())[:, :, :3].astype(np.int16)

    try:
        blank = frame()
        ink = {}
        for artist in artists:
            artist.set_visible(True)
            ink[id(artist)] = np.argwhere(np.abs(frame() - blank).sum(axis=2) > 24)
            artist.set_visible(False)
    finally:
        for artist in all_text:
            artist.set_visible(True)
    return {key: {(int(y), int(x)) for y, x in pts} for key, pts in ink.items()}


def _collisions(fig: Any) -> list[str]:
    """Pairs of labels that ink the same pixels — what a reader sees as text over text.

    Two stages, because each instrument is wrong on its own. Bounding boxes are cheap
    but over-report: a rotated tick label's axis-aligned extent is far wider than its
    glyphs, and an upright one carries font ascent/descent padding, so boxes "overlap"
    while sharing no pixel. Pixels are exact but need a redraw per artist. So boxes
    nominate candidates and pixels decide.
    """
    # Settle the layout, then freeze it: every measurement below has to describe one
    # single arrangement of the canvas, and the layout engine reflows on every draw.
    fig.canvas.draw()
    fig.set_layout_engine("none")
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    boxed = []
    for artist in fig.findobj(Text):
        if not (artist.get_text().strip() and artist.get_visible()):
            continue
        bb = artist.get_window_extent(renderer=renderer)
        if bb.width > 0 and bb.height > 0:
            boxed.append((artist, bb))
    candidates = [(a, b) for i, (a, box_a) in enumerate(boxed) for b, box_b in boxed[i + 1 :] if box_a.overlaps(box_b)]
    if not candidates:
        return []
    suspects = list({id(art): art for pair in candidates for art in pair}.values())
    ink = _ink_of(fig, suspects)
    return [f"{a.get_text()[:14]}|{b.get_text()[:14]}" for a, b in candidates if ink[id(a)] & ink[id(b)]]


@pytest.fixture
def _figure() -> Any:
    """A canvas the same size and shape `render_to_png` uses, torn down after."""
    _cr._apply_style()
    made = []

    def make() -> Any:
        fig, ax = plt.subplots(figsize=(_cr._FIG_W, _cr._FIG_H), layout="constrained")
        made.append(fig)
        return fig, ax

    yield make
    for fig in made:
        plt.close(fig)


@pytest.mark.parametrize("name", list(_MATRIX))
def test_every_chart_renders_at_one_fixed_size(name: str, _figure: Any) -> None:
    """Same pixel size for every chart, so none of them lands in the doc as a thumbnail.

    With ``savefig.bbox="tight"`` the canvas was cropped to whatever content happened
    to be there, which produced 26 different sizes across the chart set — and the
    narrow ones displayed as thumbnails next to the wide ones.
    """
    fig, ax = _figure()
    _MATRIX[name](fig, ax)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)
    assert Image.open(buf).size == (int(_cr._FIG_W * _cr._DPI), int(_cr._FIG_H * _cr._DPI))


@pytest.mark.parametrize("name", list(_MATRIX))
def test_no_chart_draws_text_over_text(name: str, _figure: Any) -> None:
    """No two labels may share pixels — titles under legends, ticks into ticks.

    The reported cases: an area chart's title struck through by its legend, a
    histogram title behind the 均值/中位数 legend, and a gantt whose 31 date labels
    smeared into each other.
    """
    fig, ax = _figure()
    _MATRIX[name](fig, ax)
    assert _collisions(fig) == []


def test_title_and_legend_take_separate_rows(_figure: Any) -> None:
    """A legend sits above the axes, so the title has to move up rather than share.

    Both used to be placed in the same band — ``ax.set_title`` and a legend anchored
    at ``(0, 1.02)`` — which is what drew the legend through the title text.
    """
    fig, ax = _figure()
    _cr.draw_stacked_area(["1月", "2月"], [("直营", [1.0, 2.0]), ("加盟", [2.0, 3.0])], title="收入构成变化")(fig, ax)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    title = fig._suptitle
    assert title is not None, "a chart with a legend must promote its title to the figure"
    assert title.get_text() == "收入构成变化"
    legend = ax.get_legend()
    assert legend is not None
    title_box = title.get_window_extent(renderer=renderer)
    legend_box = legend.get_window_extent(renderer=renderer)
    assert title_box.y0 >= legend_box.y1, "title must sit above the legend, not across it"


def test_crowded_axis_thins_its_tick_labels(_figure: Any) -> None:
    """More labels than fit gets thinned; an axis that fits keeps every label."""
    fig, ax = _figure()
    ticks = [f"2026-08-{d:02d}" for d in range(1, 32)]
    _cr.draw_gantt(
        [("开发", 1.0, 30.0, "研发")],
        title="排期",
        tick_labels=ticks,
    )(fig, ax)
    fig.canvas.draw()
    shown = [t.get_text() for t in ax.get_xticklabels() if t.get_text().strip()]
    assert 0 < len(shown) < len(ticks)
    assert shown[0] == ticks[0]  # thinning keeps the first label, so the axis still anchors


def test_source_note_does_not_collide_with_the_axis(_figure: Any) -> None:
    """The data-source footnote used to be pinned under the tick labels and drawn through."""
    fig, ax = _figure()
    _cr.draw_line(
        [f"2026年{m}月" for m in range(1, 13)],
        [("A", [float(j) for j in range(12)])],
        title="趋势",
        source="财务系统",
    )(fig, ax)
    assert _collisions(fig) == []
    assert any("财务系统" in t.get_text() for t in fig.findobj(Text))


# ── Placing a chart into a docx as an image block ──────────────────────────────


class _FakeFeishu:
    """Records each _invoke call so the create → upload → patch sequence can be asserted.

    Call sites hand ``_invoke`` a request *factory* (so a retry under a second identity
    gets a clean request), so resolve it the way the real ``_invoke`` does before
    recording — the assertions below are about the request that would go on the wire.
    """

    def __init__(self, *, fail_at: str = "") -> None:
        self.calls: list[Any] = []
        self.fail_at = fail_at

    async def __call__(
        self, request: Any, user_key: str | None = None, prefer: str = "tenant", **_kw: Any
    ) -> dict[str, Any]:
        request = request() if callable(request) else request
        self.calls.append(request)
        uri = getattr(request, "uri", "")
        method = request.http_method.name
        if "medias/upload_all" in uri:
            if self.fail_at == "upload":
                return {"ok": False, "message": "upload rejected"}
            return {"ok": True, "data": {"file_token": "tok_img"}}
        if method == "PATCH":
            if self.fail_at == "patch":
                return {"ok": False, "message": "patch rejected"}
            return {"ok": True, "data": {}}
        if method == "DELETE":
            return {"ok": True, "data": {}}
        if "children" in uri:
            return {"ok": True, "data": {"children": [{"block_id": "blk1"}], "index": 3}}
        return {"ok": True, "data": {}}

    def uris(self) -> list[str]:
        return [getattr(c, "uri", "") for c in self.calls]


@pytest.mark.asyncio
async def test_append_doc_image_runs_create_upload_patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    png = tmp_path / "c.png"
    png.write_bytes(_PNG_MAGIC + b"0" * 100)
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", str(png))
    assert result["ok"] is True
    assert result["block_id"] == "blk1"
    assert result["file_token"] == "tok_img"
    methods = [c.http_method.name for c in fake.calls]
    assert methods == ["POST", "POST", "PATCH"]  # create block, upload media, bind token
    # The upload must target the new block, not a Drive folder.
    upload = fake.calls[1]
    assert upload.body["parent_type"] == "docx_image"
    assert upload.body["parent_node"] == "blk1"
    assert fake.calls[2].body["replace_image"]["token"] == "tok_img"


@pytest.mark.asyncio
async def test_append_doc_image_writes_caption(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    png = tmp_path / "c.png"
    png.write_bytes(_PNG_MAGIC + b"0" * 100)
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", str(png), "图1：人力分布")  # noqa: RUF001
    assert result["caption_written"] is True
    assert any("blocks/:block_id/children" in u for u in fake.uris()[3:])


@pytest.mark.asyncio
async def test_append_doc_image_cleans_up_after_failed_upload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    png = tmp_path / "c.png"
    png.write_bytes(_PNG_MAGIC + b"0" * 100)
    fake = _FakeFeishu(fail_at="upload")
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", str(png))
    assert result["ok"] is False
    # the empty placeholder block must not be left behind
    assert "DELETE" in [c.http_method.name for c in fake.calls]


@pytest.mark.asyncio
async def test_append_doc_image_cleans_up_after_failed_patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    png = tmp_path / "c.png"
    png.write_bytes(_PNG_MAGIC + b"0" * 100)
    fake = _FakeFeishu(fail_at="patch")
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", str(png))
    assert result["ok"] is False
    assert "DELETE" in [c.http_method.name for c in fake.calls]


@pytest.mark.asyncio
async def test_append_doc_image_requires_document_id() -> None:
    result = await _impl.append_doc_image_impl("  ", "x.png")
    assert result["ok"] is False
    assert "document_id" in result["message"]


@pytest.mark.asyncio
async def test_append_doc_image_reports_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", "no/such/chart.png")
    assert result["ok"] is False
    assert "not found" in result["message"]


@pytest.mark.asyncio
async def test_chart_tool_places_into_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeFeishu()
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = json.loads(
        await _chart._render_pie(
            '["研发","市场"]',
            "[3,1]",
            document_id="doc1",
            caption="图1：占比",  # noqa: RUF001
        )
    )
    assert result["ok"] is True
    assert result["block_id"] == "blk1"
    assert result["chart_type"] == "pie"
    assert await anyio.Path(result["image_path"]).exists()


@pytest.mark.asyncio
async def test_chart_tool_keeps_png_when_placement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeFeishu(fail_at="upload")
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = json.loads(await _chart._render_pie('["研发","市场"]', "[3,1]", document_id="doc1"))
    assert result["ok"] is False
    # the rendered chart is still usable — say so rather than implying total failure
    assert await anyio.Path(result["image_path"]).exists()
    assert "usable" in result["hint"]


@pytest.mark.asyncio
async def test_pie_reports_folded_slices_to_the_caller() -> None:
    labels = json.dumps([f"部门{i}" for i in range(9)], ensure_ascii=False)
    values = json.dumps(list(range(1, 10)))
    result = json.loads(await _chart._render_pie(labels, values))
    assert result["ok"] is True
    assert result["folded_into_other"] == 3


# ── Regressions: the two reasons charts silently failed to land in a doc ────────


def test_upload_request_carries_the_binary_as_a_file_object() -> None:
    """The SDK decides multipart by finding an ``io.IOBase`` in the *body*.

    Assigning ``req.files`` looks right but is discarded: ``Client.arequest`` overwrites
    it with ``Files.extract_files(req.body)`` just before sending, so a ``bytes`` payload
    went out as application/json and Feishu answered ``400 boundary not found``.
    """
    req = _impl._build_media_upload_all_request("chart.png", "docx_image", "blk1", 3, b"png", None)
    extracted = _impl.Files.extract_files(req.body) if hasattr(_impl, "Files") else None
    assert isinstance(req.body["file"], io.IOBase)
    assert req.body["file"].name == "chart.png"
    assert req.body["file"].read() == b"png"
    assert extracted is None or "file" in extracted


def test_upload_request_is_rebuildable_for_a_second_identity() -> None:
    """Sending consumes the body's file entry, so a retry needs a freshly built request."""
    build = lambda: _impl._build_media_upload_all_request("c.png", "docx_image", "b", 3, b"png", None)  # noqa: E731
    first = build()
    del first.body["file"]  # what the SDK does on send
    second = _impl._fresh(build)
    assert isinstance(second.body["file"], io.IOBase)
    assert second.body["file"].read() == b"png"


@pytest.mark.asyncio
async def test_invoke_falls_back_to_tenant_when_the_user_token_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user authorizing the app says nothing about their rights on *this* doc.

    Feishu answers 1770032 forBidden for the UAT while the bot's tenant token can edit
    the block fine, so a denied UAT must not end the attempt.
    """
    sent: list[str] = []

    async def as_user(request: Any, user_key: str) -> dict[str, Any]:
        sent.append("user")
        return {"ok": False, "code": 1770032, "msg": "forBidden", "message": "Feishu API error 1770032: forBidden"}

    async def as_tenant(request: Any) -> dict[str, Any]:
        sent.append("tenant")
        return {"ok": True, "code": 0, "data": {"children": [{"block_id": "blk1"}]}}

    monkeypatch.setattr(_impl, "_send_as_user", as_user)
    monkeypatch.setattr(_impl, "_send_as_tenant", as_tenant)
    res = await _impl._invoke(lambda: object(), user_key="ou_x", prefer="user", identity="user", capabilities=[])
    assert sent == ["user", "tenant"]
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_invoke_reports_the_denial_when_neither_identity_may_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-authorizing can't fix a doc nobody may edit, so don't send the user through it."""
    denied = {"ok": False, "code": 1770032, "msg": "forBidden", "message": "Feishu API error 1770032: forBidden"}

    async def as_user(request: Any, user_key: str) -> dict[str, Any]:
        return dict(denied)

    async def as_tenant(request: Any) -> dict[str, Any]:
        return dict(denied)

    monkeypatch.setattr(_impl, "_send_as_user", as_user)
    monkeypatch.setattr(_impl, "_send_as_tenant", as_tenant)
    res = await _impl._invoke(lambda: object(), user_key="ou_x", prefer="user", identity="user", capabilities=[])
    assert res["ok"] is False
    assert res.get("need_auth") is not True
    assert res["code"] == 1770032


def test_forbidden_code_is_recognised_as_a_permission_error() -> None:
    assert _impl._is_permission_error({"ok": False, "code": 1770032, "msg": "forBidden"}) is True


@pytest.mark.asyncio
async def test_invoke_sends_a_fresh_request_per_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each attempt must get its own request: the SDK narrows token_types in place."""
    seen: list[Any] = []
    built: list[Any] = []

    # _invoke hands the senders a factory; resolving it with _fresh is what the real
    # _send_as_* do, so the doubles must do it too or they'd inspect the factory itself.
    async def as_user(request: Any, user_key: str) -> dict[str, Any]:
        seen.append(_impl._fresh(request))
        return {"ok": False, "code": 1770032, "msg": "forBidden"}

    async def as_tenant(request: Any) -> dict[str, Any]:
        seen.append(_impl._fresh(request))
        return {"ok": True, "code": 0, "data": {}}

    def build() -> Any:
        # Held in `built` so neither object is freed — comparing id()s of dead objects
        # is unsound, CPython reuses addresses.
        obj = type("R", (), {})()
        built.append(obj)
        return obj

    monkeypatch.setattr(_impl, "_send_as_user", as_user)
    monkeypatch.setattr(_impl, "_send_as_tenant", as_tenant)
    await _impl._invoke(build, user_key="ou_x", prefer="user", identity="user", capabilities=[])
    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert len(built) == 2


@pytest.mark.asyncio
async def test_plain_requests_survive_the_tenant_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A call site that passes a plain BaseRequest must still retry correctly.

    The SDK narrows ``token_types`` in place, so the tenant attempt used to inherit
    ``{USER}`` from the failed UAT attempt and raise
    ``NoAuthorizationException: user_access_token not found`` — which is how captions
    broke while the image itself went in fine.
    """
    seen: list[set[Any]] = []

    async def as_user(request: Any, user_key: str) -> dict[str, Any]:
        sent = _impl._fresh(request)
        seen.append(set(sent.token_types))
        sent.token_types = {AccessTokenType.USER}  # what verify() does
        sent.body.pop("file", None)  # what extract_files() does
        return {"ok": False, "code": 1770032, "msg": "forBidden"}

    async def as_tenant(request: Any) -> dict[str, Any]:
        seen.append(set(_impl._fresh(request).token_types))
        return {"ok": True, "code": 0, "data": {}}

    monkeypatch.setattr(_impl, "_send_as_user", as_user)
    monkeypatch.setattr(_impl, "_send_as_tenant", as_tenant)

    req = _impl._build_media_upload_all_request("c.png", "docx_image", "blk1", 3, b"png", None)
    res = await _impl._invoke(req, user_key="ou_x", prefer="user", identity="user", capabilities=[])
    assert res["ok"] is True
    # The tenant attempt must see both token types again, not the narrowed {USER}.
    assert AccessTokenType.TENANT in seen[1]
    # and the file must be back in the body, rewound, so the retry re-sends the bytes
    assert req.body["file"].read() == b"png"


@pytest.mark.asyncio
async def test_failed_upload_deletes_the_placeholder_without_an_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup must not depend on an ``index`` the create response never returns.

    Feishu answers the block-create with ``{children, client_token,
    document_revision_id}`` and no index, so the old guard skipped the delete every
    time and each failed chart left a permanent empty image box in the user's doc.
    """
    png = tmp_path / "c.png"
    png.write_bytes(_PNG_MAGIC + b"0" * 100)
    deleted: list[dict[str, Any]] = []

    async def fake(request: Any, user_key: str | None = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        uri, method = getattr(req, "uri", ""), req.http_method.name
        if "medias/upload_all" in uri:
            return {"ok": False, "message": "upload rejected"}
        if method == "GET" and "children" in uri:
            # No index on create, so cleanup has to find the block by listing children.
            return {"ok": True, "data": {"items": [{"block_id": "other"}, {"block_id": "blk1"}]}}
        if method == "DELETE":
            deleted.append(dict(req.body))
            return {"ok": True, "data": {}}
        if "children" in uri:
            return {"ok": True, "data": {"children": [{"block_id": "blk1"}], "document_revision_id": 11}}
        return {"ok": True, "data": {}}

    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.append_doc_image_impl("doc1", str(png))
    assert result["ok"] is False
    assert deleted == [{"start_index": 1, "end_index": 2}]


# ── Combined figures: several panels in one image ──────────────────────────────

# One panel spec per chart kind, in the shape `panels_json` accepts. Kept exhaustive on
# purpose: a panel builder that forgets a chart kind, or drifts from that chart's tool
# arguments, only shows up when every kind is actually built.
#
# The data is deliberately realistic rather than minimal — units on the values, CJK labels
# of the length a real report uses, and two or more series where the chart supports them.
# A two-point single-series panel draws a one-item legend and short labels, which is
# exactly the case that has room to spare and hides every crowding defect.
_PANEL_SPECS: dict[str, dict[str, Any]] = {
    "pie": {"labels": ["研发", "市场", "销售"], "values": [42, 28, 19]},
    "donut": {"labels": ["华东", "华北", "华南"], "values": [520, 310, 180]},
    "funnel": {"stages": ["访问", "注册", "下单", "复购"], "values": [1000, 420, 180, 60]},
    "line": {
        "labels": ["1月", "2月", "3月", "4月"],
        "series": {"营收": [120, 145, 138, 170], "成本": [80, 90, 88, 95]},
    },
    "area": {"labels": ["1月", "2月", "3月"], "series": {"累计营收": [120, 265, 403]}},
    "stacked_area": {"labels": ["1月", "2月", "3月"], "series": {"直销": [60, 70, 66], "线上": [60, 75, 72]}},
    "column": {"labels": ["华东", "华北", "华南", "西南"], "values": [118, 92, 76, 54], "unit": "万元"},
    "bar": {"labels": ["华东区域", "华北区域", "华南区域"], "values": [118, 92, 76], "unit": "万元"},
    "grouped_column": {"labels": ["Q1", "Q2", "Q3"], "series": {"2025": [10, 20, 15], "2026": [14, 25, 19]}},
    "stacked_column": {"labels": ["Q1", "Q2", "Q3"], "series": {"直销": [10, 20, 15], "线上": [14, 25, 19]}},
    "waterfall": {"labels": ["期初", "新增", "流失", "期末"], "deltas": [100, 40, -25, 0]},
    "histogram": {"values": [1, 2, 2, 3, 3, 3, 4, 4, 5, 6, 3, 3, 2, 4, 5]},
    "box": {"groups": {"A组": [1, 2, 3, 4], "B组": [2, 3, 4, 5], "C组": [3, 4, 5, 6]}},
    "scatter": {"points": [[1, 2], [3, 4], [5, 3], [2, 5]], "x_label": "投入（万元）", "y_label": "产出（万元）"},  # noqa: RUF001
    "bubble": {"points": [[1, 2, 30], [3, 4, 50], [5, 3, 80]]},
    "heatmap": {
        "row_labels": ["线上", "线下"],
        "col_labels": ["华东", "华北", "华南"],
        "values": [[12, 5, 3], [4, 9, 6]],
    },
    "radar": {"axes": ["质量", "速度", "成本", "服务"], "series": {"A组": [4, 3, 5, 4], "B组": [3, 5, 3, 5]}},
    "pareto": {"labels": ["登录失败", "支付超时", "页面卡顿"], "values": [120, 85, 42]},
    "combo": {
        "labels": ["1月", "2月", "3月"],
        "bar_series": {"营收": [120, 145, 138]},
        "line_series": {"毛利率": [32, 35, 33]},
        "line_percent": True,
    },
    "gantt": {
        "tasks": [
            {"name": "设计", "start": "2026-08-01", "days": 5},
            {"name": "开发", "start": "2026-08-04", "days": 8},
        ]
    },
    "progress": {"items": {"华东": 118, "华北": 92, "华南": 76}, "target": 100},
}


def _panels_json(*kinds: str) -> str:
    return json.dumps(
        [{"chart": kind, "title": f"面板{kind}", **_PANEL_SPECS[kind]} for kind in kinds], ensure_ascii=False
    )


def test_every_chart_kind_has_a_panel_spec_in_this_test() -> None:
    """The matrix below is only meaningful if it covers every kind the tool advertises."""
    assert set(_PANEL_SPECS) == set(_cr.PANEL_CHARTS)


@pytest.mark.parametrize("kind", sorted(_PANEL_SPECS))
def test_build_panel_draw_handles_every_chart_kind(kind: str) -> None:
    draw, title = _cr.build_panel_draw({"chart": kind, "title": "标题", **_PANEL_SPECS[kind]}, 0)
    assert callable(draw)
    assert title == "标题"


def test_build_panel_draw_rejects_an_unknown_chart() -> None:
    with pytest.raises(_cr.ChartDataError, match="unknown chart"):
        _cr.build_panel_draw({"chart": "sunburst", "labels": ["a"], "values": [1]}, 0)


def test_build_panel_draw_names_the_panel_and_its_missing_field() -> None:
    with pytest.raises(_cr.ChartDataError, match=r"panel 3 .*line.*missing: series"):
        _cr.build_panel_draw({"chart": "line", "labels": ["1月"]}, 2)


@pytest.mark.parametrize(
    ("layout", "count", "expected"),
    [
        ("horizontal", 2, (1, 2)),
        ("vertical", 3, (3, 1)),
        ("grid", 4, (2, 2)),
        ("grid", 3, (2, 2)),  # a 3-panel grid keeps a 2x2 shape and hides the empty cell
        ("grid", 6, (2, 3)),
    ],
)
def test_panel_grid_shapes(layout: str, count: int, expected: tuple[int, int]) -> None:
    assert _cr.panel_grid(count, layout) == expected


def test_panel_grid_rejects_an_unknown_layout() -> None:
    with pytest.raises(_cr.ChartDataError, match="unknown layout"):
        _cr.panel_grid(2, "diagonal")


def test_figure_size_refuses_a_canvas_a_doc_cannot_show() -> None:
    """Six panels in one row would be 48in wide, which Feishu scales down to a thumbnail."""
    with pytest.raises(_cr.ChartDataError, match=r"over the .*limit"):
        _cr.figure_size(1, 6)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("layout", "kinds", "size"),
    [
        # Each panel keeps the full 1600x900 single-chart footprint.
        ("horizontal", ("line", "pie"), (3200, 900)),
        ("vertical", ("line", "pie"), (1600, 1800)),
        ("grid", ("line", "pie", "bar"), (3200, 1800)),
    ],
)
async def test_render_panels_writes_one_png_sized_by_the_grid(
    tmp_path: Path, layout: str, kinds: tuple[str, ...], size: tuple[int, int]
) -> None:
    draws, titles = _cr.parse_panels(_panels_json(*kinds))
    out = await _cr.render_panels_to_png(draws, str(tmp_path / "fig.png"), layout=layout, figure_title="总览")
    assert titles == [f"面板{k}" for k in kinds]
    with Image.open(out) as img:
        assert img.size == size


@pytest.mark.asyncio
async def test_render_panels_rejects_a_single_panel(tmp_path: Path) -> None:
    draws, _titles = _cr.parse_panels(_panels_json("line"))
    with pytest.raises(_cr.ChartDataError, match="at least 2 panels"):
        await _cr.render_panels_to_png(draws, str(tmp_path / "fig.png"))


@pytest.mark.asyncio
async def test_render_panels_rejects_too_many_panels(tmp_path: Path) -> None:
    draws, _titles = _cr.parse_panels(_panels_json(*(("line",) * 7)))
    with pytest.raises(_cr.ChartDataError, match="more than 6"):
        await _cr.render_panels_to_png(draws, str(tmp_path / "fig.png"), layout="grid")


def test_panel_titles_and_sources_stay_on_their_own_panel() -> None:
    """Regression: both used to be written at *figure* level, so panel 2 erased panel 1.

    ``_set_title`` promoted a title to ``fig.suptitle`` whenever a legend was present and
    ``_source_note`` used ``fig.supxlabel`` — a figure has one of each, so in a two-panel
    figure only the last panel's title and source survived.
    """
    token = _cr._panel_mode.set(True)
    try:
        fig, axes = plt.subplots(1, 2, figsize=(16, 4.5), layout="constrained", squeeze=False)
        flat = [ax for row in axes for ax in row]
        for ax, name in zip(flat, ("左", "右"), strict=True):
            draw = _cr.draw_line(
                ["1月", "2月"], [(name, [1, 2]), ("对比", [2, 1])], title=f"标题{name}", source=f"来源{name}"
            )
            draw(fig, ax)
    finally:
        _cr._panel_mode.reset(token)
    assert fig.get_suptitle() == ""  # nothing was promoted to the shared figure slot
    assert [ax.get_title(loc="left") for ax in flat] == ["标题左", "标题右"]
    labelled = [ax.get_xlabel().endswith(f"来源{side}") for ax, side in zip(flat, ("左", "右"), strict=True)]
    assert labelled == [True, True]
    plt.close(fig)


def _build_figure(kinds: tuple[str, ...], layout: str) -> Any:
    """A combined figure built exactly the way ``_render_panels_sync`` builds one.

    Mirrors the real path step for step — panel mode, tags, figure title/source, then both
    closing passes — because the layout defects this guards against only appear in the
    finished figure, after those passes have moved things.
    """
    # Before any figure exists: the house style installs the CJK font stack, and glyph
    # widths are what decide whether two labels collide. Measuring with matplotlib's
    # default font would size every Chinese label as a tofu box and test a layout no user
    # ever sees.
    _cr._apply_style()
    rows, cols = _cr.panel_grid(len(kinds), layout)
    size = _cr.figure_size(rows, cols)
    token = _cr._panel_mode.set(True)
    try:
        fig = plt.figure(figsize=size, layout="constrained")
        cells = fig.subfigures(rows, cols, squeeze=False)
        flat_cells = [cell for row in cells for cell in row]
        draws, _titles = _cr.parse_panels(_panels_json(*kinds))
        for index, (draw, cell) in enumerate(zip(draws, flat_cells, strict=False)):
            ax = cell.subplots()
            before = set(cell.get_axes())
            draw(cell, ax)
            live = ax if ax in cell.get_axes() else next(iter(set(cell.get_axes()) - before), ax)
            _cr._tag_panel(live, index)
        fig.suptitle("整图标题", x=0.01, ha="left", fontsize=18, fontweight="bold")
        fig.supxlabel("数据来源：台账", fontsize=10, ha="left", x=0.01)  # noqa: RUF001
        _cr._promote_panel_titles(fig)
        _cr._settle_panel_annotations(fig)
        _cr._align_panel_plot_boxes(fig)
    finally:
        _cr._panel_mode.reset(token)
    return fig


def test_panels_are_tagged_a_b_c_below_the_axes_and_do_not_collide() -> None:
    """Every panel carries its (a)/(b)/(c) key, centred under the panel, ink-free.

    The tag is its panel's subfigure supxlabel: a real layout band below the axes, which
    is where a paper puts a sub-figure key and what makes the tags line up with each other
    across a row. Prefixing it into the title (an earlier version) left them ragged, each
    starting wherever its title did.
    """
    fig = _build_figure(("line", "pie", "bar"), "grid")
    cells = [ax.get_figure() for ax in fig.get_axes() if ax.get_subplotspec() is not None]
    tags = [cell._supxlabel for cell in cells]
    assert [tag.get_text() for tag in tags] == ["(a)", "(b)", "(c)"]
    assert {tag.get_ha() for tag in tags} == {"center"}
    # Below its own plot box, not above it.
    for cell, tag in zip(cells, tags, strict=True):
        axes_box = next(ax for ax in cell.get_axes() if ax.get_subplotspec() is not None).get_window_extent()
        assert tag.get_window_extent(fig.canvas.get_renderer()).y1 <= axes_box.y0
    assert _collisions(fig) == []
    plt.close(fig)


def test_panel_titles_leave_the_plot_box_its_full_height() -> None:
    """A panel's title must not be bought with plot height.

    The first fix for "title struck through by the legend" padded the title. Constrained
    layout answers a pad by shrinking the axes, so the loop chased its own tail and left a
    207px-tall plot box where 589px was available — the panels came out flattened. The
    title now goes in the subfigure's own title band, which costs the plot box nothing.
    """
    fig = _build_figure(("line", "column"), "horizontal")
    for ax in fig.get_axes():
        cell = ax.get_figure()
        box, cell_box = ax.get_window_extent(), cell.bbox
        assert box.height > cell_box.height * 0.45, f"plot box squeezed to {box.height:.0f}px"
        # The promoted title sits above the legend band, and only one copy of it exists.
        assert ax.get_title(loc="left") == ""
        assert cell._suptitle.get_window_extent(fig.canvas.get_renderer()).y0 >= box.y1
    plt.close(fig)


@pytest.mark.parametrize(
    "kinds",
    [("line", "column"), ("donut", "line"), ("radar", "heatmap"), ("funnel", "progress", "gantt", "bubble")],
)
def test_every_panel_gets_the_same_plot_box_size(kinds: tuple[str, ...]) -> None:
    """Panels of different chart kinds must still look the same size.

    Constrained layout sizes each panel around its own decorations, so a chart with a
    two-line legend got a 1409x588 box beside its neighbour's 1468x515 — side by side the
    charts read as different sizes, which is what a reader notices before any detail. A
    square chart (pie, heatmap) keeps its aspect *inside* an equal allocation.
    """
    layout = "grid" if len(kinds) > 2 else "horizontal"
    fig = _build_figure(kinds, layout)
    boxes = {
        (round(ax.get_window_extent().width, 1), round(ax.get_window_extent().height, 1))
        for ax in fig.get_axes()
        if ax.get_subplotspec() is not None
    }
    plt.close(fig)
    assert len(boxes) == 1, f"panels differ in size: {boxes}"


# Every ordered pair of chart kinds, in both a row and a column. One combination is not
# enough coverage: the first version of the panel code was checked against line+pie+bar
# only, and 245 of these 420 pairs had text over text — a panel title struck through by
# its legend, a box plot's glyph key sitting on its tick labels, a column's top value
# label pushed into the title band. Each is invisible in the one combination that happened
# to be tested, and obvious in the matrix.
_PANEL_PAIRS = [(a, b) for a, b in itertools.combinations(sorted(_PANEL_SPECS), 2)]


@pytest.mark.parametrize("layout", ["horizontal", "vertical"])
@pytest.mark.parametrize(("first", "second"), _PANEL_PAIRS)
def test_no_two_labels_overlap_in_any_two_panel_figure(first: str, second: str, layout: str) -> None:
    fig = _build_figure((first, second), layout)
    overlaps = _collisions(fig)
    plt.close(fig)
    assert overlaps == []


@pytest.mark.parametrize("start", range(len(_PANEL_SPECS)))
def test_no_two_labels_overlap_in_a_four_panel_grid(start: int) -> None:
    """Grids add their own failure mode: a panel is half-height, so labels sit closer.

    The window slides one kind at a time and wraps, rather than stepping in blocks of
    four: block-stepping only ever built 5 of the 21 possible groupings, and the donut's
    centre total striking through its 合计 lived in a grouping no block reached.
    """
    ordered = sorted(_PANEL_SPECS)
    kinds = tuple(ordered[(start + offset) % len(ordered)] for offset in range(4))
    fig = _build_figure(kinds, "grid")
    overlaps = _collisions(fig)
    plt.close(fig)
    assert overlaps == []


def test_a_panel_without_a_title_still_gets_its_tag() -> None:
    """The caption refers to panels by tag, so an untitled panel can't go unlabelled."""
    token = _cr._panel_mode.set(True)
    try:
        fig = plt.figure(figsize=(8, 4.5), layout="constrained")
        cell = fig.subfigures(1, 1, squeeze=False)[0][0]
        ax = cell.subplots()
        _cr.draw_line(["1月", "2月"], [("营收", [1, 2])])(cell, ax)
        _cr._tag_panel(ax, 1)
    finally:
        _cr._panel_mode.reset(token)
    assert cell.get_supxlabel() == "(b)"
    plt.close(fig)


@pytest.mark.asyncio
async def test_radar_panel_takes_only_its_own_cell(tmp_path: Path) -> None:
    """A radar swaps in polar axes; built from `111` it would cover the whole figure."""
    draws, _titles = _cr.parse_panels(_panels_json("radar", "line"))
    out = await _cr.render_panels_to_png(draws, str(tmp_path / "fig.png"), layout="horizontal")
    with Image.open(out) as img:
        assert img.size == (3200, 900)
        # The right half must still carry the line panel's ink, not blank canvas.
        right = np.asarray(img.convert("RGB"))[:, 1600:, :]
    assert (right != 255).any()


# ── Caption numbering ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("content", "kind", "expected"),
    [
        ("", "图", 0),
        ("图1：人力分布", "图", 1),  # noqa: RUF001
        ("图 1：a\n\n图 2：b", "图", 2),  # noqa: RUF001
        ("图一：手写的中文数字", "图", 1),  # noqa: RUF001
        ("图十二：两位中文数字", "图", 12),  # noqa: RUF001
        # Figures and tables are independent sequences.
        ("表 3：台账\n图 1：图表", "图", 1),  # noqa: RUF001
        ("表 3：台账\n图 1：图表", "表", 3),  # noqa: RUF001
        # Ordinary words starting with 图/表 are not captions.
        ("图书馆的表现很好", "图", 0),
        ("图书馆的表现很好", "表", 0),
        # In-prose references count too: the number is already spoken for.
        ("如图 4 所示，营收上行", "图", 4),  # noqa: RUF001
        ("(图 5) 见上", "图", 5),
        ("详见表2、表3", "表", 3),
    ],
)
def test_highest_number_reads_the_captions_already_in_a_doc(content: str, kind: str, expected: int) -> None:
    assert _cap.highest_number(content, kind) == expected


def test_format_caption_names_the_panels() -> None:
    assert _cap.format_caption("图", 3, "各区域经营概况") == "图 3：各区域经营概况"  # noqa: RUF001
    assert _cap.format_caption("图", 3, "经营概况", ["营收趋势", "渠道占比"]) == (
        "图 3：经营概况\n(a) 营收趋势；(b) 渠道占比"  # noqa: RUF001
    )
    # A single panel needs no key — "(a) x" alone tells the reader nothing.
    assert "\n" not in _cap.format_caption("图", 1, "概况", ["只有一个"])


def test_strip_own_number_removes_a_hand_written_prefix() -> None:
    """Agents were told for months to write the number themselves; that habit outlives this change."""
    assert _cap.strip_own_number("图2：缺陷分析", "图") == "缺陷分析"  # noqa: RUF001
    assert _cap.strip_own_number("图 12. 缺陷分析", "图") == "缺陷分析"
    assert _cap.strip_own_number("缺陷分析", "图") == "缺陷分析"
    # Not a caption prefix, just a word that happens to start with 图.
    assert _cap.strip_own_number("图书馆调研", "图") == "图书馆调研"


class _FakeDoc:
    """A fake docx whose raw_content grows as captions are written into it.

    Numbering is derived from the document, so a fake that *accumulates* is the only
    kind that can show a sequence continuing across separate calls.
    """

    def __init__(self, content: str = "") -> None:
        self.content = content
        self.written: list[str] = []

    async def __call__(
        self, request: Any, user_key: str | None = None, prefer: str = "tenant", **_kw: Any
    ) -> dict[str, Any]:
        req = request() if callable(request) else request
        uri, body = getattr(req, "uri", ""), getattr(req, "body", None) or {}
        if "raw_content" in uri:
            return {"ok": True, "data": {"content": self.content}}
        if "medias/upload_all" in uri:
            return {"ok": True, "data": {"file_token": "tok_img"}}
        if "descendant" in uri:
            return {"ok": True, "data": {}}
        if uri.endswith("/blocks/:block_id/children") and body.get("children"):
            children = body["children"]
            if children[0].get("block_type") == 27:  # the image placeholder
                return {"ok": True, "data": {"children": [{"block_id": "blk1"}], "index": 1}}
            for block in children:
                text = "".join(e["text_run"]["content"] for e in block.get("text", {}).get("elements", []))
                self.written.append(text)
                self.content += text + "\n"
            return {"ok": True, "data": {}}
        return {"ok": True, "data": {}}


@pytest.mark.asyncio
async def test_caption_numbers_continue_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDoc("图 1：已有的图\n")  # noqa: RUF001
    monkeypatch.setattr(_impl, "_invoke", fake)
    first = json.loads(await _chart._render_pie('["研发","市场"]', "[3,1]", document_id="doc1", caption="人力分布"))
    second = json.loads(await _chart._render_bar('["华东","华北"]', "[3,1]", document_id="doc1", caption="区域排名"))
    assert (first["caption_number"], second["caption_number"]) == (2, 3)
    assert fake.written == ["图 2：人力分布", "图 3：区域排名"]  # noqa: RUF001
    # The caller numbers nothing itself, so the returned caption is its only way to know
    # which line the chart landed under. It must be the text actually written.
    assert [first["caption"], second["caption"]] == fake.written


@pytest.mark.asyncio
async def test_no_caption_given_reports_no_caption_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ``caption`` field would read as if a blank caption had been written."""
    fake = _FakeDoc("")
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = json.loads(await _chart._render_pie('["研发","市场"]', "[3,1]", document_id="doc1"))
    assert result["ok"] is True
    assert "caption" not in result
    assert fake.written == []


@pytest.mark.asyncio
async def test_a_hand_written_number_is_not_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDoc()
    monkeypatch.setattr(_impl, "_invoke", fake)
    await _chart._render_pie('["研发","市场"]', "[3,1]", document_id="doc1", caption="图9：人力分布")  # noqa: RUF001
    assert fake.written == ["图 1：人力分布"]  # noqa: RUF001


@pytest.mark.asyncio
async def test_auto_number_off_writes_the_caption_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDoc("图 7：已有的图\n")  # noqa: RUF001
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = json.loads(
        await _chart._render_pie('["研发","市场"]', "[3,1]", document_id="doc1", caption="人力分布", auto_number=False)
    )
    assert "caption_number" not in result
    assert fake.written == ["图：人力分布"]  # noqa: RUF001


@pytest.mark.asyncio
async def test_an_unreadable_document_still_gets_its_caption(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chart is worth having without its number; the reason is reported, not raised."""

    async def fake(request: Any, user_key: str | None = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        uri = getattr(req, "uri", "")
        if "raw_content" in uri:
            return {"ok": False, "message": "no permission to read the doc"}
        if "medias/upload_all" in uri:
            return {"ok": True, "data": {"file_token": "tok_img"}}
        if "children" in uri:
            return {"ok": True, "data": {"children": [{"block_id": "blk1"}], "index": 1}}
        return {"ok": True, "data": {}}

    monkeypatch.setattr(_impl, "_invoke", fake)
    result = json.loads(await _chart._render_pie('["研发","市场"]', "[3,1]", document_id="doc1", caption="人力分布"))
    assert result["ok"] is True
    assert "no permission" in result["caption_number_skipped"]
    assert result["caption_written"] is True


@pytest.mark.asyncio
async def test_figure_tool_places_one_image_with_a_panel_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDoc("图 1：已有的图\n")  # noqa: RUF001
    monkeypatch.setattr(_impl, "_invoke", fake)
    result = json.loads(
        await _chart.feishu_chart_figure(
            _panels_json("line", "pie"), document_id="doc1", caption="经营概览", figure_title="上半年"
        )
    )
    assert result["ok"] is True
    assert (result["panels"], result["caption_number"]) == (2, 2)
    # One image block for the whole figure, and a caption naming both panels.
    assert sum(1 for c in fake.written if c.startswith("图")) == 1
    assert fake.written[0] == "图 2：经营概览"  # noqa: RUF001
    assert fake.written[1] == "(a) 面板line；(b) 面板pie"  # noqa: RUF001


@pytest.mark.asyncio
async def test_figure_without_a_document_leaves_the_png_on_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    result = json.loads(await _chart.feishu_chart_figure(_panels_json("line", "pie")))
    assert result["ok"] is True
    assert await anyio.Path(result["image_path"]).is_file()
    assert "no document_id" in result["note"]


@pytest.mark.asyncio
async def test_table_caption_goes_above_the_table_with_its_own_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Table titles sit above the table (figures caption below), numbered 表 N separately."""
    fake = _FakeDoc("表 1：已有台账\n图 5：已有的图\n")  # noqa: RUF001
    monkeypatch.setattr(_impl, "_invoke", fake)
    order: list[str] = []
    original = fake.__call__

    async def tracking(request: Any, **kw: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        if "descendant" in getattr(req, "uri", ""):
            order.append("table")
        elif getattr(req, "body", None) and (req.body.get("children") or [{}])[0].get("block_type") == 2:
            order.append("caption")
        return await original(req, **kw)

    monkeypatch.setattr(_impl, "_invoke", tracking)
    result = json.loads(
        await _doc.feishu_doc_append_table("doc1", '[["姓名","部门"],["张三","研发"]]', caption="客户明细")
    )
    assert result["caption_number"] == 2  # continues 表, not the doc's 图 5
    assert result["caption_written"] is True
    assert order == ["caption", "table"]
    assert fake.written == ["表 2：客户明细"]  # noqa: RUF001
