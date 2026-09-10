# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""Regression tests for 正负面「判断卡」MVP v2 P0（场景① 记录确认 + 场景② 判断复核）。

依据《正负面清单 · MVP v2 优化方案》(2026-09-03)：一种判断卡模板三种场景；
P0 = 会议纪要候选链路 + 场景①/②。新增代码：
- tools/_pn_impl.py                  —— 共享核心（状态/渲染/幂等/记录管道）
- tools/feishu_pn_confirm_card.py    —— 场景① 候选确认卡（确认记录/修改内容/不做记录，multi_use 逐行）
- tools/feishu_pn_confirm_edit.py    —— 场景①「修改内容」表单提交（按新文字入库）
- tools/feishu_pn_judge_card.py      —— 场景② 判断复核卡（同意判断/调整判断/退回补证）
- tools/feishu_pn_judge_override.py  —— 场景②「调整判断」表单提交（覆写判断）

卡面可读性约定：颜色只用 <font color> 标记，不用 <span>（飞书卡片不认
<span>，会把标签原文渲染出来，即卡面上的"一串英文"）。

Asserted (offline, Feishu network mocked):
- 每张卡都是合法 schema-2.0 JSON；每个回调 action 规范且唯一；multi_use 卡
  handlers 键 == 卡上实际 action（键不匹配会在 Channel fail closed）；
- 场景① 逐行闭环：keep 入库（records.json）/ drop 未记录 / edit 弹表单 → 提交按
  新文字入库；重复点击被幂等拒绝（不重复入库、不重复改卡）；
- 场景② 闭环：同意生效 / 退回补证 / 调整弹表单 → 提交覆写判断；终态整卡
  无按钮、不再响应；negative 判断缺「正确做法·建议」被拒收；
- 状态落在隔离 AppData（conftest 已把 PSI_APPDATA 指到 tmp）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]  # agents/feishu
TOOLS_DIR = WORKSPACE / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_api_impl  # noqa: E402
import _feishu_impl  # noqa: E402
import feishu_pn_confirm_card as confirm_card  # noqa: E402
import feishu_pn_confirm_edit as confirm_edit  # noqa: E402
import feishu_pn_feedback_card as feedback_card  # noqa: E402
import feishu_pn_feedback_note as feedback_note  # noqa: E402
import feishu_pn_judge_card as judge_card  # noqa: E402
import feishu_pn_judge_override as judge_override  # noqa: E402
import feishu_pn_weekly_card as weekly_card  # noqa: E402

PN_DIR = "pn-state"


@pytest.fixture()
def feishu_network(monkeypatch):
    """Mock the Feishu send/edit card layer; record every call."""
    calls = {"send": [], "edit": []}
    counter = {"n": 0}

    def _mid():
        counter["n"] += 1
        return f"om_pn_{counter['n']}"

    async def fake_send(
        receive_id,
        card_json,
        receive_id_type,
        user_key=None,
        business_context_json="{}",
        action_handlers_json="{}",
        multi_use=False,
        **_kw,
    ):
        calls["send"].append(
            {
                "receive_id": receive_id,
                "card": json.loads(card_json) if isinstance(card_json, str) else card_json,
                "receive_id_type": receive_id_type,
                "user_key": user_key,
                "business_context": json.loads(business_context_json or "{}"),
                "action_handlers": json.loads(action_handlers_json or "{}"),
                "multi_use": multi_use,
                "message_id": _mid(),
            }
        )
        return {"ok": True, "message_id": calls["send"][-1]["message_id"], "thread_id": "", "chat_id": "oc_pn_test"}

    async def fake_edit(message_id, card_json, user_key=""):
        calls["edit"].append(
            {
                "message_id": message_id,
                "card": json.loads(card_json) if isinstance(card_json, str) else card_json,
                "user_key": user_key,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(_feishu_impl, "send_card_impl", fake_send)
    monkeypatch.setattr(_feishu_impl, "edit_card_impl", fake_edit)
    return calls


# ── helpers ──────────────────────────────────────────────────────────────────


def _callback_actions(card: dict) -> list[str]:
    out: list[str] = []

    def walk(items):
        for el in items or []:
            if not isinstance(el, dict):
                continue
            if el.get("tag") == "button":
                for b in el.get("behaviors") or []:
                    if isinstance(b, dict) and b.get("type") == "callback":
                        v = b.get("value") or {}
                        if isinstance(v, str):
                            v = json.loads(v)
                        out.append((v or {}).get("action") or "")
            for key in ("elements", "columns"):
                walk(el.get(key))

    walk(card.get("body", {}).get("elements", []) if card.get("schema") == "2.0" else card.get("elements", []))
    return out


def _assert_consistent(card: dict, handlers: dict, *, multi_use: bool):
    actions = _callback_actions(card)
    if not actions:
        assert handlers == {}, f"no buttons but handlers {handlers}"
        return
    assert all(a == a.strip() and a for a in actions), f"non-canonical action: {actions}"
    if multi_use:
        assert len(actions) == len(set(actions)), f"multi_use actions not unique: {actions}"
    assert set(handlers) == set(actions), (
        f"handler/action mismatch: handlers={sorted(handlers)} actions={sorted(actions)}"
    )
    assert all(h == h.strip() and h for h in handlers.values())
    assert card.get("schema") == "2.0"


def _card_action_payload(
    *, action: str, message_id: str, operator: str = "ou_op", form_value: dict | None = None, **value_extra
) -> str:
    action_obj = {"value": {"action": action, **value_extra}}
    if form_value is not None:
        action_obj["form_value"] = form_value
    return json.dumps(
        {
            "action": action_obj,
            "message_id": message_id,
            "operator": {"open_id": operator},
        },
        ensure_ascii=False,
    )


def _state_dir() -> Path:
    return Path(os.environ["PSI_APPDATA"]) / PN_DIR


def _records() -> list[dict]:
    path = _state_dir() / "records.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["records"]


def _batch_of(send_message_id: str) -> dict:
    for f in (_state_dir()).glob("cand_*.json"):
        b = json.loads(f.read_text(encoding="utf-8"))
        if b.get("message_id") == send_message_id:
            return b
    raise AssertionError(f"no batch with message_id {send_message_id}")


def _judge_of(send_message_id: str) -> dict:
    for f in (_state_dir()).glob("judge_*.json"):
        j = json.loads(f.read_text(encoding="utf-8"))
        if j.get("message_id") == send_message_id:
            return j
    raise AssertionError(f"no judge with message_id {send_message_id}")


_CANDIDATES = [
    {"text": "晨会里提出把 todo 清单导出从手动改成脚本定时，但没定负责人", "note": "示例·计划执行"},
    "复盘时只说了结果没给下一步（示例）",
    {"text": "发现线上故障后 10 分钟内拉了会议同步（示例）", "note": "示例·正面"},
]


async def _send_confirm(feishu_network, receive_id: str = "ou_ma") -> dict:
    out = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            receive_id=receive_id,
            person_name="马晨柯",
            candidates_json=json.dumps(_CANDIDATES, ensure_ascii=False),
            source_label="日会纪要",
            meeting_date="09-03",
        )
    )
    assert out["ok"], out
    return out


