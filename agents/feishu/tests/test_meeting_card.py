from __future__ import annotations

# Local meeting tools intentionally load from the agent package rather than an installed package.
# ruff: noqa: E402, RUF001 (用例正文是中文会议文案, 全角标点是内容本身)
import json
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _card_dsl  # ty: ignore[unresolved-import]
import _meeting_card as cardmod  # ty: ignore[unresolved-import]


def _render(card_xml: str) -> dict[str, Any]:
    result = _card_dsl.render_card(card_xml=card_xml)
    assert result.get("ok"), result.get("error")
    return result


def _element_texts(card: dict[str, Any]) -> list[str]:
    """卡片里所有可读文本: 普通 markdown, 以及折叠面板的标题与面板内正文。"""
    texts: list[str] = []
    for element in card["body"]["elements"]:
        if element.get("tag") == "markdown":
            texts.append(str(element.get("content", "")))
        elif element.get("tag") == "collapsible_panel":
            texts.append(str(((element.get("header") or {}).get("title") or {}).get("content", "")))
            texts.extend(str(item.get("content", "")) for item in element.get("elements", []))
    return texts


def test_section_and_divider_compile_to_markdown_and_hr() -> None:
    result = _render(
        '<card title="测试" template="blue">'
        '<section title="小节" text="正文 **粗体**"/>'
        "<divider/>"
        '<section text="无标题段落"/>'
        '<section title="只有标题"/>'
        "</card>"
    )
    card = result["card"]
    elements = card["body"]["elements"]
    assert elements[0] == {"tag": "markdown", "content": "**小节**\n\n正文 **粗体**"}
    assert elements[1] == {"tag": "hr"}
    assert elements[2] == {"tag": "markdown", "content": "无标题段落"}
    assert elements[3] == {"tag": "markdown", "content": "**只有标题**"}
    assert len(elements) == 4


def test_section_empty_placeholder_vanishes() -> None:
    result = _render('<card title="测试" template="blue"><section title="" text=""/><section text="有内容"/></card>')
    assert _element_texts(result["card"]) == ["有内容"]


def test_template_renders_full_summary_card() -> None:
    values = {
        "meeting_line": "周中对齐会 · 2026-09-08",
        "summary": "C 端发布定于 9 月 9 日。",
        "key_points": "孙逊要求分层汇报。",
        "positives": "- P-1 孙逊肯定汇报准备",
        "negatives": "- N-1 汇报准备不足",
        "footer": "候选观察 · 不计分",
    }
    result = _card_dsl.render_template("meeting-summary-card", values_json=json.dumps(values, ensure_ascii=False))
    assert result.get("ok"), result.get("error")
    card = result["card"]
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "会议总结"
    elements = card["body"]["elements"]
    assert [element.get("tag") for element in elements].count("hr") == 2

    # 首屏: 元信息 + 摘要保持可见
    assert elements[0] == {"tag": "markdown", "content": "周中对齐会 · 2026-09-08"}
    assert any(
        element.get("tag") == "markdown"
        and "**会议摘要**" in str(element.get("content"))
        and "C 端发布定于" in str(element.get("content"))
        for element in elements
    )

    # 长段落进折叠面板: 默认收起, 正文**完整**在面板里(这是"内容不再被截断"的落点)
    panels = [element for element in elements if element.get("tag") == "collapsible_panel"]
    assert len(panels) == 3
    assert all(panel["expanded"] is False for panel in panels), "折叠面板必须默认收起"
    assert [panel["header"]["title"]["content"] for panel in panels] == [
        "关键决定与分析（点开看全文）",
        "✅ 正面候选（点开看全文）",
        "⚠️ 负面候选（点开看全文）",
    ]
    bodies = [panel["elements"][0]["content"] for panel in panels]
    assert "分层汇报" in bodies[0]
    assert "P-1" in bodies[1]
    assert "N-1" in bodies[2]
    assert any("候选观察 · 不计分" in text for text in _element_texts(card))


