"""Markdown → native Feishu docx blocks (``_feishu_md``).

Covers the routing decision (rich Markdown vs plain prose), the normalisation that gets
Markdown into a shape the converter actually reads as tables/paragraphs, the two payload
fixes the convert→descendant pair needs (``merge_info`` stripped, ``header_row`` set),
the 1000-block batching, and the clip-and-grow path for a table too big for one call.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_md: Any = importlib.import_module("_feishu_md")


def _cell(cid: str, text_id: str) -> dict[str, Any]:
    return {"block_id": cid, "block_type": 32, "children": [text_id], "parent_id": "tbl"}


def _text(tid: str, content: str) -> dict[str, Any]:
    return {
        "block_id": tid,
        "block_type": 2,
        "parent_id": "cell",
        "text": {"elements": [{"text_run": {"content": content}}]},
    }


def _table_payload(rows: int, columns: int, *, table_id: str = "tbl") -> list[dict[str, Any]]:
    """A converted table's blocks, shaped the way ``blocks/convert`` returns them."""
    cells = [f"c{r}_{c}" for r in range(rows) for c in range(columns)]
    blocks: list[dict[str, Any]] = [
        {
            "block_id": table_id,
            "block_type": 31,
            "children": list(cells),
            "parent_id": "",
            "table": {
                "cells": list(cells),
                "property": {
                    "row_size": rows,
                    "column_size": columns,
                    "column_width": [100] * columns,
                    # convert always emits this; /descendant rejects it (1770001).
                    "merge_info": [{"col_span": 1, "row_span": 1} for _ in cells],
                },
            },
        }
    ]
    for cid in cells:
        blocks.append(_cell(cid, f"{cid}_t"))
        blocks.append(_text(f"{cid}_t", f"v{cid}"))
    return blocks


# ── Routing: which content needs Feishu's converter ───────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        "| 姓名 | 部门 |\n| --- | --- |\n| 张三 | 研发 |",
        "- 一\n- 二",
        "* 一\n* 二",
        "1. 一\n2. 二",
        "- [ ] 待办",
        "> 引用",
        "```python\nprint(1)\n```",
        "---",
        "带 **加粗** 的一段",
        "带 `行内码` 的一段",
        "带 ~~删除线~~ 的一段",
        "看 [链接](https://example.com)",
        "![图](https://example.com/x.png)",
        # Single-marker emphasis: used to fall through to the local path and land in the
        # document as literal "*斜体*".
        "带 *斜体* 的一段",
        "带 _斜体_ 的一段",
        # A borderless table (valid GFM) — nothing used to match it.
        "模块 | 状态\n--- | ---\nA | 好",
        # Emphasis whose run spans a soft line break.
        "这里**加粗\n跨行**结束",
    ],
)
def test_has_rich_markdown_detects_native_constructs(content: str) -> None:
    assert _md.has_rich_markdown(content) is True


@pytest.mark.parametrize(
    "content",
    [
        "# 标题\n正文一段。\n正文两段。",
        "###### 六级标题",
        "纯文本没有任何标记。",
        "含 - 连字符但不是列表",
        "价格是 3*4 元",
        "",
        # Underscores inside identifiers are not emphasis: dunders and snake_case are
        # ordinary words in a technical doc, and styling them would eat the underscores.
        "变量 __init__ 与 snake_case_name 混排",
        "字段 note_1 和 note_2 都要填",
        # Lone markers, and an opener with no closer.
        "乘法 2 * 3 * 4",
        "半 *开头没闭合",
        "范围 10*20*30 米",
    ],
)
def test_has_rich_markdown_leaves_plain_prose_local(content: str) -> None:
    assert _md.has_rich_markdown(content) is False


# ── Normalising what the converter is fussy about ─────────────────────────────


def test_normalize_separates_prose_lines_into_paragraphs() -> None:
    """One-paragraph-per-line prose must not collapse: convert joins single newlines."""
    out = _md.normalize_for_convert("第一段结论。\n第二段说明**很重要**。\n第三段补充。")
    assert out == "第一段结论。\n\n第二段说明**很重要**。\n\n第三段补充。"