async def _send_judge(feishu_network, receive_id: str = "ou_mentor", negative: bool = True) -> dict:
    j = {
        "polarity": "negative" if negative else "positive",
        "label": "负面·复盘",
        "behavior": "周五复盘只念了结果清单，没给出结论和下一步（示例）",
        "verdict": "这是复盘失能：有流水无结论。",
        "score": 2,
        "advice": "下次复盘先写结论再列流水，并指定下一步负责人。",
        "source": "晨会纪要",
        "source_time": "09-03",
    }
    out = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id=receive_id,
            receiver_name="孙逊",
            judgment_json=json.dumps(j, ensure_ascii=False),
        )
    )
    assert out["ok"], out
    return out


# ── 场景① 候选确认卡 ─────────────────────────────────────────────────────


async def test_confirm_send_valid_multi_use_card(feishu_network):
    out = await _send_confirm(feishu_network)
    assert out["action"] == "sent" and out["counts"]["candidates"] == 3
    send = feishu_network["send"][-1]
    assert send["multi_use"] is True
    assert send["receive_id"] == "ou_ma"
    assert send["business_context"]["kind"] == "pn_confirm"
    actions = _callback_actions(send["card"])
    assert len(actions) == 9  # 3 行 × (记/不记/改)
    assert all(a.startswith(("pn_keep_", "pn_drop_", "pn_edit_")) for a in actions)
    _assert_consistent(send["card"], send["action_handlers"], multi_use=True)
    body = json.dumps(send["card"]["body"], ensure_ascii=False)
    assert "马晨柯" in body and "09-03" in body
    # 状态落盘 & message_id 回写
    b = _batch_of(send["message_id"])
    assert b["person_name"] == "马晨柯" and len(b["rows"]) == 3
    assert all(r["status"] == "pending" for r in b["rows"])


async def test_confirm_keep_records_and_rebuilds_in_place(feishu_network):
    out = await _send_confirm(feishu_network)
    msg = out["message_id"]
    # 点第 0 行「记入」
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_keep_0", message_id=msg, batch_id=out["batch_id"], person_open_id="ou_ma"
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"], res
    assert res["action"] == "pn_keep_0"
    assert res["commit"]["ok"] is True and res["commit"]["ledger"] is False
    assert len(_records()) == 1
    assert _records()[0]["text"] == _CANDIDATES[0]["text"]
    assert _records()[0]["by"] == "candidate_confirm"
    # 原卡原地重建：已点行终态、未点行按钮保留
    edited = feishu_network["edit"][-1]
    assert edited["message_id"] == msg and edited["card"]["schema"] == "2.0"
    body = json.dumps(edited["card"]["body"], ensure_ascii=False)
    assert "已记入" in body
    remaining = [a for a in _callback_actions(edited["card"]) if a.startswith("pn_keep_")]
    assert remaining == ["pn_keep_1", "pn_keep_2"]
    # 状态也更新了
    assert _batch_of(msg)["rows"][0]["status"] == "kept"


async def test_confirm_drop_does_not_record(feishu_network):
    out = await _send_confirm(feishu_network)
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_drop_0", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"], res
    assert "commit" not in res  # drop 不入库
    assert _records() == []
    assert _batch_of(out["message_id"])["rows"][0]["status"] == "dropped"
    assert "未记录" in json.dumps(feishu_network["edit"][-1]["card"]["body"], ensure_ascii=False)


async def test_confirm_edit_click_opens_edit_form(feishu_network):
    out = await _send_confirm(feishu_network)
    n_send = len(feishu_network["send"])
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_edit_0", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"], res
    assert res["action"] == "pn_edit_0"
    assert "edit_form_message_id" in res
    # 原卡第 0 行刷成「修改中」
    assert "修改中" in json.dumps(feishu_network["edit"][-1]["card"]["body"], ensure_ascii=False)
    # 多发的第二张 = 修改表单卡（legacy form，发给本人）
    assert len(feishu_network["send"]) == n_send + 1
    form_send = feishu_network["send"][-1]
    assert form_send["receive_id"] == "ou_ma" and form_send["multi_use"] is False
    assert form_send["action_handlers"] == {"pn_confirm_edit_submit": "feishu_pn_confirm_edit"}
    flat = json.dumps(form_send["card"], ensure_ascii=False)
    assert "pn_edit_text" in flat and "form_submit" in flat and "确认修改并记入" in flat
    assert _batch_of(out["message_id"])["rows"][0]["status"] == "editing"


async def test_confirm_edit_submit_records_edited_text(feishu_network):
    out = await _send_confirm(feishu_network)
    msg = out["message_id"]
    await confirm_card.feishu_pn_confirm_card(
        card_action_json=_card_action_payload(
            action="pn_edit_0", message_id=msg, batch_id=out["batch_id"], person_open_id="ou_ma"
        ),
        user_key="ou_ma",
    )
    edit_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await confirm_edit.feishu_pn_confirm_edit(
            card_action_json=_card_action_payload(
                action="pn_confirm_edit_submit",
                message_id=edit_msg,
                operator="ou_ma",
                batch_id=out["batch_id"],
                row_index=0,
                person_open_id="ou_ma",
                form_value={"pn_edit_text": "把导出改成脚本定时，负责人张三（改）"},
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"], res
    assert res["text"] == "把导出改成脚本定时，负责人张三（改）"
    row = _batch_of(msg)["rows"][0]
    assert row["status"] == "kept_edited" and row["edited_text"].endswith("（改）")
    assert len(_records()) == 1
    assert "（改）" in _records()[0]["text"]
    # 原候选确认卡原地刷成「已记入(修改后)」
    edited = feishu_network["edit"][-1]
    assert "已记入(修改后)" in json.dumps(edited["card"]["body"], ensure_ascii=False)


async def test_confirm_click_idempotent(feishu_network):
    out = await _send_confirm(feishu_network)
    payload = _card_action_payload(
        action="pn_keep_0", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
    )
    first = json.loads(await confirm_card.feishu_pn_confirm_card(card_action_json=payload, user_key="ou_ma"))
    assert first["ok"] and first["commit"]["ok"]
    n_edits = len(feishu_network["edit"])
    again = json.loads(await confirm_card.feishu_pn_confirm_card(card_action_json=payload, user_key="ou_ma"))
    assert again["ok"] is True and again["unchanged"] is True
    assert len(_records()) == 1  # 不重复入库
    assert len(feishu_network["edit"]) == n_edits  # 不重复改卡


async def test_confirm_validation(feishu_network):
    bad = json.loads(await confirm_card.feishu_pn_confirm_card(receive_id="ou_ma", candidates_json="[]"))
    assert bad["ok"] is False and "no candidates" in bad["error"]
    too_many = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            receive_id="ou_ma", candidates_json=json.dumps([f"候选{i}" for i in range(20)], ensure_ascii=False)
        )
    )
    assert too_many["ok"] is False and "cap is" in too_many["error"]
    invalid = json.loads(await confirm_card.feishu_pn_confirm_card(receive_id="ou_ma", candidates_json="not json"))
    assert invalid["ok"] is False
    assert feishu_network["send"] == []  # 校验失败不发出任何卡


async def test_confirm_escapes_html(feishu_network):
    await confirm_card.feishu_pn_confirm_card(
        receive_id="ou_ma",
        person_name="马晨柯",
        candidates_json=json.dumps(["<b>加粗注入</b> & 特殊字符"], ensure_ascii=False),
    )
    body = json.dumps(feishu_network["send"][-1]["card"]["body"], ensure_ascii=False)
    assert "<b>加粗注入</b>" not in body
    assert "&lt;b&gt;加粗注入&lt;/b&gt;" in body


