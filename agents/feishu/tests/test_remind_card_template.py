"""remind-card DSL 模板结构钉 —— 14:30 填报提醒卡,只含词汇表元素。"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
TEMPLATE = SKILLS_DIR / "card-dsl" / "templates" / "remind-card.xml"


def _template_text() -> str:
    assert TEMPLATE.is_file(), "missing remind-card.xml template"
    return TEMPLATE.read_text(encoding="utf-8")


def test_template_uses_only_vocabulary_elements() -> None:
    """模板只许用 DSL 词汇表元素(card/info), 别引入未定义标签。"""
    text = _template_text()
    for tag in ("card", "info"):
        assert f"<{tag}" in text, f"missing <{tag}>"
    assert "<button" not in text, "提醒卡不需要回调按钮(button 是回调不是跳转)"
    assert "<list" not in text, "提醒卡不用待办列表"


def test_template_placeholders_are_declared() -> None:
    text = _template_text()
    for ph in ("{name}", "{hint}", "{board_link}"):
        assert ph in text, f"missing placeholder {ph}"


def test_template_renders_a_schema2_card_with_clickable_link() -> None:
    card_dsl = importlib.import_module("_card_dsl")

    out = card_dsl.render_template(
        template_name="remind-card",
        values_json=json.dumps(
            {
                "name": "张三",
                "hint": "按三层结构写,每条带时间与标准",
                "board_link": "https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc",
            }
        ),
        context_json="{}",
        round_=0,
        handler_overrides_json="{}",
    )
    assert out.get("ok"), f"render failed: {out}"
    card = out["card"]
    assert card["schema"] == "2.0"
    elements = card["body"]["elements"]
    assert len(elements) == 3, "提醒卡固定三行(提醒/要求/看板链接)"
    assert all(e["tag"] == "markdown" for e in elements)
    last = elements[-1]["content"]
    assert "[点这里填写](https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc)" in last, (
        "看板链接必须渲染成可点 markdown 链接"
    )
