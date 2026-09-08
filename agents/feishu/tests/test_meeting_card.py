from __future__ import annotations

# Local meeting tools intentionally load from the agent package rather than an installed package.
# ruff: noqa: E402
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
    return [element.get("content", "") for element in card["body"]["elements"] if element.get("tag") == "markdown"]


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
    tags = [element.get("tag") for element in card["body"]["elements"]]
    assert tags.count("hr") == 2
    texts = _element_texts(card)
    assert texts[0] == "周中对齐会 · 2026-09-08"
    assert any("**会议摘要**" in text and "C 端发布定于" in text for text in texts)
    assert any("**关键决定与分析**" in text and "分层汇报" in text for text in texts)
    assert any("**✅ 正面候选**" in text and "P-1" in text for text in texts)
    assert any("**⚠️ 负面候选**" in text and "N-1" in text for text in texts)
    assert any("候选观察 · 不计分" in text for text in texts)


def test_render_meeting_summary_card_builds_values_and_truncates() -> None:
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
    assert values["key_points"].endswith("…(内容较长, 已截断, 完整文本见会议存档)")
    assert "P-1" in values["positives"] and "孙逊公开肯定汇报" in values["positives"]
    assert values["positives"].count("证据: ") == 1
    assert values["footer"].startswith("以下为候选观察")
    assert "候选观察: 不进入正式正负面总表" in values["footer"]


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