# ── 场景② 判断复核卡 ─────────────────────────────────────────────────────


async def test_judge_send_valid_card(feishu_network):
    out = await _send_judge(feishu_network)
    assert out["action"] == "sent" and out["polarity"] == "negative"
    send = feishu_network["send"][-1]
    assert send["multi_use"] is True and send["receive_id"] == "ou_mentor"
    actions = _callback_actions(send["card"])
    assert sorted(actions) == ["pn_judge_agree", "pn_judge_override", "pn_judge_return"]
    _assert_consistent(send["card"], send["action_handlers"], multi_use=True)
    body = json.dumps(send["card"]["body"], ensure_ascii=False)
    assert "复盘失能" in body and "★" in body and "正确做法 · 建议" in body
    assert "孙逊" in json.dumps(send["card"]["header"], ensure_ascii=False)
    j = _judge_of(send["message_id"])
    assert j["decided"] == "pending" and j["judgment"]["score"] == 2


async def test_judge_positive_and_negative_missing_advice_rejected(feishu_network):
    base = {
        "polarity": "negative",
        "label": "负面·复盘",
        "behavior": "有流水无结论",
        "verdict": "复盘失能。",
        "score": 2,
        "source": "晨会",
        "source_time": "09-03",
    }
    missing = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id="ou_mentor", receiver_name="孙逊", judgment_json=json.dumps(base, ensure_ascii=False)
        )
    )
    assert missing["ok"] is False and "advice" in missing["error"]
    bad_score = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id="ou_mentor",
            receiver_name="孙逊",
            judgment_json=json.dumps({**base, "advice": "先结论后流水。", "score": 9}, ensure_ascii=False),
        )
    )
    assert bad_score["ok"] is False and "1..5" in bad_score["error"]
    assert feishu_network["send"] == []


async def test_judge_agree_rebuilds_done_card(feishu_network):
    out = await _send_judge(feishu_network)
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_agree",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"], res
    assert _judge_of(out["message_id"])["decided"] == "approved"
    edited = feishu_network["edit"][-1]
    assert edited["message_id"] == out["message_id"]
    assert "已同意判断" in json.dumps(edited["card"]["body"], ensure_ascii=False)
    assert _callback_actions(edited["card"]) == []  # 终态无按钮


async def test_judge_return_rebuilds_done_card(feishu_network):
    out = await _send_judge(feishu_network)
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_return",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"], res
    assert _judge_of(out["message_id"])["decided"] == "returned"
    body = json.dumps(feishu_network["edit"][-1]["card"]["body"], ensure_ascii=False)
    assert "已退回补证" in body and "退回" in body
    assert _callback_actions(feishu_network["edit"][-1]["card"]) == []


async def test_judge_override_click_opens_form(feishu_network):
    out = await _send_judge(feishu_network)
    n_edit = len(feishu_network["edit"])
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_override",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"], res
    assert "override_form_message_id" in res
    assert len(feishu_network["edit"]) == n_edit  # 改判弹表单时复核卡先不动
    form_send = feishu_network["send"][-1]
    assert form_send["receive_id"] == "ou_mentor" and form_send["multi_use"] is False
    assert form_send["action_handlers"] == {"pn_judge_override_submit": "feishu_pn_judge_override"}
    flat = json.dumps(form_send["card"], ensure_ascii=False)
    assert "pn_override_text" in flat and "提交调整判断" in flat


async def test_judge_override_submit_overwrites_and_rebuilds(feishu_network):
    out = await _send_judge(feishu_network)
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out["message_id"],
            judge_id=out["judge_id"],
            person_open_id="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out["judge_id"],
                form_value={"pn_override_text": "3分 复盘要先给结论，把负责人落实到人。"},
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"], res
    assert res["score"] == 3
    j = _judge_of(out["message_id"])
    assert j["decided"] == "overridden"
    assert "负责人落实到人" in j["override"]["verdict"]
    edited = feishu_network["edit"][-1]
    assert edited["message_id"] == out["message_id"]  # 刷的是原复核卡
    body = json.dumps(edited["card"]["body"], ensure_ascii=False)
    assert "已调整判断" in body and "负责人落实到人" in body
    assert _callback_actions(edited["card"]) == []


async def test_judge_final_no_longer_responsive(feishu_network):
    out = await _send_judge(feishu_network)
    payload = _card_action_payload(
        action="pn_judge_agree", message_id=out["message_id"], judge_id=out["judge_id"], person_open_id="ou_mentor"
    )
    await judge_card.feishu_pn_judge_card(card_action_json=payload, user_key="ou_mentor")
    n_edits = len(feishu_network["edit"])
    again = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_return",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert again["ok"] is True and again["unchanged"] is True
    assert _judge_of(out["message_id"])["decided"] == "approved"
    assert len(feishu_network["edit"]) == n_edits


async def test_unknown_actions_rejected(feishu_network):
    out = await _send_confirm(feishu_network)
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_keep_99", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"] is False
    jres = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(action="pn_judge_bogus", message_id="om_x", judge_id="j1"),
            user_key="ou_x",
        )
    )
    assert jres["ok"] is False


async def test_decided_lines_never_show_raw_operator_id(feishu_network):
    """回归（"一串英文"）：复核终态行把操作者 open_id 原样印上卡，必须换成姓名。

    三种终态（同意/退回/调整）都走 decided_by=open_id 的留痕 + decided block
    展示，任何一条都不许把 ou_xxx 直接渲染出来；兜底显示复核人姓名。
    """
    out = await _send_judge(feishu_network)  # receive_id=ou_mentor, receiver_name=孙逊
    agree = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_agree",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert agree["ok"], agree
    body = json.dumps(feishu_network["edit"][-1]["card"]["body"], ensure_ascii=False)
    assert "ou_mentor" not in body and "ou_op" not in body
    assert "孙逊" in body  # 兜底 = 复核人姓名，不是 id

    # 退回终态：重发一张再点退回
    out2 = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id="ou_mentor",
            receiver_name="孙逊",
            judgment_json=json.dumps(
                {
                    "polarity": "negative",
                    "label": "负面·复盘",
                    "behavior": "行为",
                    "verdict": "锐评",
                    "score": 2,
                    "advice": "建议",
                },
                ensure_ascii=False,
            ),
        )
    )
    assert out2["ok"], out2
    ret = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_return",
                message_id=out2["message_id"],
                judge_id=out2["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert ret["ok"], ret
    body = json.dumps(feishu_network["edit"][-1]["card"]["body"], ensure_ascii=False)
    assert "已退回补证" in body
    assert "ou_mentor" not in body and "ou_op" not in body
    assert "孙逊" in body

    # 调整判断终态同样不许带 id
    out3 = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id="ou_mentor",
            receiver_name="孙逊",
            judgment_json=json.dumps(
                {
                    "polarity": "negative",
                    "label": "负面·复盘",
                    "behavior": "行为2",
                    "verdict": "锐评2",
                    "score": 3,
                    "advice": "建议2",
                },
                ensure_ascii=False,
            ),
        )
    )
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out3["message_id"],
            judge_id=out3["judge_id"],
            person_open_id="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    ov = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out3["judge_id"],
                form_value={"pn_override_text": "4分 先给结论再给动作"},
            ),
            user_key="ou_mentor",
        )
    )
    assert ov["ok"], ov
    body = json.dumps(feishu_network["edit"][-1]["card"]["body"], ensure_ascii=False)
    assert "已调整判断" in body
    assert "ou_mentor" not in body and "ou_op" not in body
    assert "孙逊" in body