def test_collapse_compiles_to_a_collapsed_panel() -> None:
    """<collapse> → 飞书 collapsible_panel: 默认收起, 正文一个字不丢。"""
    result = _render(
        '<card title="测试" template="blue">'
        '<section text="首屏"/>'
        '<collapse title="点开看全文" text="完整正文, 一个字不丢"/>'
        '<collapse title="显式展开" text="正文" expanded="true"/>'
        "</card>"
    )
    elements = result["card"]["body"]["elements"]
    panel = elements[1]
    assert panel["tag"] == "collapsible_panel"
    assert panel["expanded"] is False, "默认必须收起, 否则首屏又被长文撑满"
    assert panel["header"]["title"] == {"tag": "markdown", "content": "点开看全文"}
    assert panel["elements"] == [{"tag": "markdown", "content": "完整正文, 一个字不丢"}]
    assert elements[2]["expanded"] is True


def test_collapse_without_body_is_skipped() -> None:
    result = _render('<card title="测试" template="blue"><collapse title="只有标题"/><section text="有内容"/></card>')
    assert [element.get("tag") for element in result["card"]["body"]["elements"]] == ["markdown"]


def test_unknown_dsl_element_message_lists_collapse() -> None:
    result = _card_dsl.render_card(card_xml='<card title="测试" template="blue"><bogus/></card>')
    assert not result.get("ok")
    assert "collapse" in str(result.get("error"))


def test_render_meeting_summary_card_keeps_full_content() -> None:
    """正文按内容原样进卡: 不再按 700/1500 字硬截断(2026-09-12 起改折叠面板)。"""
    overview = json.dumps(
        {
            "declaration": "以下为候选观察, 不进入正式正负面总表。",
            "positive_candidates": [
                {
                    "id": "P-1",
                    "scope": "正式会议",
                    "candidate": "孙逊公开肯定汇报",
                    "evidence": "原话证据",
                    "evidence_kind": "现场陈述",
                }
            ],
            "negative_candidates": [],
        },
        ensure_ascii=False,
    )
    analysis = {
        "analysis_text": "要点" * 3000,
        "meeting_summary": "摘要内容",
        "positive_negative_overview": overview,
    }
    result = cardmod.render_meeting_summary_card("周中对齐会", "57152787045", "2026-09-08", analysis)
    assert result.get("ok"), result.get("error")
    values = result["values"]
    assert values["meeting_line"] == "周中对齐会 · 2026-09-08"
    assert "摘要内容" in values["summary"]
    assert values["key_points"].count("要点") == 3000, "6000 字的分析不该被截断"
    assert cardmod._card_bytes(result["card"]) <= cardmod.CARD_BYTE_BUDGET
    assert "P-1" in values["positives"] and "孙逊公开肯定汇报" in values["positives"]
    assert values["positives"].count("证据: ") == 1
    assert values["footer"].startswith("以下为候选观察")
    assert "候选观察: 不进入正式正负面总表" in values["footer"]


def test_real_meeting_sizes_fit_without_any_truncation() -> None:
    """线上真实体量必须完整进卡: 9/9 那场摘要有 1530 字、正负面概览 3624 字 ——
    这正是用户反馈"内容被截断"的那一场。"""
    analysis = {
        "meeting_summary": "摘要内容。" * 306,
        "analysis_text": "分析要点。" * 291,
        "positive_negative_overview": "【正面候选】" + "正面观察。" * 300 + "\n【负面候选】" + "负面观察。" * 300,
    }
    result = cardmod.render_meeting_summary_card("周中对齐会", "57152787045", "2026-09-09", analysis)
    assert result.get("ok"), result.get("error")
    values = result["values"]
    assert cardmod._card_bytes(result["card"]) <= cardmod.CARD_BYTE_BUDGET
    marker = cardmod._ELLIPSIS.strip()
    for key in ("summary", "key_points", "positives", "negatives"):
        assert marker not in str(values[key]), f"{key} 不该被截断"
    assert str(values["summary"]).count("摘要内容") == 306
    assert str(values["positives"]).count("正面观察") == 300
    assert str(values["negatives"]).count("负面观察") == 300