def test_normalize_keeps_lines_of_one_construct_together() -> None:
    """List items, quote lines and a heading's own line get no blank line wedged in."""
    assert _md.normalize_for_convert("- 一\n- 二\n- 三") == "- 一\n- 二\n- 三"
    assert _md.normalize_for_convert("> 第一行\n> 第二行") == "> 第一行\n> 第二行"
    assert _md.normalize_for_convert("1. 一\n2. 二") == "1. 一\n2. 二"


def test_normalize_does_not_break_an_emphasis_run_across_lines() -> None:
    """A blank line inside "**加粗\\n跨行**" would turn the markers into literal asterisks."""
    out = _md.normalize_for_convert("这里**加粗\n跨行**结束\n下一段。")
    assert out == "这里**加粗\n跨行**结束\n\n下一段。"


def test_normalize_leaves_fenced_code_verbatim() -> None:
    """Inside a fence a blank line is content and a pipe is a pipe."""
    src = "说明:\n```python\nx = 1\n\ny = 2\n```\n结束"
    out = _md.normalize_for_convert(src)
    assert "```python\nx = 1\n\ny = 2\n```" in out
    # The prose after the fence still becomes its own paragraph.
    assert out.endswith("```\n\n结束")


def test_normalize_pads_a_ragged_table() -> None:
    """A delimiter row that disagrees with the header means "not a table" to CommonMark."""
    out = _md.normalize_for_convert("| a | b | c |\n| --- | --- |\n| 1 | 2 | 3 |")
    assert out == "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |"


def test_normalize_pads_short_body_rows() -> None:
    out = _md.normalize_for_convert("| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |")
    assert out.splitlines()[-1] == "| 1 | 2 |  |"


def test_normalize_inserts_a_missing_delimiter_row() -> None:
    """Two pipe rows with nothing between them is not a table either."""
    out = _md.normalize_for_convert("| a | b |\n| 1 | 2 |")
    assert out == "| a | b |\n| --- | --- |\n| 1 | 2 |"


def test_normalize_leaves_a_lone_pipe_row_alone() -> None:
    """One row is not a table: inventing a header would invent structure."""
    assert _md.normalize_for_convert("| 只有一行 |") == "| 只有一行 |"


def test_normalize_keeps_a_well_formed_table_parseable() -> None:
    out = _md.normalize_for_convert("| a | b |\n|:---|---:|\n| 1 | 2 |")
    assert _md._delimited_table_count(out) == 1
    assert out.splitlines()[0] == "| a | b |"


def test_normalize_does_not_touch_pipes_inside_a_fence() -> None:
    src = "```\n| a | b |\n| 1 | 2 |\n```"
    assert _md.normalize_for_convert(src) == src


def test_repair_table_ignores_the_delimiter_when_sizing() -> None:
    """The delimiter is the row most likely to be wrong, so it never sets the width."""
    out = _md.repair_table(["| a | b | c |", "| --- |", "| 1 | 2 | 3 |"])
    assert out == ["| a | b | c |", "| --- | --- | --- |", "| 1 | 2 | 3 |"]


# ── Payload fixes: what convert emits vs what /descendant accepts ─────────────


def test_sanitize_strips_merge_info_and_parent_id() -> None:
    blocks = _md.sanitize_converted(_table_payload(2, 2))
    table = blocks[0]
    assert "merge_info" not in table["table"]["property"]
    assert all("parent_id" not in b for b in blocks)
    # The table's children must survive: dropping them gives 1770041 open schema mismatch.
    assert len(table["children"]) == 4


def test_sanitize_sets_header_row() -> None:
    blocks = _md.sanitize_converted(_table_payload(2, 2))
    assert blocks[0]["table"]["property"]["header_row"] is True
    unset = _md.sanitize_converted(_table_payload(2, 2), header_row=False)
    assert "header_row" not in unset[0]["table"]["property"]