# ── 难度拉满回归：分数解析 / 落账原子性 / 超长拒绝 / 注入隔离 ────────────


def test_score_parsing_ignores_dates_and_ordinals():
    """改判文本里的日期/序号数字不得被误当分数（回归：旧实现抓 2026 的 2 分）。"""
    assert judge_override._score_in_text("2026-09-05 前给出结论", 3) == 3  # 全日期→fallback
    assert judge_override._score_in_text("9月4日给3分复盘", 2) == 3  # 跳过 9/4 抓 3分
    assert judge_override._score_in_text("★5 复盘到位", 1) == 5
    assert judge_override._score_in_text("先给结论，再 4分", 1) == 4  # 正文记号仍可抓
    assert judge_override._score_in_text("这次没有分数", 2) == 2  # 无记号→fallback
    assert judge_override._score_in_text("12分超范围", 1) == 1  # 12 不是 1-5
    assert judge_override._score_in_text("", 5) == 5


def test_override_strips_leading_score_from_verdict():
    """开头分数记号进 override.score，正文只留判断句，展示不重复。"""
    assert judge_override._strip_leading_score("3分 复盘要先给结论") == "复盘要先给结论"
    assert judge_override._strip_leading_score("4 分　先给结论") == "先给结论"
    assert judge_override._strip_leading_score("先给结论 3分") == "先给结论 3分"  # 非开头不动


async def test_confirm_ledger_success_writes_bitable_not_local(feishu_network, monkeypatch):
    """给真台账坐标且 bitable 成功：只写台账，本地 records.json 不落。"""
    import _feishu_api_impl as _api  # noqa: PLC0415

    seen = {}

    async def fake_call(method, uri, body_json="", query_json="", paths_json="", **kw):
        seen.update({"method": method, "uri": uri, "body": body_json, "paths": paths_json, "kw": kw})
        return {"ok": True, "data": {"record": {"record_id": "rec_bitable_1"}}}

    monkeypatch.setattr(_api, "call_api_impl", fake_call)
    out = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            receive_id="ou_ma",
            person_name="马晨柯",
            candidates_json=json.dumps(["把导出改成脚本定时（记账成功用例）"], ensure_ascii=False),
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
        )
    )
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_keep_0",
                message_id=out["message_id"],
                batch_id=out["batch_id"],
                person_open_id="ou_ma",
                operator="ou_ma",
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"] and res["commit"]["ok"]
    assert res["commit"]["ledger"] is True and res["commit"]["record_id"] == "rec_bitable_1"
    assert "fallback" not in res["commit"]
    assert _records() == []  # 台账成功就不落本地
    assert seen["uri"] == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    assert "app_ledger" in seen["paths"] and "tbl_ledger" in seen["paths"]
    assert "把导出改成脚本定时（记账成功用例）" in seen["body"]
    # 回归(99991668)：用户身份建的台账库必须用 user token 直写，禁止 tenant 先试
    assert seen["kw"].get("prefer") == "user"
    assert seen["kw"].get("user_key") == "ou_ma"  # 点击者 id 一路透传到写入


async def test_confirm_ledger_failure_falls_back_local_and_marks_kept(feishu_network, monkeypatch):
    """台账写失败必须回退本地暂存（回归：旧实现丢记录还谎报 recorded locally）。"""
    import _feishu_api_impl as _api  # noqa: PLC0415

    async def fake_call(*a, **kw):
        return {"ok": False, "message": "bitable quota exceeded"}

    monkeypatch.setattr(_api, "call_api_impl", fake_call)
    out = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            receive_id="ou_ma",
            person_name="马晨柯",
            candidates_json=json.dumps(["这条要保住，台账挂了也得上本地"], ensure_ascii=False),
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
        )
    )
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_keep_0", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"] and res["commit"]["ok"]
    assert res["commit"]["ledger"] is False and res["commit"]["fallback"] is True
    assert "bitable quota" in res["commit"]["reason"]
    assert "warn" in res and "ledger append failed" in res["warn"]
    recs = _records()
    assert len(recs) == 1  # 本地兜住了，没丢
    assert recs[0]["ledger_fallback"] is True
    assert "台账挂了" in recs[0]["text"]
    assert _batch_of(out["message_id"])["rows"][0]["status"] == "kept"


