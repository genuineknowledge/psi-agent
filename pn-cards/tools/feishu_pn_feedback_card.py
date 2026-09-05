# ruff: noqa: RUF002, RUF003  # 中文全角标点是刻意排版
"""场景③ 结果反馈卡（判断通过 → 反馈给行为对象本人）。

判断经 mentor 复核生效（同意判断 / 改判调整）后，海豚把结果反馈给**被记录人
本人**：一条已生效的判断（判断 + ★ + 证据 + 建议），卡面两动作（用户定稿）：
开始复盘 / 补充说明。

- 开始复盘 —— 对该条记录开启一对一复盘：工具只负责留痕 + 刷终态（卡片原地
  更新为「已开始复盘」），复盘对话由 agent 在后续轮次驱动（发生了什么 / 影响 /
  下次怎么做更好）。judge 生效联动与手动发卡都会走到这里。
- 补充说明 —— 本人补写说明（辩解 / 背景 / 后续动作）：私聊弹一张表单卡，提交
  后由 feishu_pn_feedback_note 把说明**写回台账该行「备注」列**（读-拼-写，保留
  历史备注，mentor/海豚下次可见）才刷终态 —— 写不进就不算补充完成。

工具一卡两用：发卡（传 receive_id + judgment_json）/ 处理点击（传
card_action_json，action 为 pn_fb_review / pn_fb_note）。终态（reviewed / noted）
后整卡不再响应任何按钮（Channel 逐 action 消费 + 工具终态幂等，双保险）。
"""

from __future__ import annotations

import json
from typing import Any

import _feishu_impl as _core


def _fresh_core():
    """每次(重)载本文件时按磁盘路径现取 _pn_impl，绕开下划线共享模块的进程内
    import 缓存（工具框架只按文件 hash 热载顶层工具，_ 前缀 core 不会刷新）。
    """
    import importlib.util as _ilu  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    _src = _Path(__file__).resolve().parent / "_pn_impl.py"
    _spec = _ilu.spec_from_file_location("_pn_impl_fresh", _src)
    _m = _ilu.module_from_spec(_spec)
    if _spec.loader is None:
        raise RuntimeError(f"cannot load {_src}")
    _spec.loader.exec_module(_m)
    return _m


P = _fresh_core()


def _normalize_judgment(raw: Any) -> tuple[dict | None, str]:
    """与场景②同一 judgment 结构（polarity/verdict/score/behavior 必填）。"""
    if not isinstance(raw, dict):
        return None, "judgment_json must be a JSON object"
    polarity = str(raw.get("polarity") or "").strip()
    if polarity not in ("positive", "negative"):
        return None, "polarity must be 'positive' or 'negative'"
    verdict = P._clamp(raw.get("verdict"), P.MAX_VERDICT)
    behavior = P._clamp(raw.get("behavior"))
    score = raw.get("score")
    try:
        score = int(score) if score not in (None, "") else 0
    except TypeError, ValueError:
        score = 0
    if not (1 <= score <= 5):
        return None, "score must be an integer 1..5"
    if not verdict:
        return None, "verdict (锐评一句) is required"
    if not behavior:
        return None, "behavior (证据/行为事实) is required"
    advice = P._clamp(raw.get("advice"), P.MAX_ADVICE)
    label = P._clamp(raw.get("label") or f"{P._POLARITY_CN[polarity]}·行为", 24)
    source = P._clamp(raw.get("source") or "", 40)
    source_time = P._clamp(raw.get("source_time") or "", 24)
    return {
        "polarity": polarity,
        "label": label,
        "verdict": verdict,
        "score": score,
        "behavior": behavior,
        "advice": advice,
        "source": source,
        "source_time": source_time,
    }, ""


async def _rebuild_feedback_card(fb: dict, message_id: str, user_key: str) -> dict[str, Any]:
    try:
        res = await _core.edit_card_impl(
            message_id, json.dumps(P.render_feedback_card(fb), ensure_ascii=False), user_key
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res}
    return {"ok": True}


async def _handle_click(card_action_json: str, user_key: str = "") -> str:
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    if action not in (P._ACTION_FB_REVIEW, P._ACTION_FB_NOTE):
        return json.dumps({"ok": False, "error": "unrecognized feedback action"}, ensure_ascii=False)
    feedback_id = str(act.get("feedback_id") or "").strip()
    if not feedback_id:
        return json.dumps({"ok": False, "error": "no feedback_id in value"}, ensure_ascii=False)
    fb = await P.load_feedback(feedback_id)
    if not fb:
        return json.dumps({"ok": False, "error": f"feedback {feedback_id} not found"}, ensure_ascii=False)
    if fb["decided"] in P._FINAL_FEEDBACK:
        return json.dumps({"ok": True, "unchanged": True, "decided": fb["decided"]}, ensure_ascii=False)
    op = act.get("_operator") or user_key or ""
    out: dict[str, Any] = {"ok": True, "action": action}
    if action == P._ACTION_FB_REVIEW:
        fb["decided"] = "reviewed"
        fb["decided_by"] = op
        fb["decided_at"] = P._now()
        await P.save_feedback(fb)
        out["review_started"] = True
        target = act.get("_message_id") or str(fb.get("message_id") or "")
        rebuilt: dict[str, Any] = {"ok": True, "skipped": "no card to rebuild"}
        if target:
            rebuilt = await _rebuild_feedback_card(fb, target, op)
        if rebuilt.get("ok") is False:
            out["warn"] = f"card rebuild failed: {rebuilt.get('error')}"
        return json.dumps(out, ensure_ascii=False, default=str)
    # 补充说明：原卡先撤按钮(显示"填写中…")，再私聊弹表单卡；提交由
    # feishu_pn_feedback_note 处理（写回台账备注成功才刷 noted 终态）。
    if fb["decided"] != "pending":
        return json.dumps({"ok": True, "unchanged": True, "decided": fb["decided"]}, ensure_ascii=False)
    fb["decided"] = P._FEEDBACK_NOTE_PENDING
    fb["decided_at"] = P._now()
    await P.save_feedback(fb)
    target = act.get("_message_id") or str(fb.get("message_id") or "")
    rebuilt = {"ok": True, "skipped": "no card to rebuild"}
    if target:
        rebuilt = await _rebuild_feedback_card(fb, target, op)
    if rebuilt.get("ok") is False:
        out["warn"] = f"card rebuild failed: {rebuilt.get('error')}"
    person = str(act.get("person_open_id") or fb.get("person_open_id") or "").strip()
    card = P.render_feedback_note_card(fb)
    try:
        res = await _core.send_card_impl(
            receive_id=person,
            card_json=json.dumps(card, ensure_ascii=False),
            receive_id_type="open_id",
            user_key=op,
            business_context_json=json.dumps(
                {"kind": "pn_feedback_note", "feedback_id": feedback_id}, ensure_ascii=False
            ),
            action_handlers_json=json.dumps({P._ACTION_FB_NOTE_SUBMIT: "feishu_pn_feedback_note"}, ensure_ascii=False),
            multi_use=False,
        )
    except Exception as e:
        out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + f"note form send failed: {e!r}"
        return json.dumps(out, ensure_ascii=False, default=str)
    if not res.get("ok"):
        out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + (
            f"note form send failed: {res.get('message') or res}"
        )
    else:
        out["note_form_message_id"] = res.get("message_id", "")
    return json.dumps(out, ensure_ascii=False, default=str)