def test_card_over_budget_shrinks_only_the_longest_section() -> None:
    """超出飞书 30KB 硬上限时: 只降级**最长的那一段**, 其余保持完整。

    宁可让一段变短并注明"完整文本见会议存档", 也不让整张卡发送失败(230025)。
    """
    analysis = {
        "meeting_summary": "摘要" * 400,
        "analysis_text": "要点" * 12_000,
        "positive_negative_overview": json.dumps(
            {
                "positive_candidates": [{"id": "P-1", "candidate": "候选正文", "evidence": "证据"}],
                "negative_candidates": [],
            },
            ensure_ascii=False,
        ),
    }
    result = cardmod.render_meeting_summary_card("周中对齐会", "57152787045", "2026-09-08", analysis)
    assert result.get("ok"), result.get("error")
    values = result["values"]
    assert cardmod._card_bytes(result["card"]) <= cardmod.CARD_BYTE_BUDGET
    assert str(values["key_points"]).endswith(cardmod._ELLIPSIS), "超预算时最长的那段被降级并注明"
    assert str(values["summary"]).count("摘要") == 400, "短段落不该被牵连"
    assert "P-1" in str(values["positives"])


def test_overview_unstructured_falls_back_to_note_without_crashing() -> None:
    analysis = {
        "analysis_text": "要点",
        "meeting_summary": "摘要内容",
        "positive_negative_overview": "模型没有输出结构化 JSON, 就是一段话" * 50,
    }
    result = cardmod.render_meeting_summary_card("周中对齐会", "57152787045", "2026-09-08", analysis)
    assert result.get("ok"), result.get("error")
    assert result["values"]["positives"] == ""
    assert result["values"]["negatives"] == ""
    assert "没有输出结构化" in result["values"]["footer"]


def test_declaration_block_is_stripped_from_card_body() -> None:
    """口径/边界声明(系统侧约束)不得复述进卡片正文。"""
    analysis = {
        "meeting_summary": (
            "【用途与边界】本输出仅为候选观察, 不写入正式负面总表、不计分、不进入绩效。\n\n"
            "会议决定把可插拔排在基础本体之前。"
        ),
        "analysis_text": (
            "【用途与边界】本分析针对白名单固定会议 42654699903。\n\n"
            "## 关键决定\n- 可插拔优先于基础本体。\n\n"
            "msop.core.02 为 active:false, 按口径不判符合/不符合。"
        ),
        "positive_negative_overview": "",
    }
    result = cardmod.render_meeting_summary_card("日会", "42654699903", "2026-09-11", analysis)
    assert result.get("ok"), result.get("error")
    values = result["values"]
    for key in ("summary", "key_points"):
        text = values[key]
        assert "用途与边界" not in text
        assert "不计分" not in text
        assert "不进入绩效" not in text
        assert "active:false" not in text
    assert "可插拔优先于基础本体" in values["key_points"]
    assert "可插拔排在基础本体之前" in values["summary"]


def test_long_paragraph_is_rendered_as_bullets() -> None:
    """长段落按句切成分行要点, 避免卡片里一大坨流水文字。"""
    paragraph = "第一句话说明结论。" * 25
    result = cardmod.render_meeting_summary_card(
        "日会",
        "42654699903",
        "2026-09-11",
        {"meeting_summary": paragraph, "analysis_text": paragraph, "positive_negative_overview": ""},
    )
    assert result.get("ok"), result.get("error")
    assert result["values"]["summary"].count("\n- ") >= 3
    assert result["values"]["key_points"].count("\n- ") >= 3


def test_medium_paragraph_is_also_bulleted() -> None:
    """100~140 字符的中等段落此前原样成行 —— 那正是"看起来还是一大段"的来源。"""
    paragraph = (
        "会议确认了发布节奏与验收口径，把风险项单独列出并要求各自给出截止时间；"
        "同时决定把可插拔排在基础本体之前，下周复核一次进度。"
    )
    assert 60 < len(paragraph) < 140
    result = cardmod.render_meeting_summary_card(
        "日会",
        "42654699903",
        "2026-09-11",
        {"meeting_summary": paragraph, "analysis_text": "", "positive_negative_overview": ""},
    )
    assert result.get("ok"), result.get("error")
    assert result["values"]["summary"].startswith("- ")
    assert "\n- " in result["values"]["summary"], "一句一行才叫松散"


