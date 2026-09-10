# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""卡片「写入表格链接」回归：接台账坐标的卡在页脚给一行可点的台账表格链接。

依据 2026-09-05 用户需求：卡片上加入写入表格的链接（记录写进/读到哪张表，
收卡人一眼可跳去核对）。约定：
- 链接域用 feishu.cn 别名域（与手发链接同款），app_token 必给才出链接，
  有 table_id 则深链到具体数据表（?table=<table_id>）；
- 未接台账（无 ledger / 无 app_token）→ 卡面与旧版一致、不留空行、不出死链；
- 四种场景卡（候选确认 / 判断复核 / 结果反馈 / 周小结）统一行为。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]  # agents/feishu
TOOLS_DIR = WORKSPACE / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _pn_impl as pn  # noqa: E402

_LEDGER = {"app_token": "app_real", "table_id": "tbl_real", "record_id": "rec_real"}
_BASE_URL = "https://feishu.cn/base/app_real?table=tbl_real"


def _body_text(card: dict) -> str:
    return json.dumps(card.get("body", {}).get("elements", []), ensure_ascii=False)


# ── URL 拼装 ────────────────────────────────────────────────────────────────


def test_ledger_url_with_table_deep_link():
    assert pn._ledger_link_url(_LEDGER) == _BASE_URL


def test_ledger_url_without_table_is_base_only():
    assert pn._ledger_link_url({"app_token": "app_real"}) == "https://feishu.cn/base/app_real"


def test_ledger_url_empty_when_no_coordinates():
    assert pn._ledger_link_url({}) == ""
    assert pn._ledger_link_url(None) == ""
    assert pn._ledger_link_url({"table_id": "tbl_real"}) == ""  # 只有 table 没有 base，拼不出可点链接


def test_ledger_link_cell_absent_without_ledger():
    assert pn._ledger_link_cell({"ledger": {}}) is None
    assert pn._ledger_link_cell({"no_ledger": True}) is None


# ── 场景① 候选确认卡 ────────────────────────────────────────────────────────


def test_confirm_card_shows_ledger_link_when_wired():
    batch = pn._build_candidate_batch(
        person_open_id="ou_person",
        person_name="张三",
        source_label="会议纪要",
        meeting_date="09-05 晨会",
        candidates=[{"text": "说好 18:00 前发评审稿，实际 21:30 才发出"}],
        ledger=dict(_LEDGER),
    )
    body = _body_text(pn.render_confirm_card(batch))
    assert _BASE_URL in body and "打开记录写入的表格" in body


def test_confirm_card_no_link_without_ledger():
    batch = pn._build_candidate_batch(
        person_open_id="ou_person",
        person_name="张三",
        source_label="会议纪要",
        meeting_date="09-05 晨会",
        candidates=[{"text": "某候选"}],
    )
    body = _body_text(pn.render_confirm_card(batch))
    assert "feishu.cn/base/" not in body


# ── 场景② 判断复核卡（含终态重建后仍保留链接） ──────────────────────────────


def test_judge_card_shows_ledger_link_when_wired():
    judge = pn.build_judge(
        "ou_mentor",
        "王mentor",
        {
            "polarity": "negative",
            "label": "负面·复盘",
            "verdict": "复盘缺结论：这条行为后果没交代",
            "score": 3,
            "behavior": "说好 18:00 前发评审稿，实际 21:30 才发出",
            "advice": "超时即同步",
        },
        ledger=dict(_LEDGER),
    )
    body = _body_text(pn.render_judge_card(judge))
    assert _BASE_URL in body and "打开台账表格" in body


def test_judge_card_link_survives_final_state():
    judge = pn.build_judge(
        "ou_mentor",
        "王mentor",
        {
            "polarity": "positive",
            "label": "正面·候选确认",
            "verdict": "记录链路是通的",
            "score": 4,
            "behavior": "候选确认入库",
        },
        ledger=dict(_LEDGER),
    )
    judge["decided"] = "approved"
    judge["decided_by"] = "ou_mentor"
    judge["decided_at"] = "2026-09-05 18:00"
    body = _body_text(pn.render_judge_card(judge))
    assert _BASE_URL in body  # 终态卡也可跳去核对表格，链接不消失


def test_judge_card_no_link_without_ledger():
    judge = pn.build_judge(
        "ou_mentor",
        "王mentor",
        {
            "polarity": "positive",
            "label": "正面·候选确认",
            "verdict": "记录链路是通的",
            "score": 4,
            "behavior": "候选确认入库",
        },
    )
    assert "feishu.cn/base/" not in _body_text(pn.render_judge_card(judge))


# ── 场景③ 结果反馈卡 ────────────────────────────────────────────────────────


def test_feedback_card_shows_ledger_link_when_wired():
    fb = pn.build_feedback(
        "ou_person",
        "张三",
        {
            "polarity": "negative",
            "label": "负面·复盘",
            "verdict": "复盘缺结论",
            "score": 3,
            "behavior": "说好 18:00 前发评审稿，实际 21:30 才发出",
            "advice": "超时即同步",
        },
        ledger=dict(_LEDGER),
    )
    body = _body_text(pn.render_feedback_card(fb))
    assert _BASE_URL in body and "打开备注写回的表格" in body


def test_feedback_card_no_link_without_ledger():
    fb = pn.build_feedback(
        "ou_person",
        "张三",
        {
            "polarity": "positive",
            "label": "正面·候选确认",
            "verdict": "记录链路是通的",
            "score": 4,
            "behavior": "候选确认入库",
        },
    )
    assert "feishu.cn/base/" not in _body_text(pn.render_feedback_card(fb))


# ── 场景④ 周小结卡 ──────────────────────────────────────────────────────────


def test_weekly_card_shows_ledger_link_when_wired():
    weekly = pn.build_weekly(
        "ou_person",
        "张三",
        "08-31 ~ 09-05",
        [
            {"record_id": "rec_1", "text": "候选一条", "status": "待复核", "date": "09-05 晨会", "note": ""},
        ],
        ledger=dict(_LEDGER),
    )
    body = _body_text(pn.render_weekly_card(weekly))
    assert _BASE_URL in body and "打开本周记录所在表格" in body


def test_weekly_card_no_link_without_ledger():
    weekly = pn.build_weekly("ou_person", "张三", "08-31 ~ 09-05", [])
    assert "feishu.cn/base/" not in _body_text(pn.render_weekly_card(weekly))