async def test_confirm_keep_nothing_persisted_returns_error_and_stays_pending(feishu_network, monkeypatch):
    """台账与本地双双写失败：整单报错、行保持 pending、不重建终态卡（不谎报已记入）。"""
    import _feishu_api_impl as _api  # noqa: PLC0415

    async def fake_call(*a, **kw):
        return {"ok": False, "message": "boom"}

    async def fake_commit(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(_api, "call_api_impl", fake_call)
    # 工具文件自带独立 core 实例（_fresh_core），要补丁它自己那份 commit_record
    monkeypatch.setattr(confirm_card.P, "commit_record", fake_commit)
    out = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            receive_id="ou_ma",
            person_name="马晨柯",
            candidates_json=json.dumps(["双失败用例"], ensure_ascii=False),
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
        )
    )
    n_edits = len(feishu_network["edit"])
    res = json.loads(
        await confirm_card.feishu_pn_confirm_card(
            card_action_json=_card_action_payload(
                action="pn_keep_0", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"] is False and "nothing persisted" in res["error"]
    assert _records() == []
    assert _batch_of(out["message_id"])["rows"][0]["status"] == "pending"  # 可重发卡重试
    assert len(feishu_network["edit"]) == n_edits  # 没把卡刷成已记入


async def test_confirm_edit_too_long_rejected_not_truncated(feishu_network):
    """修改表单超长：显式报错，绝不允许静默截断后按截断文本入库。"""
    from _pn_impl import MAX_TEXT  # noqa: PLC0415

    out = await _send_confirm(feishu_network)
    await confirm_card.feishu_pn_confirm_card(
        card_action_json=_card_action_payload(
            action="pn_edit_0", message_id=out["message_id"], batch_id=out["batch_id"], person_open_id="ou_ma"
        ),
        user_key="ou_ma",
    )
    edit_msg = feishu_network["send"][-1]["message_id"]
    long_text = "长" * (MAX_TEXT + 50)
    res = json.loads(
        await confirm_edit.feishu_pn_confirm_edit(
            card_action_json=_card_action_payload(
                action="pn_confirm_edit_submit",
                message_id=edit_msg,
                operator="ou_ma",
                batch_id=out["batch_id"],
                row_index=0,
                person_open_id="ou_ma",
                form_value={"pn_edit_text": long_text},
            ),
            user_key="ou_ma",
        )
    )
    assert res["ok"] is False and "超长" in res["error"]
    assert _records() == []  # 没入库
    assert _batch_of(out["message_id"])["rows"][0]["status"] == "editing"  # 仍可重提


async def test_judge_override_too_long_rejected(feishu_network):
    from _pn_impl import MAX_VERDICT  # noqa: PLC0415

    out = await _send_judge(feishu_network)
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out["message_id"],
            judge_id=out["judge_id"],
            person_open_id="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out["judge_id"],
                form_value={"pn_override_text": "冗" * (MAX_VERDICT + 30)},
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] is False and "超长" in res["error"]
    assert _judge_of(out["message_id"])["decided"] == "pending"


async def test_judge_override_score_token_only_rejected(feishu_network):
    """改判不能只提交一个分数记号：剥掉 3分 后必须还有一句判断。"""
    out = await _send_judge(feishu_network)
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out["message_id"],
            judge_id=out["judge_id"],
            person_open_id="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out["judge_id"],
                form_value={"pn_override_text": "3分"},
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] is False and "只有分数记号" in res["error"]
    assert _judge_of(out["message_id"])["decided"] == "pending"


async def test_confirm_escapes_closing_tag_injection(feishu_network):
    """候选文本里的 </font> 等闭合标签不得逃逸出 <font> 上色容器。"""
    await confirm_card.feishu_pn_confirm_card(
        receive_id="ou_ma",
        person_name="马晨柯",
        candidates_json=json.dumps(["</font><font color='red'>注入</font> & <script>"], ensure_ascii=False),
    )
    body = json.dumps(feishu_network["send"][-1]["card"]["body"], ensure_ascii=False)
    assert "</font><font" not in body
    assert "&lt;/font&gt;&lt;font" in body
    assert "<script>" not in body and "&lt;script&gt;" in body


async def test_judge_edit_render_escaping(feishu_network):
    """复核卡原判断含 HTML 特殊字符时卡面安全（改判表单回显也要转义）。"""
    await judge_card.feishu_pn_judge_card(
        receive_id="ou_mentor",
        receiver_name="孙逊",
        judgment_json=json.dumps(
            {
                "polarity": "negative",
                "label": "负面·复盘",
                "behavior": "行为 <script>alert(1)</script>",
                "verdict": "判断 <b>加粗</b> & 未完",
                "score": 2,
                "advice": "建议 </font><font color='red'>x</font>",
            },
            ensure_ascii=False,
        ),
    )
    flat = json.dumps(feishu_network["send"][-1]["card"], ensure_ascii=False)
    assert "<script>" not in flat and "<b>加粗</b>" not in flat
    assert "&lt;script&gt;" in flat and "&lt;b&gt;加粗&lt;/b&gt;" in flat


async def test_judge_evidence_source_no_dangling_separator(feishu_network):
    """体验修正 4：来源(source/source_time)为空时不渲染悬空分隔符。

    回归：旧实现无条件拼 '<font> · {source} {source_time}</font>'，
    source 为空时证据行尾残留灰色 ' · '；只给 source_time 时残留双空格。
    """
    await judge_card.feishu_pn_judge_card(
        receive_id="ou_mentor",
        receiver_name="孙逊",
        judgment_json=json.dumps(
            {
                "polarity": "negative",
                "label": "负面·复盘",
                "behavior": "行为事实X",
                "verdict": "锐评一句",
                "score": 3,
                "advice": "建议一句",
            },
            ensure_ascii=False,
        ),
    )
    body = json.dumps(feishu_network["send"][-1]["card"]["body"], ensure_ascii=False)
    assert "行为事实X" in body
    assert " ·  " not in body  # 双空来源：无悬空 ' · '（旧实现残 ' · ' + 双空格）
    await judge_card.feishu_pn_judge_card(
        receive_id="ou_mentor",
        receiver_name="孙逊",
        judgment_json=json.dumps(
            {
                "polarity": "positive",
                "label": "正面·复盘",
                "behavior": "当晚补了复盘",
                "verdict": "到位",
                "score": 4,
                "advice": "",
                "source": "晨会纪要",
                "source_time": "09-03",
            },
            ensure_ascii=False,
        ),
    )
    body2 = json.dumps(feishu_network["send"][-1]["card"]["body"], ensure_ascii=False)
    assert " · 晨会纪要 09-03" in body2  # 有来源时仍正常拼


# ── 场景② 判断复核 × 真实台账（P0 闭环缺口回归）──────────────────────────


async def _send_judge_ledger(
    feishu_network,
    receive_id: str = "ou_mentor",
    record_id: str = "rec_ledger_1",
    app_token: str = "app_ledger",
    table_id: str = "tbl_ledger",
) -> dict:
    j = {
        "polarity": "negative",
        "label": "负面·复盘",
        "behavior": "周五复盘只念了结果清单，没给出结论和下一步（台账回写用例）",
        "verdict": "这是复盘失能：有流水无结论。",
        "score": 2,
        "advice": "下次复盘先写结论再列流水，并指定下一步负责人。",
        "source": "晨会纪要",
        "source_time": "09-05",
    }
    out = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id=receive_id,
            receiver_name="孙逊",
            judgment_json=json.dumps(j, ensure_ascii=False),
            ledger_app_token=app_token,
            ledger_table_id=table_id,
            ledger_record_id=record_id,
        )
    )
    assert out["ok"], out
    return out


async def test_judge_agree_with_ledger_writes_status_and_finalizes(feishu_network, monkeypatch):
    """同意判断：台账「状态」列刷成已通过后决定才生效，原卡刷终态。"""
    import _feishu_impl  # noqa: PLC0415

    seen = {}

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        seen.update(
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            fields=json.loads(fields_json),
            user_key=user_key,
            identity=identity,
        )
        return {"ok": True, "record_id": record_id, "updated_fields": ["状态"], "fields": {"状态": "已通过"}}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_ledger(feishu_network)
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_agree",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
                operator="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] and res["ledger"]["status"] == "已通过"
    assert seen["app_token"] == "app_ledger" and seen["table_id"] == "tbl_ledger"
    assert seen["record_id"] == "rec_ledger_1"
    assert seen["fields"] == {"状态": "已通过"}
    assert seen["identity"] == "user" and seen["user_key"] == "ou_mentor"
    assert _judge_of(out["message_id"])["decided"] == "approved"
    assert feishu_network["edit"][-1]["message_id"] == out["message_id"]  # 原卡刷终态


async def test_judge_agree_ledger_failure_blocks_decision(feishu_network, monkeypatch):
    """台账状态写不进 → 判断不生效、保持 pending 可重试、不刷终态卡（防假闭环）。"""
    import _feishu_impl  # noqa: PLC0415

    async def fake_update(*a, **kw):
        return {"ok": False, "message": "99991668 Invalid access token"}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_ledger(feishu_network)
    n_edits = len(feishu_network["edit"])
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_agree",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] is False and "未生效" in res["error"]
    assert _judge_of(out["message_id"])["decided"] == "pending"  # 决定没落
    assert len(feishu_network["edit"]) == n_edits  # 卡没被刷成终态


async def test_judge_return_with_ledger_writes_returned(feishu_network, monkeypatch):
    """退回补证：台账「状态」列刷成已退回。"""
    import _feishu_impl  # noqa: PLC0415

    seen = {}

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        seen["fields"] = json.loads(fields_json)
        return {"ok": True, "record_id": record_id, "updated_fields": ["状态"], "fields": seen["fields"]}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_ledger(feishu_network)
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_return",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] and res["ledger"]["status"] == "已退回"
    assert seen["fields"] == {"状态": "已退回"}
    assert _judge_of(out["message_id"])["decided"] == "returned"


