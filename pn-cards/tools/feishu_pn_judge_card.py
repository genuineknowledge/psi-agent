# ruff: noqa: RUF001, RUF002, RUF003  # 中文全角标点是刻意排版
"""场景② 判断复核卡（mentor 反馈闭环 P0）。

海豚对一条已入清单的行为给出判断（锐评一句 + ★总分 + 证据 + 建议；负面附
「正确做法 · 建议」），把「判断复核卡」推给 mentor。卡面三动作：

- 同意判断 —— 判断成立，生效（整卡原地刷成已同意判断，无按钮）。
- 调整判断 —— 私聊弹一张覆写表单卡，mentor 写下自己的判断，提交即调整
  （由 feishu_pn_judge_override 处理）。
- 退回补证 —— 判断退回（结论不成立，需补证后重新判断），整卡刷成已退回补证。

工具一卡两用：发卡（传 receive_id + judgment_json）/ 处理点击（传
card_action_json）。复核终态后整卡不再响应任何按钮（工具内终态幂等）。
负面结论先经 mentor 复核再通知本人，全程留痕。
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
    if polarity == "negative" and not advice:
        return None, "negative judgments must carry advice (正确做法 · 建议)"
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


async def _rebuild_judge_card(judge: dict, message_id: str, user_key: str) -> dict[str, Any]:
    try:
        res = await _core.edit_card_impl(
            message_id, json.dumps(P.render_judge_card(judge), ensure_ascii=False), user_key
        )
    except Exception as e:
        return {"ok": False, "error": f"{e!r}"}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("message") or res}
    return {"ok": True}


async def _handle_click(card_action_json: str, user_key: str = "") -> str:
    act = P.parse_action(card_action_json)
    action = str(act.get("action") or "")
    if action not in (P._ACTION_JUDGE_AGREE, P._ACTION_JUDGE_OVERRIDE, P._ACTION_JUDGE_RETURN):
        return json.dumps({"ok": False, "error": "unrecognized judge action"}, ensure_ascii=False)
    judge_id = str(act.get("judge_id") or "").strip()
    if not judge_id:
        return json.dumps({"ok": False, "error": "no judge_id in value"}, ensure_ascii=False)
    judge = await P.load_judge(judge_id)
    if not judge:
        return json.dumps({"ok": False, "error": f"judge {judge_id} not found"}, ensure_ascii=False)
    if judge["decided"] in P._FINAL_JUDGE:
        return json.dumps({"ok": True, "unchanged": True, "decided": judge["decided"]}, ensure_ascii=False)
    op = act.get("_operator") or user_key or ""
    out: dict[str, Any] = {"ok": True, "action": action}
    if action == P._ACTION_JUDGE_AGREE:
        decided = "approved"
    elif action == P._ACTION_JUDGE_RETURN:
        decided = "returned"
    else:  # 改判：弹覆写表单卡，复核卡先不动，等提交后再刷终态
        reviewer = str(act.get("person_open_id") or judge.get("receiver_open_id") or "").strip() or op
        card = P.render_judge_override_card(judge)
        try:
            res = await _core.send_card_impl(
                receive_id=reviewer,
                card_json=json.dumps(card, ensure_ascii=False),
                receive_id_type="open_id",
                user_key=op,
                business_context_json=json.dumps(
                    {"kind": "pn_judge_override", "judge_id": judge_id}, ensure_ascii=False
                ),
                action_handlers_json=json.dumps(
                    {P._ACTION_OVERRIDE_SUBMIT: "feishu_pn_judge_override"}, ensure_ascii=False
                ),
                multi_use=False,
            )
        except Exception as e:
            return json.dumps({"ok": False, "error": f"override card send failed: {e!r}"}, ensure_ascii=False)
        if not res.get("ok"):
            return json.dumps(
                {"ok": False, "error": f"override card send failed: {res.get('message') or res}"},
                ensure_ascii=False,
                default=str,
            )
        out["action"] = action
        out["override_form_message_id"] = res.get("message_id", "")
        return json.dumps(out, ensure_ascii=False, default=str)

    # 接台账时先把「状态」列写掉(同意→已通过 / 退回→已退回); 写不进就不算决定
    # 成立、保持 pending 可重试 —— 不许出现卡面已终态、表里还停待复核的假闭环。
    ledger_res = await P.apply_judge_ledger_status(judge, decided, op)
    if not ledger_res.get("ok"):
        return json.dumps(
            {
                "ok": False,
                "action": action,
                "error": f"台账状态未更新，判断未生效(可稍后重试): {ledger_res.get('error')}",
            },
            ensure_ascii=False,
            default=str,
        )
    out["ledger"] = ledger_res

    judge["decided"] = decided
    judge["decided_by"] = op
    judge["decided_at"] = P._now()
    await P.save_judge(judge)
    msg_id = act.get("_message_id") or str(judge.get("message_id") or "")
    rebuilt: dict[str, Any] = {"ok": True, "skipped": "no card to rebuild"}
    if msg_id:
        rebuilt = await _rebuild_judge_card(judge, msg_id, op)
    if rebuilt.get("ok") is False:
        out["warn"] = f"card rebuild failed: {rebuilt.get('error')}"
    # 判断生效(同意) → 自动给行为对象本人发结果反馈卡(场景③)。发失败只 warn，
    # 不阻塞判断本身 —— 判断已生效落账, 反馈可稍后手动补发。
    if decided == "approved":
        fb_sent = await P.maybe_send_feedback_after_judge(judge, op)
        out["feedback"] = fb_sent
        if not fb_sent.get("ok") and not fb_sent.get("skipped"):
            out["warn"] = (out.get("warn") + " / " if out.get("warn") else "") + (
                f"feedback card send failed: {fb_sent.get('error')}"
            )
    return json.dumps(out, ensure_ascii=False, default=str)


async def feishu_pn_judge_card(
    receive_id: str = "",
    receiver_name: str = "",
    judgment_json: str = "",
    ledger_app_token: str = "",
    ledger_table_id: str = "",
    ledger_record_id: str = "",
    person_open_id: str = "",
    person_name: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
    card_action_json: str = "",
) -> str:
    """正负面「判断卡」场景②——判断复核（发卡 / 处理点击两用）。

    **发卡**：把海豚对一条行为记录的判断推给 mentor 复核。judgment_json 字段：
    ``polarity``（positive/negative，必填）、``label``（标签，如 负面·复盘）、
    ``verdict``（锐评一句，必填，锐评三要：下结论/指名行为/带可执行动作）、
    ``score``（★总分 1-5，必填）、``behavior``（证据/行为事实，必填）、
    ``advice``（建议；negative 必填，作为「正确做法 · 建议」行展示）、
    ``source``/``source_time``（来源标注）。
    ledger_app_token/table_id/record_id 可选：给了就把复核决定写回该台账行的
    「状态」列（同意→已通过 / 调整→已调整 / 退回→已退回）；**写不进就不算
    决定成立**，卡片保持待复核可重试。不给则只走本地复核状态（pn-state/）。
    person_open_id/person_name 可选：被判断的行为对象本人 —— 判断生效（同意/
    改判）后海豚自动把结果反馈卡（场景③，开始复盘/补充说明）发给这个人。

    **处理点击**（传 card_action_json）：action 为 pn_judge_agree（同意判断，判断生效，
    且自动给行为对象本人发结果反馈卡）/ pn_judge_override（调整判断，弹覆写表单卡）
    / pn_judge_return（退回补证，判断退回）。终态后不再响应（Channel 逐 action 消费
    + 工具终态幂等），整卡原地更新。

    Args:
        receive_id: mentor open_id（发卡时必填）。
        receiver_name: mentor/复核人姓名（卡面称呼）。
        judgment_json: 判断 JSON（发卡时必填，见上）。
        ledger_app_token: 可选真实台账 base app_token。
        ledger_table_id: 可选真实台账 table_id。
        ledger_record_id: 可选真实台账 record_id（待复核行）。
        person_open_id: 可选被判断的行为对象 open_id（判断生效后反馈给他）。
        person_name: 可选被判断对象姓名。
        receive_id_type: 收件人 id 类型（默认 open_id）。
        user_key: 调用者/点击者 open_id。
        card_action_json: 点击回调 payload（处理点击时传）。

    Returns:
        JSON：ok / action(sent|pn_judge_agree|pn_judge_override|pn_judge_return)
        / judge_id / message_id / decided / ledger（状态回写结果）/ feedback（同意后
        自动发的反馈卡结果）/ warn / error。
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
    judge = P.build_judge(
        receive_id.strip(),
        (receiver_name or "成员").strip(),
        judgment,
        ledger=ledger,
        person_open_id=person_open_id.strip(),
        person_name=person_name.strip(),
    )
    await P.save_judge(judge)
    handlers = P.judge_card_handlers(judge)
    res = await _core.send_card_impl(
        receive_id=receive_id.strip(),
        card_json=json.dumps(P.render_judge_card(judge), ensure_ascii=False),
        receive_id_type=receive_id_type.strip() or "open_id",
        user_key=user_key,
        business_context_json=json.dumps(
            {"kind": "pn_judge_review", "judge_id": judge["judge_id"], "receiver": receiver_name or ""},
            ensure_ascii=False,
        ),
        action_handlers_json=json.dumps(handlers, ensure_ascii=False),
        multi_use=True,
    )
    if not res.get("ok"):
        return json.dumps(
            {"ok": False, "error": res.get("message") or res.get("error") or res}, ensure_ascii=False, default=str
        )
    judge["message_id"] = res.get("message_id", "")
    await P.save_judge(judge)
    return json.dumps(
        {
            "ok": True,
            "action": "sent",
            "judge_id": judge["judge_id"],
            "message_id": res.get("message_id", ""),
            "polarity": judgment["polarity"],
            "score": judgment["score"],
        },
        ensure_ascii=False,
        default=str,
    )
