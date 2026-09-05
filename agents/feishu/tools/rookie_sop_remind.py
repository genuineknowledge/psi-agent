"""每日 9:00 催办: 只在入职第 1、2 天发卡(绿/红), 第 2 天若仍未完成再给 HR 发一张反馈卡；
全部完成则随时发毕业卡；第 3 天起不再推、并删掉自己的定时(不管完没完成)。

由 schedules/rookie-remind-<后8位> 以 fire=tool 调用, 到点不经过 LLM。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF002
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_docapi as _docapi
import _rookie_sop_progress as _p
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
import rookie_sop_sync_doc as _sync
from feishu_api import feishu_api
from feishu_message import feishu_message_edit_card, feishu_message_send_card
from schedule_manage import schedule_manage

# 催办持续一周: 前两天由 rookie-docsync 每 10 分钟同步+重绘, 第 3 天起降为每天
# 9:00 一次(本工具顺带同步与重绘), 满一周停推并自删定时。


# 发卡只有前两天(用户明确定过): Day 1 提醒、Day 2 提醒并让 HR 知道。
REMIND_DAYS = 2
# 但进度同步要多活五天 —— 第 3-7 天不发卡、每天 9:00 只跑一次同步+重绘。
# 高频同步(rookie-docsync, 每 10 分钟)只覆盖前两天, 若这一档也停掉, 第 3 天起新人
# 勾什么都不会进表, 卡面与 HR 那张总览表就此冻结。第 8 天起才自删定时。
SYNC_DAYS = 7


def decide_remind(rows: list[dict[str, Any]], today: date, day_index: int) -> dict[str, Any]:
    """五条分支: 毕业 / 催办(第1天) / 催办+上报HR(第2天) / 只同步(第3-7天) / 停止(第8天起)。

    发卡只有两天, 但同步窗口有七天 —— 这是两件事:
      - 发卡: Day 1、Day 2 各一次。第 3 天起不再打扰新人。
      - 同步: 第 3-7 天每天 9:00 仍跑一次(kind=sync_only), 让进度继续进表、卡面继续
        更新; 高频那份(每 10 分钟)只覆盖前两天, 这一档是它之后的兜底。
    第 8 天起 kind=stop, 调用方据此自删定时 —— 不能让它永远到点转。

    "催办+上报" 不代表 HR 反馈卡真的会发出去——那要看 hr_notify_id 是否配置,
    这里只表达"这次催办事件本身值得让 HR 知道", 是否真的通知是调用方的事。
    """
    progress = _p.summarize(rows, today)
    if progress.all_done:
        return {"kind": "graduate", "progress": progress}
    if day_index <= 1:
        return {"kind": "remind", "progress": progress, "notify_hr": False}
    if day_index == 2:
        # 第 2 天的催办值得让 HR 知道 —— 那正是 rookie_sop_digest 的入选条件
        # (入职第 2 天结束仍未完成)。
        return {"kind": "remind", "progress": progress, "notify_hr": True}
    if day_index <= SYNC_DAYS:
        # 第 3-7 天: **不发卡, 只同步**。发卡只有两天(用户明确定过) —— 之后是否还没
        # 做完是 HR 反馈卡该管的事, 不该让新人天天收到催办卡到无穷。但进度还得继续
        # 更新: 高频同步(每 10 分钟)只覆盖前两天, 若这里也一并停掉, 第 3 天起新人
        # 勾什么都不会进表, 卡面和 HR 那张表就此冻结。所以这一档保留定时、只跑同步。
        return {"kind": "sync_only", "progress": progress}
    # 第 8 天起彻底停: 到这一步还没做完, 靠定时也不会变, 该由 HR 当面解决。
    # 不停的话就是一个永远到点转的定时。
    return {"kind": "stop", "progress": progress}


async def _delete_own_schedule(target: str, workspace: str = "") -> str:
    """删掉这个人的 rookie-remind-<后8位> 定时 —— 命名约定必须与创建时一致
    (rookie_sop_card_send.py), 否则删的是个不存在的名字, 任务永远留着。
    """
    # workspace 必须显式传: 定时在**新人自己**名下, 而本工具由定时触发时没有路径
    # 上下文(_fire_tool 不建 runtime_scope), 不传就会去调用方目录删一个不存在的
    # 名字 —— 定时永远留着, 每天空转一次。
    return await schedule_manage(action="delete", schedule_name=f"rookie-remind-{target[-8:]}", workspace=workspace)


async def rookie_sop_remind(open_id: str = "", workspace: str = "") -> str:
    """Remind one new hire of overdue / due-today onboarding items, or graduate them.

    Fired by that person's ``rookie-remind-<suffix>`` schedule with ``fire=tool``, so no
    LLM is involved. Only ever sends on onboarding day 1 (green) and day 2 (red) — from
    day 3 onward it sends nothing and deletes its own schedule, whether or not every item
    is done, so the schedule doesn't fire forever just to report "nothing to do". Day 2
    additionally sends HR a feedback card when the checklist is still incomplete — skipped
    with a reason when ``hr_notify_id`` is empty (never guessed, never silently dropped).
    Any day, if every applicable item is already done, it sends one graduation card instead
    and deletes its own schedule regardless of day index.

    Args:
        open_id: The new hire's Feishu open_id (written into the schedule's tool_args).
    """
    target = (open_id or "").strip()
    if not target:
        return json.dumps({"ok": False, "error": "open_id is required"}, ensure_ascii=False)

    # workspace 显式传入 —— 定时触发时框架不建路径上下文(schedule_registry._fire_tool
    # 裸调工具), 不传就会读到**触发方**的 state: 那是另一个人的多维表格, 催办内容
    # 会完全错(不是数据旧, 是查错了库)。
    state = await _rt.load_state(workspace)
    app_token = str(state.get("app_token") or "")
    detail_table = str(state.get("detail_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    bitable = _rt.bitable_adapter()

    # 催办前先把详情页文档里勾的项同步进来 —— 否则会拿着过期进度催人:
    # 新人昨天在文档里勾完了, 今早却收到「你还有 5 项未完成」。
    # 刻意放在 fetch_detail 之前, 让下面的判定读到同步后的行。
    # 同步失败不阻断催办(顶多进度偏旧), 但要记进返回值, 不能静默。
    doc_sync = ""
    docs = state.get("docs")
    if isinstance(docs, dict):
        doc_id = next((d for d, owner in docs.items() if str(owner) == target), "")
        if doc_id:
            try:
                synced = _store._parse_result(await _sync.rookie_sop_sync_doc(document_id=doc_id, open_id=target))
                if synced.get("ok") is not True:
                    doc_sync = f"doc sync failed: {synced.get('error')}"
            except Exception as exc:
                doc_sync = f"doc sync raised: {exc!r}"

    rows, truncated = await _store.fetch_detail(bitable, app_token, detail_table, target)
    today = date.today()
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), today)
    day = _cfg.day_index(onboard, today)
    decision = decide_remind(rows, today, day)
    kind = decision["kind"]
    progress = decision["progress"]

    if kind == "sync_only":
        # 第 3-7 天: 不发卡, 但上面的同步已经跑过了(它在 fetch_detail 之前), 所以
        # 这里只需如实报告、**保留定时** —— 删掉它进度就此冻结。
        result = {
            "ok": True,
            "sent": False,
            "kind": kind,
            "reason": "past day 2: progress synced, no card sent (schedule kept until day 7)",
            "progress": f"{progress.done}/{progress.total}",
        }
        if doc_sync:
            result["doc_sync"] = doc_sync
        return json.dumps(result, ensure_ascii=False)

    if kind == "stop":
        # 第 3 天起不再推 —— 删掉自己这份定时, 而不是继续到点转、天天返回"无事可做"。
        # 删不掉也不算硬失败(人可能已经毕业时被删过一次), 但要如实报告, 不能吞。
        schedule_result = await _delete_own_schedule(target, workspace)
        result = {
            "ok": True,
            "sent": False,
            "kind": kind,
            "reason": "past day 2, no more pushes; schedule self-deleted",
            "schedule": schedule_result,
        }
        if truncated:
            result["truncated"] = True
        return json.dumps(result, ensure_ascii=False)

    cfg = await _store.load_config()
    name = next((str(r.get("姓名") or "") for r in rows if r.get("姓名")), target)

    # 顺手把入口卡重绘一遍 —— 它的主题色按「入职第几天」定(Day 1 绿、Day 2 起红),
    # 但颜色只在有人重绘时才会变。原先只有 rookie_sop_sync_doc 会重绘, 而那个高频
    # 同步过了入职当天就自删, 于是第二天的卡永远停在发出时的绿色。
    # 实测: 罗霖第 3 天、进度 5/28, entry_card 算出的是 red, 可他手上那张还是绿的。
    # 放在这里(每天 9:00 催办前)最省事: 催办本来就要跑, 顺带刷一次颜色。
    entry_note = ""
    entry_mid = str((state.get("entry_cards") or {}).get(target) or "")
    if entry_mid:
        doc_id = next((d for d, owner in (state.get("docs") or {}).items() if str(owner) == target), "")
        doc_link = await _docapi.fetch_doc_url(feishu_api, doc_id) if doc_id else ""
        entry_card, _entry_handlers = _card.entry_card(name, rows, doc_link, today)
        edited = _store._parse_result(
            await feishu_message_edit_card(entry_mid, json.dumps(entry_card, ensure_ascii=False), target)
        )
        if edited.get("ok") is not True:
            entry_note = f"entry card redraw failed: {edited.get('message') or edited.get('error')}"

    if kind == "graduate":
        card, handlers = _card.graduation_card(name, progress.total)
    else:
        card, handlers = _card.remind_card(name, day, progress, str(cfg.get("sop_doc_url") or ""))

    business = {
        "type": "rookie_sop",
        "open_id": target,
        "name": name,
        "module": "催办",
        "app_token": app_token,
        "detail_table_id": detail_table,
        "overview_table_id": str(state.get("overview_table_id") or ""),
    }
    sent = _store._parse_result(
        await feishu_message_send_card(
            target,
            json.dumps(card, ensure_ascii=False),
            "open_id",
            "",
            json.dumps(business, ensure_ascii=False),
            json.dumps(handlers, ensure_ascii=False),
            bool(handlers),
        )
    )
    # 不能只看抛不抛异常: send_card 失败时返回 ok=false 的字符串, 更阴的一种是
    # "卡发出去了但回调快照没存下来"(callback_context_saved=false) —— 按钮全是
    # 死的, 必须当失败处理, 否则催办卡等于白发。
    if sent.get("ok") is not True or sent.get("callback_context_saved") is False:
        return json.dumps(
            {
                "ok": False,
                "error": sent.get("message") or sent.get("error") or "feishu_message_send_card failed",
                "kind": kind,
            },
            ensure_ascii=False,
        )

    schedule_result = ""
    if kind == "graduate":
        # 结果也不能吞: 万一真删不掉(比如名字对不上), 调用方要能看到
        # "[Error] ..." 而不是一个假的 ok=true。
        schedule_result = await _delete_own_schedule(target, workspace)
        if schedule_result.startswith("[Error]"):
            return json.dumps(
                {
                    "ok": False,
                    "sent": True,
                    "kind": kind,
                    "error": f"card sent but schedule delete failed: {schedule_result}",
                },
                ensure_ascii=False,
            )

    hr_feedback: dict[str, Any] = {}
    if kind == "remind" and decision.get("notify_hr"):
        # kind == "remind" 已经保证 progress.all_done 为假(decide_remind 先判毕业),
        # 这里不必再判一次 —— 走到这里就是"第 2 天, 还没做完"。
        hr_feedback = await _send_hr_feedback(cfg, name, progress)

    result: dict[str, Any] = {
        "ok": True,
        "sent": True,
        "kind": kind,
        "overdue": len(progress.overdue),
        "due_today": len(progress.due_today),
    }
    if doc_sync:
        result["doc_sync"] = doc_sync
    # 入口卡没刷成功要报出来 —— 静默就意味着新人手上那张卡的颜色永远不对, 而没人知道
    if entry_note:
        result["entry_card"] = entry_note
    if schedule_result:
        result["schedule"] = schedule_result
    if hr_feedback:
        result["hr_feedback"] = hr_feedback
    if truncated:
        result["truncated"] = True
    return json.dumps(result, ensure_ascii=False)


async def _send_hr_feedback(cfg: dict[str, Any], name: str, progress: Any) -> dict[str, Any]:
    """入职第 2 天仍未完成时, 顺带给 HR 发一张反馈卡。

    hr_notify_id 在联调阶段被刻意留空(安全考虑) —— 空的时候必须明确跳过并说明原因,
    不能悄悄不发、也不能猜一个收件人发出去(那比不发更糟: 卡片发给了错的人)。
    """
    hr_target = str(cfg.get("hr_notify_id") or "").strip()
    if not hr_target:
        return {"ok": False, "sent": False, "reason": "hr_notify_id is empty in config/rookie_sop.yaml"}

    card, handlers = _card.hr_feedback_card(name, progress, str(cfg.get("sop_doc_url") or ""))
    sent = _store._parse_result(
        await feishu_message_send_card(
            hr_target,
            json.dumps(card, ensure_ascii=False),
            "open_id",
            "",
            json.dumps({"type": "rookie_sop_hr_feedback", "name": name}, ensure_ascii=False),
            json.dumps(handlers, ensure_ascii=False),
        )
    )
    if sent.get("ok") is not True:
        return {
            "ok": False,
            "sent": False,
            "error": sent.get("message") or sent.get("error") or "feishu_message_send_card failed",
        }
    return {"ok": True, "sent": True}