def test_sanitize_keeps_non_table_blocks_intact() -> None:
    src = [{"block_id": "p1", "block_type": 2, "parent_id": "root", "text": {"elements": []}}]
    out = _md.sanitize_converted(src)
    assert out == [{"block_id": "p1", "block_type": 2, "text": {"elements": []}}]


# ── Batching under the measured 1000-block ceiling ────────────────────────────


def test_group_batches_keeps_whole_first_level_blocks_together() -> None:
    blocks = [
        *_md.sanitize_converted(_table_payload(3, 3)),
        {"block_id": "p1", "block_type": 2, "text": {"elements": []}},
    ]
    by_id = {b["block_id"]: b for b in blocks}
    # A 3x3 table is 1 + 2*9 = 19 blocks, so a cap of 19 holds the table alone.
    batches = _md.group_into_batches(["tbl", "p1"], by_id, max_blocks=19)
    assert [ids for ids, _ in batches] == [["tbl"], ["p1"]]
    assert len(batches[0][1]) == 19


def test_group_batches_never_splits_one_subtree() -> None:
    blocks = _md.sanitize_converted(_table_payload(3, 3))
    by_id = {b["block_id"]: b for b in blocks}
    # Cap below the table's own size: it comes back whole (oversized), not cut in half.
    batches = _md.group_into_batches(["tbl"], by_id, max_blocks=5)
    assert len(batches) == 1
    assert len(batches[0][1]) == 19


def test_group_batches_packs_many_small_blocks() -> None:
    blocks = [{"block_id": f"p{i}", "block_type": 2, "text": {"elements": []}} for i in range(250)]
    by_id = {b["block_id"]: b for b in blocks}
    batches = _md.group_into_batches([b["block_id"] for b in blocks], by_id, max_blocks=100)
    assert [len(bs) for _, bs in batches] == [100, 100, 50]


def test_subtree_follows_cells_and_children() -> None:
    blocks = _md.sanitize_converted(_table_payload(2, 2))
    by_id = {b["block_id"]: b for b in blocks}
    ids = [b["block_id"] for b in _md.subtree("tbl", by_id)]
    assert ids[0] == "tbl"
    assert set(ids) == {"tbl", *[f"c{r}_{c}" for r in range(2) for c in range(2)]} | {
        f"c{r}_{c}_t" for r in range(2) for c in range(2)
    }


# ── Clip-and-grow for a table over the ceiling ────────────────────────────────


def test_rows_that_fit_leaves_room_for_the_table_block() -> None:
    # 1 block for the table + 2 per cell.
    assert _md.rows_that_fit(12, max_blocks=1000) == 41
    assert _md.rows_that_fit(2, max_blocks=21) == 5
    # Never zero, even for an absurdly wide table.
    assert _md.rows_that_fit(5000, max_blocks=1000) == 1


def test_clip_table_splits_head_and_tail_text() -> None:
    blocks = _md.sanitize_converted(_table_payload(4, 2))
    by_id = {b["block_id"]: b for b in blocks}
    descendants, tail = _md.clip_table(by_id["tbl"], 2, by_id)
    head = descendants[0]
    assert head["table"]["property"]["row_size"] == 2
    assert head["table"]["cells"] == head["children"] == ["c0_0", "c0_1", "c1_0", "c1_1"]
    # 1 table + 2 rows x 2 cols x (cell + text)
    assert len(descendants) == 9
    assert tail == [["vc2_0", "vc2_1"], ["vc3_0", "vc3_1"]]


def test_cell_text_reads_the_paragraph_inside_the_cell() -> None:
    blocks = _md.sanitize_converted(_table_payload(1, 1))
    by_id = {b["block_id"]: b for b in blocks}
    assert _md.cell_text("c0_0", by_id) == "vc0_0"
    assert _md.cell_text("missing", by_id) == ""