def test_long_sentence_is_split_on_commas() -> None:
    """单句超过目标长度就按逗号把短分句攒成多条要点, 每行都要短。"""
    sentence = "会议确认发布节奏保持不变，风险项由各自负责人补充截止时间，下周一复核，若仍无结论则升级到组会讨论。"
    result = cardmod.render_meeting_summary_card(
        "日会",
        "42654699903",
        "2026-09-11",
        {"meeting_summary": sentence, "analysis_text": "", "positive_negative_overview": ""},
    )
    assert result.get("ok"), result.get("error")
    bullets = [line for line in result["values"]["summary"].splitlines() if line.startswith("- ")]
    assert len(bullets) >= 2, f"长句该切成多条要点: {bullets}"
    assert max(len(line) for line in bullets) <= 60, f"每条都该短: {bullets}"
    assert "".join(line[2:] for line in bullets) == sentence, "切分不许丢字"


def test_bullets_survive_into_the_rendered_card() -> None:
    """回归钉子: 引擎曾把属性里的字面换行归一成空格, 于是所有要点被压成一行。

    XML 规范要求属性值里的字面换行折成空格, 所以渲染后**必须**仍是多行 ——
    只断言 ``values`` 是不够的(那里本来就是多行, bug 发生在渲染器里)。
    """
    paragraph = "第一句话说明结论。" * 20
    result = cardmod.render_meeting_summary_card(
        "周中对齐会",
        "57152787045",
        "2026-09-11",
        {"meeting_summary": paragraph, "analysis_text": paragraph, "positive_negative_overview": ""},
    )
    assert result.get("ok"), result.get("error")
    contents = _element_texts(result["card"])
    body = [text for text in contents if "**会议摘要**" in text]
    assert body, contents
    lines = body[0].splitlines()
    assert len(lines) >= 5, f"要点必须各占一行, 实际只有 {len(lines)} 行: {body[0][:200]}"
    assert max(len(line) for line in lines) <= 120, "不该出现一整块长行"


def test_candidates_are_listed_whatever_key_the_model_used() -> None:
    """候选正文键不写死一种: 实测模型用过 ``event``, 只认 ``candidate`` 会把整栏丢光。"""
    overview = json.dumps(
        {
            "disclaimer": "候选观察, 不计分。",
            "positive_candidates": [
                {"event": "主持人当场确立等待上限规则", "evidence": "[842102] 原话", "axis": "会议时间纪律"},
            ],
            "negative_candidates": [
                {"event": "会议超时且无超时分流", "fact_layer": "可观察事实"},
                "纯字符串条目也要列出来",
            ],
        },
        ensure_ascii=False,
    )
    result = cardmod.render_meeting_summary_card(
        "日会",
        "42654699903",
        "2026-09-11",
        {"meeting_summary": "", "analysis_text": "", "positive_negative_overview": overview},
    )
    assert result.get("ok"), result.get("error")
    values = result["values"]
    assert "主持人当场确立等待上限规则" in values["positives"]
    assert "会议时间纪律" in values["positives"]
    assert "证据: " in values["positives"]
    assert "会议超时且无超时分流" in values["negatives"]
    assert "纯字符串条目也要列出来" in values["negatives"], "字符串条目不许被静默跳过"