async def test_judge_override_submit_with_ledger_writes_adjusted(feishu_network, monkeypatch):
    """改判提交：台账「状态」列刷成已调整，原卡刷「已调整判断」终态。"""
    import _feishu_impl  # noqa: PLC0415

    seen = {}

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        seen["fields"] = json.loads(fields_json)
        return {"ok": True, "record_id": record_id, "updated_fields": ["状态"], "fields": seen["fields"]}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_ledger(feishu_network)
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out["message_id"],
            judge_id=out["judge_id"],
            person_open_id="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out["judge_id"],
                form_value={"pn_override_text": "3分 先给结论再列流水"},
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] and res["ledger"]["status"] == "已调整"
    assert seen["fields"] == {"状态": "已调整"}
    j = _judge_of(out["message_id"])
    assert j["decided"] == "overridden" and j["override"]["score"] == 3
    assert feishu_network["edit"][-1]["message_id"] == out["message_id"]  # 原卡刷终态


async def test_judge_override_submit_ledger_failure_blocks(feishu_network, monkeypatch):
    """改判提交时台账写不进 → 改判不生效、保持 pending，不谎报已调整。"""
    import _feishu_impl  # noqa: PLC0415

    async def fake_update(*a, **kw):
        return {"ok": False, "message": "99991668 Invalid access token"}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_ledger(feishu_network)
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out["message_id"],
            judge_id=out["judge_id"],
            person_open_id="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out["judge_id"],
                form_value={"pn_override_text": "先给结论再列流水"},
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] is False and "未生效" in res["error"]
    assert _judge_of(out["message_id"])["decided"] == "pending"


async def test_judge_ledger_params_require_all_three(feishu_network):
    """ledger 三个坐标必须一起给，缺一个直接拒发，不许半截接线。"""
    j = {"polarity": "positive", "label": "正面·复盘", "behavior": "B", "verdict": "V", "score": 4, "advice": ""}
    out = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id="ou_mentor",
            receiver_name="孙逊",
            judgment_json=json.dumps(j, ensure_ascii=False),
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
        )
    )
    assert out["ok"] is False and "together" in out["error"]


# ── 场景③ 结果反馈卡（P1）──────────────────────────────────────────────

_FB_JUDGMENT = {
    "polarity": "negative",
    "label": "负面·承诺执行",
    "behavior": "说好 18:00 前发评审稿，实际 21:30 才发出且未提前说明（示例）",
    "verdict": "承诺没兑现还零提前说明，信任就是这么被消耗的。",
    "score": 2,
    "advice": "赶不上截止=提前告知，并给出新的预计时间。",
    "source": "会议纪要",
    "source_time": "09-05",
}


def _feedback_of(send_message_id: str) -> dict:
    for f in _state_dir().glob("feedback_*.json"):
        fb = json.loads(f.read_text(encoding="utf-8"))
        if fb.get("message_id") == send_message_id:
            return fb
    raise AssertionError(f"no feedback with message_id {send_message_id}")


def _weekly_of(send_message_id: str) -> dict:
    for f in _state_dir().glob("weekly_*.json"):
        w = json.loads(f.read_text(encoding="utf-8"))
        if w.get("message_id") == send_message_id:
            return w
    raise AssertionError(f"no weekly with message_id {send_message_id}")


async def _send_feedback(feishu_network, receive_id: str = "ou_person", ledger: bool = False) -> dict:
    kwargs = {
        "receive_id": receive_id,
        "person_name": "马晨柯",
        "judgment_json": json.dumps(_FB_JUDGMENT, ensure_ascii=False),
    }
    if ledger:
        kwargs.update(ledger_app_token="app_ledger", ledger_table_id="tbl_ledger", ledger_record_id="rec_ledger_1")
    out = json.loads(await feedback_card.feishu_pn_feedback_card(**kwargs))
    assert out["ok"], out
    return out


async def test_feedback_send_valid_multi_use_card(feishu_network):
    """③ 发卡：schema 2.0、两按钮(开始复盘/补充说明)、handlers 匹配、multi_use。"""
    out = await _send_feedback(feishu_network)
    assert out["action"] == "sent" and out["polarity"] == "negative"
    card = feishu_network["send"][-1]["card"]
    _assert_consistent(card, feishu_network["send"][-1]["action_handlers"], multi_use=True)
    assert _callback_actions(card) == ["pn_fb_review", "pn_fb_note"]
    assert "已通过复核" in json.dumps(card, ensure_ascii=False)
    assert feishu_network["send"][-1]["receive_id"] == "ou_person"


async def test_feedback_review_finalizes(feishu_network):
    """③ 开始复盘 → 终态 reviewed、卡重建无按钮、重复点击 unchanged。"""
    out = await _send_feedback(feishu_network)
    res = json.loads(
        await feedback_card.feishu_pn_feedback_card(
            card_action_json=_card_action_payload(
                action="pn_fb_review",
                message_id=out["message_id"],
                feedback_id=out["feedback_id"],
                operator="ou_person",
            ),
            user_key="ou_person",
        )
    )
    assert res["ok"] and res["review_started"] is True
    assert _feedback_of(out["message_id"])["decided"] == "reviewed"
    assert _callback_actions(feishu_network["edit"][-1]["card"]) == []  # 无按钮
    again = json.loads(
        await feedback_card.feishu_pn_feedback_card(
            card_action_json=_card_action_payload(
                action="pn_fb_review",
                message_id=out["message_id"],
                feedback_id=out["feedback_id"],
                operator="ou_person",
            ),
            user_key="ou_person",
        )
    )
    assert again["ok"] and again.get("unchanged") is True


async def test_feedback_note_form_then_submit_local(feishu_network):
    """③ 补充说明：点按钮弹表单卡(原卡撤按钮)，无台账时提交落本地状态 noted。"""
    out = await _send_feedback(feishu_network)  # 无 ledger
    res = json.loads(
        await feedback_card.feishu_pn_feedback_card(
            card_action_json=_card_action_payload(
                action="pn_fb_note",
                message_id=out["message_id"],
                feedback_id=out["feedback_id"],
                person_open_id="ou_person",
                operator="ou_person",
            ),
            user_key="ou_person",
        )
    )
    assert res["ok"] and res["note_form_message_id"]
    # 原卡已重建为 note_pending(无按钮)；再发的一张是表单卡(legacy form)
    assert _callback_actions(feishu_network["edit"][-1]["card"]) == []
    form_msg = feishu_network["send"][-1]["message_id"]
    form_card = feishu_network["send"][-1]["card"]
    assert form_card.get("config", {}).get("wide_screen_mode") is True
    sub = json.loads(
        await feedback_note.feishu_pn_feedback_note(
            card_action_json=_card_action_payload(
                action="pn_fb_note_submit",
                message_id=form_msg,
                operator="ou_person",
                feedback_id=out["feedback_id"],
                form_value={"pn_fb_note_text": "当时在等法务确认，没有及时同步是我的问题。"},
            ),
            user_key="ou_person",
        )
    )
    assert sub["ok"] and sub["note"].startswith("当时在等法务确认")
    assert sub["ledger"].get("skipped")  # 无台账坐标 → 只落本地
    fb = _feedback_of(out["message_id"])
    assert fb["decided"] == "noted" and "法务确认" in fb["note_text"]


