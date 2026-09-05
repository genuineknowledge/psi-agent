# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""场景③「补充说明」的提交处理。

行为对象在结果反馈卡上点「补充说明」后会收到一张表单卡；写下自己的说明
（辩解 / 背景 / 后续动作）点提交即触发本工具：先把说明**写回台账该行「备注」
列**（读-拼-写，保留历史备注，mentor/海豚下次可见），写成功才把反馈卡刷成
「📝 已补充说明」终态 —— **没写进台账就不算补充完成**（防"卡面已补充、表里
没留痕"的假闭环）。未接台账坐标时说明只落本地反馈状态（pn-state/）。

纯回调工具：只吃 card_action_json，不自己发卡。
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


async def feishu_pn_feedback_note(card_action_json: str = "", user_key: str = "") -> str:
    """处理「补充说明」表单卡的提交（场景③ 结果反馈的补充入口）。

    Args:
        card_action_json: <feishu_card_action> payload（处理点击时传）。
        user_key: 点击者 open_id（Session 注入）。

    Returns:
        JSON：ok / action / feedback_id / note / ledger（备注回写结果）/ warn / error。
    """
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    if action != P._ACTION_FB_NOTE_SUBMIT:
        return json.dumps({"ok": False, "error": "unrecognized action"}, ensure_ascii=False)
    feedback_id = str(act.get("feedback_id") or "").strip()
    if not feedback_id:
        return json.dumps({"ok": False, "error": "no feedback_id in value"}, ensure_ascii=False)
    form = act.get("_form_value") or {}
    if not isinstance(form, dict):
        return json.dumps({"ok": False, "error": "no form_value in payload"}, ensure_ascii=False)
    note = str(form.get("pn_fb_note_text") or "").strip()
    if not note:
        return json.dumps({"ok": False, "error": "补充说明不能为空"}, ensure_ascii=False)
    if len(note) > P._MAX_NOTE_TEXT:
        return json.dumps(
            {"ok": False, "error": f"补充说明超长（{len(note)} > {P._MAX_NOTE_TEXT} 字上限），请精简后重新提交"},
            ensure_ascii=False,
        )
    fb = await P.load_feedback(feedback_id)
    if not fb:
        return json.dumps({"ok": False, "error": f"feedback {feedback_id} not found"}, ensure_ascii=False)
    if fb["decided"] in P._FINAL_FEEDBACK:
        return json.dumps({"ok": True, "unchanged": True, "decided": fb["decided"]}, ensure_ascii=False)
    if fb["decided"] != P._FEEDBACK_NOTE_PENDING:
        # 理论上点补充说明才会进 note_pending; 直接收到 submit(如重放) 先按 pending 处理
        fb["decided"] = P._FEEDBACK_NOTE_PENDING
    op = act.get("_operator") or user_key or ""
    # 先写台账备注：成功或 skipped(未接台账) 才刷终态；写失败保持 note_pending 可重试。
    ledger_res = await P.append_feedback_note_to_ledger(fb, note, op)
    if not ledger_res.get("ok"):
        return json.dumps(
            {
                "ok": False,
                "action": action,
                "error": f"备注未写回台账，补充说明未生效(可稍后重试): {ledger_res.get('error')}",
            },
            ensure_ascii=False,
            default=str,
        )
    fb["decided"] = "noted"
    fb["decided_by"] = op
    fb["decided_at"] = P._now()
    fb["note_text"] = note
    await P.save_feedback(fb)
    out: dict[str, Any] = {
        "ok": True,
        "action": action,
        "feedback_id": feedback_id,
        "note": note,
        "ledger": ledger_res,
    }
    if ledger_res.get("skipped"):
        out["warn"] = "no ledger record wired — note kept in local feedback state only"
    # 重建目标是原结果反馈卡(不是表单卡); 表单卡已被飞书单次消费。
    target = str(fb.get("message_id") or "") or act.get("_message_id")
    if target:
        try:
            res = await _core.edit_card_impl(
                target, json.dumps(P.render_feedback_card(fb), ensure_ascii=False), user_key
            )
        except Exception as e:
            out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + f"card rebuild failed: {e!r}"
        else:
            if not res.get("ok"):
                out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + (
                    f"card rebuild failed: {res.get('message') or res}"
                )
    return json.dumps(out, ensure_ascii=False, default=str)
