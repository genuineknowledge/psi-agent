# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""场景②「调整判断」的提交处理。

mentor 在判断复核卡上点「改判」后会收到一张覆写表单卡；写下自己的判断
（一句话，可含 1-5 分数）点提交即触发本工具：把改判结果写入复核状态
（decided=overridden，override.verdict/score），并把原判断复核卡原地刷成
「✍️ 已改判」终态。

纯回调工具：只吃 card_action_json，不自己发卡。
"""

from __future__ import annotations

import json
import re

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

_SCORE_MARK = re.compile(r"(?:(\d{1,2})\s*(?:分|星|★)|(?:★|星)\s*(\d{1,2}))")
_LEADING_SCORE = re.compile(r"^(?:(\d{1,2})\s*(?:分|星|★)|(?:★|星)\s*(\d{1,2}))\s*")


def _score_in_text(text: str, fallback: int) -> int:
    """从改判文本里抓 1-5 分。

    只认「N分 / N星 / N★ / ★N」的显式分数记号（如 \"3分 复盘缺结论\"），优先取
    **开头**的记号；抓不到才扫正文记号；都没有则沿用原分。**先捕获完整数字再
    校验 1-5** —— \"12分\" 是超范围的 12 分，不会退化成 2 分。刻意不做裸数字
    扫描，日期/序号里的数字不会被误当分数（\"2026-09-05 前给出结论\" 不会抓成
    2 分）。
    """
    for pat in (_LEADING_SCORE, _SCORE_MARK):
        m = pat.match(text) if pat is _LEADING_SCORE else pat.search(text)
        if not m:
            continue
        n = int(m.group(1) or m.group(2))
        if 1 <= n <= 5:
            return n
    return fallback


def _strip_leading_score(text: str) -> str:
    """去掉改判文本开头的分数记号（\"3分 先给结论…\" → \"先给结论…\"）。

    分数已单独解析存进 override.score，正文里再留一份会和 ★ 展示重复。
    """
    return _LEADING_SCORE.sub("", text).strip()


async def feishu_pn_judge_override(card_action_json: str = "", user_key: str = "") -> str:
    """处理「调整判断」表单卡的提交（场景② 判断复核的调整入口）。

    Args:
        card_action_json: <feishu_card_action> payload（处理点击时传）。
        user_key: 点击者 open_id（Session 注入）。

    Returns:
        JSON：ok / action / judge_id / verdict / score / error。
    """
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    if action != P._ACTION_OVERRIDE_SUBMIT:
        return json.dumps({"ok": False, "error": "unrecognized action"}, ensure_ascii=False)
    judge_id = str(act.get("judge_id") or "").strip()
    if not judge_id:
        return json.dumps({"ok": False, "error": "no judge_id in value"}, ensure_ascii=False)
    form = act.get("_form_value") or {}
    if not isinstance(form, dict):
        return json.dumps({"ok": False, "error": "no form_value in payload"}, ensure_ascii=False)
    raw_text = str(form.get("pn_override_text") or "").strip()
    if not raw_text:
        return json.dumps({"ok": False, "error": "改判内容不能为空"}, ensure_ascii=False)
    if len(raw_text) > P.MAX_VERDICT:
        return json.dumps(
            {"ok": False, "error": f"改判内容超长（{len(raw_text)} > {P.MAX_VERDICT} 字上限），请精简后重新提交"},
            ensure_ascii=False,
        )
    judge = await P.load_judge(judge_id)
    if not judge:
        return json.dumps({"ok": False, "error": f"judge {judge_id} not found"}, ensure_ascii=False)
    if judge["decided"] in P._FINAL_JUDGE:
        return json.dumps({"ok": True, "unchanged": True, "decided": judge["decided"]}, ensure_ascii=False)
    op = act.get("_operator") or user_key or ""
    old_score = int((judge.get("judgment") or {}).get("score") or 0)
    verdict_text = _strip_leading_score(raw_text)
    if not verdict_text:
        return json.dumps({"ok": False, "error": "改判内容不能只有分数记号，请写一句判断"}, ensure_ascii=False)
    # 接台账时先写「状态=已调整」；写不进就不算改判成立，保持 pending 可重试。
    ledger_res = await P.apply_judge_ledger_status(judge, "overridden", op)
    if not ledger_res.get("ok"):
        return json.dumps(
            {"ok": False, "error": f"台账状态未更新，改判未生效(可稍后重试): {ledger_res.get('error')}"},
            ensure_ascii=False,
            default=str,
        )
    judge["decided"] = "overridden"
    judge["decided_by"] = op
    judge["decided_at"] = P._now()
    judge["override"] = {"verdict": verdict_text, "score": _score_in_text(raw_text, old_score)}
    await P.save_judge(judge)
    out = {
        "ok": True,
        "action": action,
        "judge_id": judge_id,
        "verdict": verdict_text,
        "score": judge["override"]["score"],
        "ledger": ledger_res,
    }
    # 改判生效 → 自动给行为对象本人发结果反馈卡(场景③, 用 mentor 调整后的判断)。
    # 发失败只 warn，不阻塞改判本身 —— 判断已生效落账, 反馈可稍后手动补发。
    fb_sent = await P.maybe_send_feedback_after_judge(judge, op)
    out["feedback"] = fb_sent
    if not fb_sent.get("ok") and not fb_sent.get("skipped"):
        out["warn"] = f"feedback card send failed: {fb_sent.get('error')}"
    # 重建目标是原判断复核卡(不是改判表单卡); 表单卡已被飞书单次消费。
    msg_id = str(judge.get("message_id") or "") or act.get("_message_id")
    if msg_id:
        try:
            res = await _core.edit_card_impl(
                msg_id, json.dumps(P.render_judge_card(judge), ensure_ascii=False), user_key
            )
        except Exception as e:
            out["warn"] = f"card rebuild failed: {e!r}"
        else:
            if not res.get("ok"):
                out["warn"] = f"card rebuild failed: {res.get('message') or res}"
    return json.dumps(out, ensure_ascii=False, default=str)