async def feishu_pn_feedback_card(
    receive_id: str = "",
    person_name: str = "",
    judgment_json: str = "",
    ledger_app_token: str = "",
    ledger_table_id: str = "",
    ledger_record_id: str = "",
    source_judge_id: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
    card_action_json: str = "",
) -> str:
    """正负面「判断卡」场景③——结果反馈（发卡 / 处理点击两用）。

    **发卡**：把一条已生效的判断反馈给行为对象本人。judgment_json 字段与场景②
    同一结构：``polarity``（positive/negative，必填）、``label``、``verdict``
    （锐评一句，必填）、``score``（★ 1-5，必填）、``behavior``（证据/行为事实，
    必填）、``advice``（建议；negative 必填）、``source``/``source_time``。
    ledger_app_token/table_id/record_id 可选：给了则本人「补充说明」会写回该行
    「备注」列（读-拼-写，保留历史备注）；**写不进就不算补充完成**。不给则补充
    说明只落本地反馈状态（pn-state/feedback_*.json）。
    source_judge_id 可选：来源判断复核记录 id（judge 生效联动自动带）。

    **处理点击**（传 card_action_json）：action 为 pn_fb_review（开始复盘，整卡刷
    成已开始复盘）/ pn_fb_note（补充说明，弹表单卡，提交由 feishu_pn_feedback_note
    写回台账后刷终态）。终态后不再响应，整卡原地更新。

    Args:
        receive_id: 本人 open_id（发卡时必填）。
        person_name: 本人姓名（卡面称呼）。
        judgment_json: 判断 JSON（发卡时必填，见上）。
        ledger_app_token: 可选真实台账 base app_token。
        ledger_table_id: 可选真实台账 table_id。
        ledger_record_id: 可选真实台账 record_id。
        source_judge_id: 可选来源判断记录 id。
        receive_id_type: 收件人 id 类型（默认 open_id）。
        user_key: 调用者/点击者 open_id。
        card_action_json: 点击回调 payload（处理点击时传）。

    Returns:
        JSON：ok / action(sent|pn_fb_review|pn_fb_note) / feedback_id / message_id
        / decided / review_started / note_form_message_id / warn / error。
    """
    if card_action_json.strip():
        return await _handle_click(card_action_json, user_key)
    if not receive_id.strip():
        return json.dumps({"ok": False, "error": "receive_id is required"}, ensure_ascii=False)
    try:
        raw = json.loads(judgment_json) if judgment_json.strip() else None
    except ValueError as e:
        return json.dumps({"ok": False, "error": f"judgment_json is not valid JSON: {e!r}"}, ensure_ascii=False)
    judgment, err = _normalize_judgment(raw)
    if err:
        return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
    ledger = {}
    if ledger_app_token.strip() or ledger_table_id.strip() or ledger_record_id.strip():
        if not (ledger_app_token.strip() and ledger_table_id.strip() and ledger_record_id.strip()):
            return json.dumps(
                {"ok": False, "error": "ledger needs app_token, table_id and record_id together"},
                ensure_ascii=False,
            )
        ledger = {
            "app_token": ledger_app_token.strip(),
            "table_id": ledger_table_id.strip(),
            "record_id": ledger_record_id.strip(),
        }
    fb = P.build_feedback(
        person_open_id=receive_id.strip(),
        person_name=(person_name or "你").strip(),
        judgment=judgment,
        ledger=ledger,
        source_judge_id=source_judge_id.strip(),
    )
    await P.save_feedback(fb)
    try:
        sent = await P.send_feedback_card(fb, user_key=user_key)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{e!r}"}, ensure_ascii=False, default=str)
    if not sent.get("ok"):
        return json.dumps({"ok": False, "error": sent.get("error") or sent}, ensure_ascii=False, default=str)
    return json.dumps(
        {
            "ok": True,
            "action": "sent",
            "feedback_id": fb["feedback_id"],
            "message_id": sent.get("message_id", ""),
            "polarity": judgment["polarity"],
            "score": judgment["score"],
        },
        ensure_ascii=False,
        default=str,
    )