def test_fill_requests_target_the_cells_text_child() -> None:
    reqs = _md.fill_requests(["cellA", "cellB"], ["x", "y"], {"cellA": ["tA"], "cellB": ["tB"]})
    assert [r["block_id"] for r in reqs] == ["tA", "tB"]
    assert reqs[0]["update_text_elements"]["elements"][0]["text_run"]["content"] == "x"


def test_fill_requests_skip_blank_text_and_childless_cells() -> None:
    reqs = _md.fill_requests(["a", "b", "c"], ["", "y", "z"], {"a": ["ta"], "b": ["tb"], "c": []})
    assert [r["block_id"] for r in reqs] == ["tb"]


# ── Request builders ─────────────────────────────────────────────────────────


def test_convert_request_shape() -> None:
    req = _md.build_convert_request("# H")
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/docx/v1/documents/blocks/convert"
    assert req.body == {"content_type": "markdown", "content": "# H"}


def test_insert_row_request_appends_by_default() -> None:
    req = _md.build_insert_row_request("doc1", "tbl1")
    assert req.http_method.name == "PATCH"
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    assert req.paths["block_id"] == "tbl1"
    assert req.body == {"insert_table_row": {"row_index": -1}}


def test_batch_update_request_shape() -> None:
    req = _md.build_batch_update_request("doc1", [{"block_id": "b1"}])
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks/batch_update"
    assert req.paths["document_id"] == "doc1"
    assert req.body == {"requests": [{"block_id": "b1"}]}


def test_real_block_id_resolves_temporary_ids() -> None:
    res = {"data": {"block_id_relations": [{"temporary_block_id": "tmp", "block_id": "doxcnReal"}]}}
    assert _impl.real_block_id(res, "tmp") == "doxcnReal"
    assert _impl.real_block_id(res, "other") == ""
    assert _impl.real_block_id({"data": {}}, "tmp") == ""


# ── append_markdown / routing ─────────────────────────────────────────────────


