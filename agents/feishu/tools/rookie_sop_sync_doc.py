"""把新人在详情页文档里勾的项同步回明细表 —— 由文档变更事件驱动, 不轮询。

刻意为之: 事件只说「这个文档被编辑了」, 不说哪一项被勾, 所以每次都整份读回来
对比。代价是一次读取, 换来的是不必轮询 —— 10 个新人若每 5 分钟轮询一次是每天
2880 次调用, 事件驱动只有实际勾选那几十次。

只认「未完成 → 勾上」一个方向(见 _rookie_sop_doc.diff_state 的说明): 允许取消
勾选就撤销完成记录, 等于让新人一取消就能抹掉数据, HR 日报会变得不可信。
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_doc as _doc
import _rookie_sop_docapi as _docapi
import _rookie_sop_progress as _p
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
from feishu_api import feishu_api
from feishu_message import feishu_message_edit_card
from schedule_manage import schedule_manage


def _doc_id_of(payload: dict[str, Any]) -> str:
    """从文档变更事件里取 document_id。

    飞书这类事件把 token 放在 file_token, 也可能直接给 document_id ——
    两种都认, 认不出就返回空串让调用方报错而不是猜。
    """
    for key in ("document_id", "file_token", "token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def rookie_sop_sync_doc(
    document_id: str = "", event_payload_json: str = "", open_id: str = "", workspace: str = ""
) -> str:
    """Sync a new hire's ticked items from their onboarding checklist doc into the detail table.

    Fired by the ``haitun.rookie.doc_edited`` trigger with ``fire=tool`` when the doc
    changes, so no LLM is involved and no polling is needed. The event only says *the doc
    changed*, never which row, so the whole doc is read back and compared.

    Only ``未完成 → ticked`` is applied. Un-ticking never revokes a completion: letting a
    new hire erase recorded progress by un-ticking would make the HR digest untrustworthy.

    Args:
        document_id: The checklist doc. Empty → read from ``event_payload_json``.
        event_payload_json: Event envelope payload (injected by Session).
        open_id: Whose checklist this is. Empty → resolved from the state file's doc index.
        workspace: That person's workspace dir. Required when fired by a schedule —
            ``schedule_registry._fire_tool`` calls tools without a ``runtime_scope``, so
            path ContextVars are unset and the state file would be looked up under the
            agent package instead of the new hire's workspace.
    """
    payload = _store._parse_result(event_payload_json) if event_payload_json else {}
    doc_id = (document_id or "").strip() or _doc_id_of(payload)

    state = await _rt.load_state(workspace)
    if not doc_id:
        # 只给了 open_id 时按 docs 索引反查 —— 催办前的同步就是这条路径:
        # 它手里只有人, 没有文档 token。原先在读 state 之前就返回, 于是催办
        # 永远同步不到东西(单测没覆盖到这条路, 是端到端验证才发现的)。
        target_hint = (open_id or "").strip()
        docs_index = state.get("docs")
        if target_hint and isinstance(docs_index, dict):
            doc_id = next((d for d, owner in docs_index.items() if str(owner) == target_hint), "")
    if not doc_id:
        return json.dumps(
            {"ok": False, "error": "no document_id in args/event payload, and open_id is not in the doc index"},
            ensure_ascii=False,
        )

    app_token = str(state.get("app_token") or "")
    detail_table = str(state.get("detail_table_id") or "")
    overview_table = str(state.get("overview_table_id") or "")
    if not app_token or not detail_table:
        return json.dumps({"ok": False, "error": "rookie SOP base is not initialised"}, ensure_ascii=False)

    # 文档索引: {document_id: open_id} —— 事件只带文档 token, 得反查是谁的清单
    docs = state.get("docs")
    docs = docs if isinstance(docs, dict) else {}
    target = (open_id or "").strip() or str(docs.get(doc_id) or "").strip()
    if not target:
        return json.dumps(
            {"ok": False, "error": f"cannot map document {doc_id} to an open_id; state has {len(docs)} doc(s)"},
            ensure_ascii=False,
        )

    read = await _docapi.read_blocks(feishu_api, doc_id)
    if read.get("ok") is not True:
        return json.dumps({"ok": False, "error": f"read doc failed: {read.get('error')}"}, ensure_ascii=False)

    # block_map 是建文档时存下的 {block_id: "item_id:role"} —— 靠它认条目, 所以
    # 文档正文里没有 item 标记。缺映射就无从对齐, 报错而不是瞎猜。
    maps = state.get("doc_block_maps")
    maps = maps if isinstance(maps, dict) else {}
    block_map = maps.get(doc_id)
    if not isinstance(block_map, dict) or not block_map:
        return json.dumps(
            {"ok": False, "error": f"no block map for document {doc_id}; re-send the card to rebuild it"},
            ensure_ascii=False,
        )

    blocks = read.get("blocks") or []
    doc_state, unclear = _doc.read_doc_state(blocks, block_map)
    bitable = _rt.bitable_adapter()
    rows, truncated = await _store.fetch_detail(bitable, app_token, detail_table, target)
    newly_ticked = _doc.diff_state(doc_state, rows)

    today = date.today()

    # 角色选择在文档里(两个互斥勾选框), 所以同步时要把它落地: 选了非研发就把 5 个
    # dev_only 项标成不适用, 让分母从 28 降到 23。
    # 刻意只在「表里还没记角色」时做一次: 反复标不适用是无谓的写入, 而且新人若
    # 改了主意(重勾另一个框), 由 HR 或本人改表更稳妥 —— 自动来回翻转会让已完成的
    # 开发项在两种状态间反复横跳。
    role_note = ""
    role_choice = _doc.read_role_choice(blocks, block_map)
    already_has_role = any(str(r.get("适用角色") or "") in {"研发", "非研发"} for r in rows)
    if role_choice and not already_has_role:
        role_note = await _apply_role_choice(
            bitable, app_token, detail_table, target=target, choice=role_choice, today=today
        )
        rows, _ = await _store.fetch_detail(bitable, app_token, detail_table, target)
        newly_ticked = _doc.diff_state(doc_state, rows)

    marked: list[str] = []
    failures: list[dict[str, str]] = []
    for item_id in newly_ticked:
        done = await _store.mark_done(bitable, app_token, detail_table, open_id=target, item_id=item_id, today=today)
        if done.get("ok") is not True:
            failures.append({"item_id": item_id, "error": str(done.get("error") or "mark_done failed")})
            continue
        marked.append(item_id)

    overview_updated = False
    if marked and overview_table:
        rows, _ = await _store.fetch_detail(bitable, app_token, detail_table, target)
        role_label = next(
            (str(r.get("适用角色") or "") for r in rows if str(r.get("适用角色") or "") in {"研发", "非研发"}), ""
        )
        role = "dev" if role_label == "研发" else "nondev" if role_label == "非研发" else ""
        name = next((str(r.get("姓名") or "") for r in rows if r.get("姓名")), target)
        recomputed = await _store.recompute_overview(
            bitable,
            app_token,
            overview_table,
            open_id=target,
            name=name,
            role=role,
            rows=rows,
            today=today,
        )
        overview_updated = recomputed.get("ok") is True

    # 刻意让工具自己删而不是靠外部清理: 定时任务是发卡时建的, 没人会记得回收它;
    # 留着它就是每 10 分钟一次的空轮询, 一年下来五万次。
    # (与 rookie_sop_remind._delete_own_schedule 同一套约定, 名字必须对上, 否则
    #  删的是个不存在的名字, 任务永远留着。)
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), None)
    schedule_note = ""
    day_index = ((today - onboard).days + 1) if onboard is not None else 0
    # 高频同步覆盖**前两天**(不只入职当天): 第 2 天是最后一天, 那天的勾选最需要及时
    # 反映到卡面和 HR 那张表上。第 3 天起降为每天 9:00 一次(催办顺带同步+重绘),
    # 所以这里把自己删掉。
    if onboard is not None and day_index > 2:
        schedule_note = await schedule_manage(action="delete", schedule_name=f"rookie-docsync-{target[-8:]}")

    progress = _p.summarize(rows, today)

    # 把新进度重绘到入口卡上。只写表是不够的 —— 用户看的是卡片, 表里数字变了而
    # 卡片停在发出时那一刻, 在他看来就是「根本没更新」(实测反馈正是如此)。
    # 用 edit_card 原地改, 不发新消息: 入口卡上只有一个 URL 跳转按钮、没有回调,
    # 所以不存在 edit 之后按钮失效的问题(那是 multi_use 勾选卡才要顾虑的)。
    # 文档里各分节的小计「x/y」也要跟着改 —— 它是建文档时算好写死的, 同步只改
    # todo 的勾选状态就会出现: 条目已划掉、分节标题还停在 0/5。
    # 小计只是显示, 失败不该让整次同步失败, 所以只记 note。
    tally_note = ""
    block_map = (state.get("doc_block_maps") or {}).get(doc_id) or {}
    if block_map:
        tallies = await _docapi.update_tallies(feishu_api, doc_id, block_map, rows)
        if tallies.get("ok") is not True:
            tally_note = f"tally update: {tallies.get('failures') or tallies.get('error')}"
        elif not tallies.get("updated"):
            # 一条都没改到 —— 多半是这份文档建于 tally 功能上线前, 映射里没有
            # tally 条目。此时报「成功」等于骗人: 文档里的小计会一直停在旧值。
            # 说清原因, 让人知道要重发一次卡(重建文档才会带上 tally 映射)。
            tally_note = (
                f"no tally blocks in this doc's block_map ({tallies.get('note') or 'nothing updated'}); "
                "resend the card to rebuild the doc with tally mapping"
            )

    card_note = ""
    entry_cards = state.get("entry_cards")
    card_mid = str((entry_cards or {}).get(target) or "") if isinstance(entry_cards, dict) else ""
    if card_mid:
        name = next((str(r.get("姓名") or "") for r in rows if r.get("姓名")), target)
        doc_link = await _docapi.fetch_doc_url(feishu_api, doc_id)
        card, _handlers = _card.entry_card(name, rows, doc_link, today)
        edited = _store._parse_result(
            await feishu_message_edit_card(card_mid, json.dumps(card, ensure_ascii=False), target)
        )
        if edited.get("ok") is not True:
            card_note = f"card redraw failed: {edited.get('message') or edited.get('error')}"
    else:
        card_note = "no entry card message_id in state; card not redrawn"

    result: dict[str, Any] = {
        "ok": not failures,
        "document_id": doc_id,
        "open_id": target,
        "ticked_in_doc": len(doc_state),
        "newly_synced": marked,
        "progress": f"{progress.done}/{progress.total}",
        "overview_updated": overview_updated,
    }
    if unclear:
        # 勾了「未完全理解」的项要让 HR 看见 —— 这正是拆两组勾选的目的:
        # 「读过」与「读懂了」不是一回事。
        result["unclear"] = unclear
    if role_note:
        result["role_note"] = role_note
    if role_choice:
        result["role_choice"] = role_choice
    if schedule_note:
        result["docsync_schedule"] = schedule_note
    # 重绘失败必须报出来: 表已经写对了, 但用户看的是卡片 —— 静默就等于
    # 「数字没更新」而没人知道为什么。
    if card_note:
        result["card_redraw"] = card_note
    if tally_note:
        result["doc_tally"] = tally_note
    if failures:
        result["failures"] = failures
    if truncated or read.get("truncated"):
        result["truncated"] = True
    return json.dumps(result, ensure_ascii=False)


async def _apply_role_choice(
    bitable: Any, app_token: str, detail_table: str, *, target: str, choice: str, today: date
) -> str:
    """把文档里勾的角色落到明细表: 打 适用角色 标签; 非研发再把 5 个开发项标不适用。

    返回空串表示成功, 否则是给人看的原因 —— 不抛异常, 因为角色落地失败不该让整次
    同步失败(勾选本身已经同步了)。

    与 rookie_sop_role_set 同一套规则: role_confirmed 自己不参与标签改写、也绝不能
    被标成不适用 —— 它是「角色已确认」这件事本身, 对研发和非研发都成立。
    """
    label = "研发" if choice == "dev" else "非研发"
    rows, _ = await _store.fetch_detail(bitable, app_token, detail_table, target)
    dev_rows = [
        r for r in rows if str(r.get("模块") or "") == _rt.DEV_MODULE and _store._item_id_of(r) != _doc.ROLE_ITEM_ID
    ]
    if dev_rows:
        raw = await bitable.update_records(
            app_token,
            detail_table,
            json.dumps(
                [{"record_id": r["record_id"], "fields": {"适用角色": label}} for r in dev_rows],
                ensure_ascii=False,
            ),
        )
        updated = _store._parse_result(raw)
        if updated.get("ok") is not True:
            return f"适用角色 update failed: {updated.get('message') or updated.get('error')}"

    if choice == "nondev":
        na = await _store.mark_module_na(
            bitable,
            app_token,
            detail_table,
            open_id=target,
            module=_rt.DEV_MODULE,
            today=today,
            exclude_item_ids=frozenset({_doc.ROLE_ITEM_ID}),
        )
        if na.get("ok") is not True:
            return f"mark_module_na failed: {na.get('error')}"
    return ""