def test_unstructured_overview_is_split_into_the_two_sections() -> None:
    """overview 不是 JSON 但有【正面候选…】小标题时, 应该分到两栏而不是塞进 footer。"""
    overview = (
        "以下均为候选观察，不写入正式正负面总表，不计分，不进入绩效。\n\n"
        "【正面候选 1】孙逊主动承担管理责任\n"
        "- 事实：孙逊说飞书应用没推动起来与自己这边的管理问题有关。\n\n"
        "【负面候选 1】行动项缺截止时间\n"
        "- 事实：多项行动项只有负责人、没有截止时间。\n"
    )
    result = cardmod.render_meeting_summary_card(
        "周中对齐会",
        "57152787045",
        "2026-09-11",
        {"meeting_summary": "", "analysis_text": "", "positive_negative_overview": overview},
    )
    assert result.get("ok"), result.get("error")
    values = result["values"]
    assert "孙逊主动承担管理责任" in values["positives"]
    assert "行动项缺截止时间" in values["negatives"]
    assert "不计分" not in values["positives"], "口径声明不该出现在正文"
    assert "候选观察" not in values["footer"] or "候选观察: 不进入" in values["footer"]


def test_code_generated_meta_and_cn_headings() -> None:
    """代码生成的"合并口径"声明与【合并后分析】横幅不进卡片; 中文序号小节变粗体标题。"""
    analysis = {
        "meeting_summary": (
            "合并口径\uff1a本分析由三段分块分析合并去重\uff0c智能纪要仅作辅助\uff1b凡仅靠单方陈述的结论标待补充证据。"
            "\n\n会议围绕架构复盘展开。"
        ),
        "analysis_text": (
            "【合并后分析\uff5c会议 42654699903\uff5cweekday-alignment-1100 日会\uff5c2026-09-11】\n\n"
            "一、证据范围与口径\n\n本分析以四段原始转写为主要证据。\n\n"
            "二、关键决定\n\n- 可插拔优先于基础本体。"
        ),
        "positive_negative_overview": "",
    }
    result = cardmod.render_meeting_summary_card("日会", "42654699903", "2026-09-11", analysis)
    assert result.get("ok"), result.get("error")
    values = result["values"]
    for key in ("summary", "key_points"):
        text = values[key]
        assert "合并口径" not in text
        assert "合并后分析" not in text
        assert "智能纪要仅作辅助" not in text
    assert "**一、证据范围与口径**" in values["key_points"]
    assert "**二、关键决定**" in values["key_points"]


@pytest.mark.anyio
async def test_notify_meeting_card_sends_then_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []

    async def fake_send(
        receive_id: str, card_json: str, receive_id_type: str, user_key: str | None = None
    ) -> dict[str, object]:
        sent.append((receive_id, card_json))
        return {"ok": True, "message_id": "om_card_1"}

    monkeypatch.setattr(cardmod._f, "send_card_impl", fake_send)
    first = json.loads(
        await cardmod.notify_meeting_card(
            meeting_name="weekday-alignment",
            recipient="ou_user_1",
            card_json='{"schema":"2.0"}',
            record_file_id="rec-1",
            appdata_root=str(tmp_path),
        )
    )
    assert first["status"] == "sent" and first["message_id"] == "om_card_1"
    assert sent == [("ou_user_1", '{"schema":"2.0"}')]

    second = json.loads(
        await cardmod.notify_meeting_card(
            meeting_name="weekday-alignment",
            recipient="ou_user_1",
            card_json='{"schema":"2.0"}',
            record_file_id="rec-1",
            appdata_root=str(tmp_path),
        )
    )
    assert second["status"] == "already_sent"


@pytest.mark.anyio
async def test_notify_meeting_card_send_failure_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_send(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"ok": False, "message": "permission denied"}

    monkeypatch.setattr(cardmod._f, "send_card_impl", fake_send)
    result = json.loads(
        await cardmod.notify_meeting_card(
            meeting_name="weekday-alignment",
            recipient="ou_user_1",
            card_json='{"schema":"2.0"}',
            record_file_id="rec-2",
            appdata_root=str(tmp_path),
        )
    )
    assert result["status"] == "send_failed" and result["error"] == "permission denied"
    receipt_file = tmp_path / "meeting-session" / "weekday-alignment" / "notification_receipts.json"
    receipts = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert len(receipts) == 1
    assert next(iter(receipts.values()))["ok"] is False