class _FakeFeishu:
    """Stand-in for the two endpoints, recording every request that would be sent."""

    def __init__(self, blocks: list[dict[str, Any]], first_level: list[str]) -> None:
        self.blocks = blocks
        self.first_level = first_level
        self.sent: list[Any] = []
        self.fail_on: str = ""

    async def __call__(
        self,
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        req = request() if callable(request) else request
        self.sent.append(req)
        uri = getattr(req, "uri", "")
        if self.fail_on and self.fail_on in uri:
            return {"ok": False, "code": 1770001, "msg": "invalid param", "message": "invalid param", "data": {}}
        if uri.endswith("/blocks/convert"):
            return {
                "ok": True,
                "code": 0,
                "msg": "",
                "data": {"blocks": self.blocks, "first_level_block_ids": self.first_level},
            }
        if uri.endswith("/descendant"):
            body = getattr(req, "body", {}) or {}
            rel = [
                {"temporary_block_id": str(b.get("block_id")), "block_id": f"real_{b.get('block_id')}"}
                for b in body.get("descendants", [])
            ]
            return {"ok": True, "code": 0, "msg": "", "data": {"block_id_relations": rel}}
        return {"ok": True, "code": 0, "msg": "", "data": {}}

    def uris(self) -> list[str]:
        return [getattr(r, "uri", "") for r in self.sent]


@pytest.mark.asyncio
async def test_append_markdown_converts_then_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = [*_table_payload(2, 2), {"block_id": "p1", "block_type": 2, "parent_id": "", "text": {"elements": []}}]
    fake = _FakeFeishu(blocks, ["tbl", "p1"])
    monkeypatch.setattr(_impl, "_invoke", fake)
    res = await _md.append_markdown("doc1", "| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert res["ok"] is True
    assert res["native_markdown"] is True
    assert res["added"] == 2
    assert fake.uris() == [
        "/open-apis/docx/v1/documents/blocks/convert",
        "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/descendant",
    ]
    body = fake.sent[1].body
    assert body["children_id"] == ["tbl", "p1"]
    # The payload that goes out is the sanitized one, not what convert handed us.
    table = next(b for b in body["descendants"] if b["block_type"] == 31)
    assert "merge_info" not in table["table"]["property"]
    assert table["table"]["property"]["header_row"] is True


@pytest.mark.asyncio
async def test_append_markdown_reports_partial_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    blocks: list[dict[str, Any]] = [
        {"block_id": f"p{i}", "block_type": 2, "parent_id": "", "text": {"elements": []}} for i in range(5)
    ]
    fake = _FakeFeishu(blocks, [str(b["block_id"]) for b in blocks])
    monkeypatch.setattr(_impl, "_invoke", fake)
    monkeypatch.setattr(_md, "DESCENDANT_MAX_BLOCKS", 2)

    calls = {"n": 0}
    original = fake.__call__

    async def flaky(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        if getattr(req, "uri", "").endswith("/descendant"):
            calls["n"] += 1
            if calls["n"] == 2:
                return {"ok": False, "code": 1, "msg": "boom", "message": "boom", "data": {}}
        return await original(request, **kwargs)

    monkeypatch.setattr(_impl, "_invoke", flaky)
    res = await _md.append_markdown("doc1", "- 一\n- 二")
    assert res["ok"] is False
    # The first batch landed; the caller is told how much, rather than being left guessing.
    assert res["blocks_written"] == 2


@pytest.mark.asyncio
async def test_append_markdown_surfaces_convert_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeFeishu([], [])
    fake.fail_on = "/blocks/convert"
    monkeypatch.setattr(_impl, "_invoke", fake)
    res = await _md.append_markdown("doc1", "- 一")
    assert res["ok"] is False
    assert fake.uris() == ["/open-apis/docx/v1/documents/blocks/convert"]


@pytest.mark.asyncio
async def test_append_markdown_rejects_empty_convert_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _FakeFeishu([], []))
    res = await _md.append_markdown("doc1", "- 一")
    assert res["ok"] is False
    assert "no blocks" in res["message"]


@pytest.mark.asyncio
async def test_append_markdown_requires_document_id() -> None:
    res = await _md.append_markdown("  ", "- 一")
    assert res["ok"] is False
    assert "document_id" in res["message"]


@pytest.mark.asyncio
async def test_convert_sends_the_normalized_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """The converter receives the repaired text, not the user's ragged original."""
    blocks = _table_payload(2, 2)
    fake = _FakeFeishu(blocks, ["tbl"])
    monkeypatch.setattr(_impl, "_invoke", fake)
    await _md.convert_markdown("| a | b | c |\n| --- | --- |\n| 1 | 2 | 3 |")
    sent = fake.sent[0].body["content"]
    assert sent == "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |"


@pytest.mark.asyncio
async def test_convert_reports_a_table_that_stayed_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    """convert answers ok with a paragraph of pipes; that must not pass silently."""
    paragraph = [{"block_id": "p1", "block_type": 2, "parent_id": "", "text": {"elements": []}}]
    monkeypatch.setattr(_impl, "_invoke", _FakeFeishu(paragraph, ["p1"]))
    res = await _md.convert_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert res["ok"] is True
    assert res["tables_not_converted"] == 1


@pytest.mark.asyncio
async def test_convert_is_quiet_when_the_table_did_convert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _FakeFeishu(_table_payload(2, 2), ["tbl"]))
    res = await _md.convert_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert "tables_not_converted" not in res


@pytest.mark.asyncio
async def test_append_markdown_notes_an_unconverted_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The write still happens — but the result says the table is plain text."""
    paragraph = [{"block_id": "p1", "block_type": 2, "parent_id": "", "text": {"elements": []}}]
    monkeypatch.setattr(_impl, "_invoke", _FakeFeishu(paragraph, ["p1"]))
    res = await _md.append_markdown("doc1", "| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert res["ok"] is True
    assert res["tables_not_converted"] == 1
    assert "表格" in res["note"]


@pytest.mark.asyncio
async def test_oversized_table_is_clipped_then_grown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A table over the cap: create the rows that fit, insert the rest, fill their text."""
    blocks = _table_payload(4, 2)
    fake = _FakeFeishu(blocks, ["tbl"])
    monkeypatch.setattr(_impl, "_invoke", fake)
    # Cap of 9 = 1 table + 2 rows x 2 cols x 2 blocks, so rows 3-4 must be grown.
    monkeypatch.setattr(_md, "DESCENDANT_MAX_BLOCKS", 9)

    grown_cells = [f"c{r}_{c}" for r in range(4) for c in range(2)]

    async def fake_list(document_id: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
        listed: list[dict[str, Any]] = [
            {"block_id": "real_tbl", "block_type": 31, "table": {"cells": grown_cells, "property": {}}}
        ]
        for cid in grown_cells:
            listed.append({"block_id": cid, "block_type": 32, "children": [f"{cid}_t"]})
        return {"ok": True, "blocks": listed}

    monkeypatch.setattr(_impl, "list_all_blocks", fake_list)

    res = await _md.append_markdown("doc1", "| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert res["ok"] is True
    assert res["table_rows"] == 4
    assert res["table_grown_rows"] == 2

    uris = fake.uris()
    assert uris[0].endswith("/blocks/convert")
    assert uris[1].endswith("/descendant")
    # Two rows short → two single-row PATCHes (batch_update can't carry insert_table_row).
    assert uris.count("/open-apis/docx/v1/documents/:document_id/blocks/:block_id") == 2
    assert uris[-1].endswith("/blocks/batch_update")

    created = fake.sent[1].body
    assert created["descendants"][0]["table"]["property"]["row_size"] == 2
    filled = fake.sent[-1].body["requests"]
    # The grown rows' text is re-applied to the cells Feishu created (rows 3-4, 2 cols).
    assert [r["block_id"] for r in filled] == ["c2_0_t", "c2_1_t", "c3_0_t", "c3_1_t"]
    assert filled[0]["update_text_elements"]["elements"][0]["text_run"]["content"] == "vc2_0"


@pytest.mark.asyncio
async def test_oversized_non_table_block_is_reported_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    # A single huge non-table block can't be split by any API we have.
    blocks = [{"block_id": "code1", "block_type": 14, "parent_id": "", "code": {"elements": []}}]
    fake = _FakeFeishu(blocks, ["code1"])
    monkeypatch.setattr(_impl, "_invoke", fake)
    monkeypatch.setattr(_md, "DESCENDANT_MAX_BLOCKS", 0)
    res = await _md.append_markdown("doc1", "```\nx\n```")
    assert res["ok"] is False
    assert "only tables can be" in res["message"]
    assert res["blocks_written"] == 0


@pytest.mark.asyncio
async def test_append_doc_content_routes_rich_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    async def fake_append(document_id: str, content: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
        called.update(document_id=document_id, content=content, user_key=user_key, identity=identity)
        return {"ok": True, "native_markdown": True}

    monkeypatch.setattr(_md, "append_markdown", fake_append)
    res = await _impl.append_doc_content_impl("doc1", "| a |\n| --- |\n| 1 |", "ou_x", "user")
    assert res == {"ok": True, "native_markdown": True}
    assert called["document_id"] == "doc1"
    assert called["user_key"] == "ou_x"
    assert called["identity"] == "user"


@pytest.mark.asyncio
async def test_append_doc_content_keeps_plain_prose_on_local_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("plain prose must not hit the convert endpoint")

    monkeypatch.setattr(_md, "append_markdown", boom)
    sent: list[Any] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        sent.append(request() if callable(request) else request)
        return {"ok": True, "code": 0, "msg": "", "data": {}}

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    res = await _impl.append_doc_content_impl("doc1", "# 标题\n正文。")
    assert res["ok"] is True
    assert res["added"] == 2
    assert sent[0].uri.endswith("/blocks/:block_id/children")


@pytest.mark.asyncio
async def test_append_doc_content_still_rejects_empty() -> None:
    res = await _impl.append_doc_content_impl("doc1", "\n\n   \n")
    assert res["ok"] is False
    assert "empty" in res["message"]