async def test_feedback_note_with_ledger_appends_remark(feishu_network, monkeypatch):
    """③ 补充说明 + 台账：备注读-拼-写(保留旧备注)，写成功才刷 noted。"""
    seen = {}

    async def fake_get(method, uri, **kw):
        assert method == "GET" and uri.endswith("/records/:record_id")
        return {
            "ok": True,
            "data": {
                "record": {"record_id": "rec_ledger_1", "fields": {"备注": [{"text": "演示·旧备注", "type": "text"}]}}
            },
        }

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        seen["fields"] = json.loads(fields_json)
        return {"ok": True, "record_id": record_id, "updated_fields": ["备注"]}

    monkeypatch.setattr(_feishu_api_impl, "call_api_impl", fake_get)
    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_feedback(feishu_network, ledger=True)
    await feedback_card.feishu_pn_feedback_card(
        card_action_json=_card_action_payload(
            action="pn_fb_note",
            message_id=out["message_id"],
            feedback_id=out["feedback_id"],
            person_open_id="ou_person",
            operator="ou_person",
        ),
        user_key="ou_person",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    sub = json.loads(
        await feedback_note.feishu_pn_feedback_note(
            card_action_json=_card_action_payload(
                action="pn_fb_note_submit",
                message_id=form_msg,
                operator="ou_person",
                feedback_id=out["feedback_id"],
                form_value={"pn_fb_note_text": "背景补充：评审稿在等外部依赖。"},
            ),
            user_key="ou_person",
        )
    )
    assert sub["ok"] and sub["ledger"].get("field") == "备注"
    new_note = seen["fields"]["备注"]
    assert "演示·旧备注" in new_note and "[补充 · 马晨柯" in new_note
    assert "背景补充：评审稿在等外部依赖。" in new_note
    assert _feedback_of(out["message_id"])["decided"] == "noted"


async def test_feedback_note_ledger_failure_keeps_pending(feishu_network, monkeypatch):
    """③ 备注写不进去 → 补充说明不生效(保持 note_pending 可重试)，防假闭环。"""

    async def fake_get(method, uri, **kw):
        return {"ok": True, "data": {"record": {"record_id": "rec_1", "fields": {}}}}

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        return {"ok": False, "message": "99991668 Invalid access token"}

    monkeypatch.setattr(_feishu_api_impl, "call_api_impl", fake_get)
    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_feedback(feishu_network, ledger=True)
    await feedback_card.feishu_pn_feedback_card(
        card_action_json=_card_action_payload(
            action="pn_fb_note",
            message_id=out["message_id"],
            feedback_id=out["feedback_id"],
            person_open_id="ou_person",
            operator="ou_person",
        ),
        user_key="ou_person",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    sub = json.loads(
        await feedback_note.feishu_pn_feedback_note(
            card_action_json=_card_action_payload(
                action="pn_fb_note_submit",
                message_id=form_msg,
                operator="ou_person",
                feedback_id=out["feedback_id"],
                form_value={"pn_fb_note_text": "这条写不进去的说明"},
            ),
            user_key="ou_person",
        )
    )
    assert sub["ok"] is False and "未生效" in sub["error"]
    assert _feedback_of(out["message_id"])["decided"] == "note_pending"


async def test_feedback_validation_and_oversize(feishu_network):
    """③ 发卡校验同场景②；补充说明超 400 字被拒。"""
    bad = {**_FB_JUDGMENT, "score": 9}
    out = json.loads(
        await feedback_card.feishu_pn_feedback_card(
            receive_id="ou_person", person_name="马晨柯", judgment_json=json.dumps(bad, ensure_ascii=False)
        )
    )
    assert out["ok"] is False and "score" in out["error"]
    fb = await _send_feedback(feishu_network)
    await feedback_card.feishu_pn_feedback_card(
        card_action_json=_card_action_payload(
            action="pn_fb_note",
            message_id=fb["message_id"],
            feedback_id=fb["feedback_id"],
            person_open_id="ou_person",
            operator="ou_person",
        ),
        user_key="ou_person",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    long_note = "长" * 401
    sub = json.loads(
        await feedback_note.feishu_pn_feedback_note(
            card_action_json=_card_action_payload(
                action="pn_fb_note_submit",
                message_id=form_msg,
                operator="ou_person",
                feedback_id=fb["feedback_id"],
                form_value={"pn_fb_note_text": long_note},
            ),
            user_key="ou_person",
        )
    )
    assert sub["ok"] is False and "超长" in sub["error"]


# ── 场景④ 周小结卡（P1）──────────────────────────────────────────────


def _today_md() -> str:
    import datetime  # noqa: PLC0415

    t = datetime.date.today()
    return f"{t.month:02d}-{t.day:02d}"


def _old_md(days_ago: int = 10) -> str:
    import datetime  # noqa: PLC0415

    d = datetime.date.today() - datetime.timedelta(days=days_ago)
    return f"{d.month:02d}-{d.day:02d}"


def _fake_search(records: list[dict]):
    async def _impl(
        app_token,
        table_id,
        filter_json="",
        sort_json="",
        field_names="",
        view_id="",
        page_size=100,
        page_token="",
        automatic_fields=False,
        user_key="",
    ):
        return {
            "ok": True,
            "records": records,
            "count": len(records),
            "has_more": False,
            "page_token": "",
            "total": len(records),
        }

    return _impl


async def test_weekly_send_from_ledger_filters_this_week(feishu_network, monkeypatch):
    """④ 周小结：只收本周(周一~今天)本人记录，状态分布/条数正确，双按钮。"""
    recs = [
        {
            "record_id": "rec_a",
            "fields": {
                "记录": [{"text": "本周负面：评审稿拖到 21:30", "type": "text"}],
                "状态": "待复核",
                "会议日期": [{"text": _today_md(), "type": "text"}],
                "备注": [{"text": "", "type": "text"}],
            },
        },
        {
            "record_id": "rec_b",
            "fields": {
                "记录": [{"text": "本周正面：及时拉会", "type": "text"}],
                "状态": "已通过",
                "会议日期": [{"text": _today_md(), "type": "text"}],
                "备注": [{"text": "已复核", "type": "text"}],
            },
        },
        {
            "record_id": "rec_old",
            "fields": {
                "记录": [{"text": "上周的旧记录", "type": "text"}],
                "状态": "已通过",
                "会议日期": [{"text": _old_md(), "type": "text"}],
                "备注": [{"text": "", "type": "text"}],
            },
        },
    ]
    monkeypatch.setattr(_feishu_impl, "search_bitable_records_impl", _fake_search(recs))
    out = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            receive_id="ou_person",
            person_name="马晨柯",
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
            user_key="ou_person",
        )
    )
    assert out["ok"], out
    assert out["counts"]["total"] == 2 and out["counts"]["statuses"] == {"待复核": 1, "已通过": 1}
    card = feishu_network["send"][-1]["card"]
    _assert_consistent(card, feishu_network["send"][-1]["action_handlers"], multi_use=True)
    assert _callback_actions(card) == ["pn_week_view", "pn_week_review"]
    text = json.dumps(card, ensure_ascii=False)
    assert "本周登记 2 条" in text and "状态分布" in text and "上周的旧记录" not in text
    assert _weekly_of(out["message_id"])["week_label"]


async def test_weekly_send_ledger_read_failure_no_card(feishu_network, monkeypatch):
    """④ 台账读失败 → 不出卡、如实报错（不发空卡假装有数）。"""

    async def _fail(*a, **kw):
        return {"ok": False, "message": "tenant token has no access"}

    monkeypatch.setattr(_feishu_impl, "search_bitable_records_impl", _fail)
    out = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            receive_id="ou_person",
            person_name="马晨柯",
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
            user_key="ou_person",
        )
    )
    assert out["ok"] is False and "ledger read failed" in out["error"]
    assert feishu_network["send"] == []


