"""feishu_todo_compare_card_send 结构钉 —— 16:00 对比卡片布局固定,不随 AI 漂移。"""

from __future__ import annotations

import importlib
import json


def _build(mod, **kwargs):
    rows = kwargs.pop("rows", [{"member": "张三", "prev": "1. a 2. b", "curr": "1. a 2. c", "status": "b→搞定;c 新开"}])
    align_notes = kwargs.pop("align_notes", "")
    mentor = kwargs.pop("mentor_name", "孙逊")
    cycle = kwargs.pop("cycle_date", "9.7")
    return mod._build_card_json(mentor, cycle, rows, align_notes)


def test_card_uses_schema2_and_single_markdown_element() -> None:
    mod = importlib.import_module("feishu_todo_compare_card_send")
    card = _build(mod)
    assert card["schema"] == "2.0"
    elements = card["body"]["elements"]
    assert len(elements) == 1, "卡片只允许一个 markdown 组件(表格),不许加戏"
    assert elements[0]["tag"] == "markdown"


def test_card_title_pins_group_and_cycle() -> None:
    mod = importlib.import_module("feishu_todo_compare_card_send")
    card = _build(mod, mentor_name="孙逊", cycle_date="9.7")
    assert card["header"]["title"]["content"] == "TODO 前后对比 · 孙逊组(9.7期)"


def test_table_columns_are_fixed() -> None:
    mod = importlib.import_module("feishu_todo_compare_card_send")
    card = _build(mod)
    markdown = card["body"]["elements"][0]["content"]
    assert "| 成员 | 上期 | 本期 | 搞定情况 |" in markdown
    assert "|---|---|---|---|" in markdown
    assert "mentor" not in markdown.split("| 成员 ")[1].split("\n")[1], "表格不该出现 mentor 列"


def test_reminder_notes_and_board_link_are_placed_below_table() -> None:
    mod = importlib.import_module("feishu_todo_compare_card_send")
    card = _build(mod, align_notes="王五|缺对齐依据")
    markdown = card["body"]["elements"][0]["content"]
    assert markdown.startswith("请检查你手下成员的当期填报是否合理:")
    table_pos = markdown.index("| 成员 |")
    assert markdown.index("**对齐存疑**") > table_pos
    assert markdown.index("看板表: https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc") > table_pos


def test_cell_content_escapes_pipes_and_newlines() -> None:
    mod = importlib.import_module("feishu_todo_compare_card_send")
    assert mod._md_cell("a|b") == "a\\|b"
    assert mod._md_cell("x\ny") == "x<br>y"


def test_tool_rejects_missing_receiver_and_bad_rows() -> None:
    f = importlib.import_module("_feishu_impl")
    err = json.loads(f.dumps_result(f._error("receive_id is required (the mentor's open_id).")))
    assert err["ok"] is False
    err2 = json.loads(f.dumps_result(f._error("rows_json must be valid JSON: x")))
    assert "JSON" in err2["message"]
