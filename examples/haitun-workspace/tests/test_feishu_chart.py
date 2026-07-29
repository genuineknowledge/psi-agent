"""Tests for the Feishu chart tools — parsing, rendering, and doc placement.

Renders are real (matplotlib to a temp PNG) since the whole value of these tools is
that a legible file comes out; the Feishu API calls are faked, so nothing here needs
credentials or network.
"""

from __future__ import annotations

import importlib
import io
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
        (lambda: _chart.feishu_chart_pie, ('["研发","市场"]', "[3,1]"), {"unit": "人"}),
        (lambda: _chart.feishu_chart_donut, ('["华东","华北"]', "[520,310]"), {"unit": "万"}),
        (lambda: _chart.feishu_chart_funnel, ('["访问","付费"]', "[100,20]"), {}),
        (lambda: _chart.feishu_chart_line, ('["1月","2月"]', '{"A":[1,2]}'), {}),
        (lambda: _chart.feishu_chart_area, ('["1月","2月"]', '{"A":[1,2]}'), {}),
        (lambda: _chart.feishu_chart_stacked_area, ('["Q1","Q2"]', '{"a":[1,2],"b":[2,3]}'), {"percent": True}),
        (lambda: _chart.feishu_chart_column, ('["研发","市场"]', "[42,28]"), {"highlight": 0}),
        (lambda: _chart.feishu_chart_bar, ('["华东区域","华北区域"]', "[520,310]"), {}),
        (lambda: _chart.feishu_chart_grouped_column, ('["Q1","Q2"]', '{"计划":[1,2],"实际":[2,3]}'), {}),
        (lambda: _chart.feishu_chart_stacked_column, ('["Q1","Q2"]', '{"a":[1,2],"b":[2,3]}'), {}),
        (lambda: _chart.feishu_chart_waterfall, ('["期初","新签","流失"]', "[500,220,-90]"), {}),
        (lambda: _chart.feishu_chart_histogram, ("[1,2,2,3,4,5,6]",), {}),
        (lambda: _chart.feishu_chart_box, ('{"研发":[1,2,3],"市场":[2,3,4]}',), {}),
        (lambda: _chart.feishu_chart_scatter, ("[[1,2],[3,4],[5,7]]",), {}),
        (lambda: _chart.feishu_chart_bubble, ("[[1,2,10],[3,4,20]]",), {"size_label": "规模"}),
        (lambda: _chart.feishu_chart_heatmap, ('["周一","周二"]', '["上午","下午"]', "[[1,2],[3,4]]"), {}),
        (lambda: _chart.feishu_chart_radar, ('["技术","沟通","交付"]', '{"张三":[4,3,5]}'), {"max_value": 5}),
        (lambda: _chart.feishu_chart_pareto, ('["A","B","C"]', "[120,85,10]"), {}),
        (lambda: _chart.feishu_chart_combo, ('["1月","2月"]', '{"营收":[1,2]}', '{"毛利率":[30,35]}'), {}),
        (
            lambda: _chart.feishu_chart_gantt,
            ('[{"name":"开发","start":"2026-08-01","days":5,"group":"研发"}]',),
            {"today": "2026-08-03"},
        ),
        (lambda: _chart.feishu_chart_progress, ('{"华东":118,"华北":92}',), {"target": 100}),
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
    result = json.loads(await _chart.feishu_chart_pie('["a","b"]', "[1,2,3]"))
    assert result["ok"] is False
    assert "2 labels but 3 values" in result["message"]


@pytest.mark.asyncio
async def test_scatter_accepts_grouped_input() -> None:
    result = json.loads(await _chart.feishu_chart_scatter('{"直营":[[1,2],[3,4]],"加盟":[[2,1],[4,3]]}'))
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_bubble_rejects_label_count_mismatch() -> None:
    result = json.loads(await _chart.feishu_chart_bubble("[[1,2,3]]", labels_json='["a","b"]'))
    assert result["ok"] is False
    assert "2 labels but 1 bubbles" in result["message"]


@pytest.mark.asyncio
async def test_chart_filename_includes_type_and_title() -> None:
    result = json.loads(await _chart.feishu_chart_pie('["a"]', "[1]", title="人力占比"))
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
        await _chart.feishu_chart_pie(
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
    result = json.loads(await _chart.feishu_chart_pie('["研发","市场"]', "[3,1]", document_id="doc1"))
    assert result["ok"] is False
    # the rendered chart is still usable — say so rather than implying total failure
    assert await anyio.Path(result["image_path"]).exists()
    assert "usable" in result["hint"]


@pytest.mark.asyncio
async def test_pie_reports_folded_slices_to_the_caller() -> None:
    labels = json.dumps([f"部门{i}" for i in range(9)], ensure_ascii=False)
    values = json.dumps(list(range(1, 10)))
    result = json.loads(await _chart.feishu_chart_pie(labels, values))
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