async def test_weekly_empty_week_still_sends(feishu_network, monkeypatch):
    """④ 本周无记录也发卡(0 条 + 仍可开始复盘)，不假装有数。"""
    monkeypatch.setattr(_feishu_impl, "search_bitable_records_impl", _fake_search([]))
    out = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            receive_id="ou_person",
            person_name="马晨柯",
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
            user_key="ou_person",
        )
    )
    assert out["ok"] and out["counts"]["total"] == 0
    text = json.dumps(feishu_network["send"][-1]["card"], ensure_ascii=False)
    assert "本周登记 0 条" in text


async def test_weekly_view_expands_then_review_finalizes(feishu_network, monkeypatch):
    """④ 查看本周记录 → 明细展开(按钮只剩开始复盘)；再开始复盘 → 终态。"""
    recs = [
        {
            "record_id": "rec_a",
            "fields": {
                "记录": [{"text": "评审稿拖到 21:30", "type": "text"}],
                "状态": "待复核",
                "会议日期": [{"text": _today_md(), "type": "text"}],
                "备注": [{"text": "", "type": "text"}],
            },
        }
    ]
    monkeypatch.setattr(_feishu_impl, "search_bitable_records_impl", _fake_search(recs))
    out = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            receive_id="ou_person",
            person_name="马晨柯",
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
            user_key="ou_person",
        )
    )
    view = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            card_action_json=_card_action_payload(
                action="pn_week_view", message_id=out["message_id"], weekly_id=out["weekly_id"], operator="ou_person"
            ),
            user_key="ou_person",
        )
    )
    assert view["ok"] and view.get("view_expanded") is True
    weekly = _weekly_of(out["message_id"])
    assert weekly["under_view"] is True
    assert _callback_actions(feishu_network["edit"][-1]["card"]) == ["pn_week_review"]
    review = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            card_action_json=_card_action_payload(
                action="pn_week_review", message_id=out["message_id"], weekly_id=out["weekly_id"], operator="ou_person"
            ),
            user_key="ou_person",
        )
    )
    assert review["ok"] and review.get("review_started") is True
    assert _weekly_of(out["message_id"])["decided"] == "reviewed"
    assert _callback_actions(feishu_network["edit"][-1]["card"]) == []
    again = json.loads(
        await weekly_card.feishu_pn_weekly_card(
            card_action_json=_card_action_payload(
                action="pn_week_review", message_id=out["message_id"], weekly_id=out["weekly_id"], operator="ou_person"
            ),
            user_key="ou_person",
        )
    )
    assert again["ok"] and again.get("unchanged") is True


# ── P1 联动：判断生效(同意/改判) → 自动发结果反馈卡给本人 ───────────────────


async def _send_judge_person(feishu_network) -> dict:
    out = json.loads(
        await judge_card.feishu_pn_judge_card(
            receive_id="ou_mentor",
            receiver_name="孙逊",
            judgment_json=json.dumps(_FB_JUDGMENT, ensure_ascii=False),
            ledger_app_token="app_ledger",
            ledger_table_id="tbl_ledger",
            ledger_record_id="rec_ledger_1",
            person_open_id="ou_person",
            person_name="马晨柯",
        )
    )
    assert out["ok"], out
    return out


async def test_judge_approve_auto_sends_feedback_to_person(feishu_network, monkeypatch):
    """联动① 同意判断生效 → 自动给行为对象本人发结果反馈卡(场景③)。"""

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        return {"ok": True, "record_id": record_id, "updated_fields": ["状态"]}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_person(feishu_network)
    n_before = len(feishu_network["send"])
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_agree",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
                operator="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"]
    assert res["feedback"]["ok"] and not res["feedback"].get("skipped")
    assert len(feishu_network["send"]) == n_before + 1  # 多发一张反馈卡
    fb_msg = res["feedback"]["message_id"]
    assert feishu_network["send"][-1]["receive_id"] == "ou_person"
    fb = _feedback_of(fb_msg)
    assert fb["person_open_id"] == "ou_person" and fb["source_judge_id"] == out["judge_id"]
    assert _callback_actions(feishu_network["send"][-1]["card"]) == ["pn_fb_review", "pn_fb_note"]


async def test_judge_approve_without_person_skips_feedback(feishu_network, monkeypatch):
    """联动① 复核卡没带行为对象 → 不硬发反馈，原逻辑不变(只本地复核)。"""

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        return {"ok": True, "record_id": record_id, "updated_fields": ["状态"]}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_ledger(feishu_network)  # 不带 person
    n_before = len(feishu_network["send"])
    res = json.loads(
        await judge_card.feishu_pn_judge_card(
            card_action_json=_card_action_payload(
                action="pn_judge_agree",
                message_id=out["message_id"],
                judge_id=out["judge_id"],
                person_open_id="ou_mentor",
                operator="ou_mentor",
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] and res["feedback"].get("skipped")
    assert len(feishu_network["send"]) == n_before  # 没有额外发卡


async def test_judge_override_auto_sends_feedback_with_new_verdict(feishu_network, monkeypatch):
    """联动② 改判生效 → 反馈卡用的是 mentor 调整后的判断(不是海豚原判)。"""

    async def fake_update(app_token, table_id, record_id, fields_json, user_key="", identity="", validate_fields=True):
        return {"ok": True, "record_id": record_id, "updated_fields": ["状态"]}

    monkeypatch.setattr(_feishu_impl, "update_bitable_record_impl", fake_update)
    out = await _send_judge_person(feishu_network)
    await judge_card.feishu_pn_judge_card(
        card_action_json=_card_action_payload(
            action="pn_judge_override",
            message_id=out["message_id"],
            judge_id=out["judge_id"],
            person_open_id="ou_mentor",
            operator="ou_mentor",
        ),
        user_key="ou_mentor",
    )
    form_msg = feishu_network["send"][-1]["message_id"]
    res = json.loads(
        await judge_override.feishu_pn_judge_override(
            card_action_json=_card_action_payload(
                action="pn_judge_override_submit",
                message_id=form_msg,
                operator="ou_mentor",
                judge_id=out["judge_id"],
                form_value={"pn_override_text": "3分 判断结论成立，但漏了责任到人——建议补上负责人。"},
            ),
            user_key="ou_mentor",
        )
    )
    assert res["ok"] and res["feedback"]["ok"]
    fb_msg = res["feedback"]["message_id"]
    assert feishu_network["send"][-1]["receive_id"] == "ou_person"
    fb = _feedback_of(fb_msg)
    assert fb["decided"] == "pending"
    text = json.dumps(feishu_network["send"][-1]["card"], ensure_ascii=False)
    assert "责任到人" in text and "承诺没兑现" not in text  # 卡上是改判后的判断
    assert fb["judgment"]["score"] == 3
