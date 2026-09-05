"""新人入职: 建明细/总览行 + 发入职卡 + 建每日催办定时任务。

主路径是 HR 对 agent 说「给某人发入职卡」——由 agent 把姓名解析成 open_id 后直接调
本工具(见 skills/feishu-rookie-onboarding/SKILL.md)。也支持从
feishu.hr.user_created 触发器 fire=tool 调用(Session 注入 event_payload_json),
这条路默认对真实新人不生效, 只是次要/兜底(见
triggers/rookie-sop-welcome/TRIGGER.md)。两种入口共享同一套 open_id/name 参数,
工具本身不关心调用方是 agent 还是触发器。
"""

from __future__ import annotations

# ruff: noqa: E402, RUF001
import contextlib
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_docapi as _docapi
import _rookie_sop_runtime as _rt
import _rookie_sop_store as _store
import _runtime_paths as _paths
from feishu_api import feishu_api
from feishu_message import feishu_message_edit_card, feishu_message_send_card
from schedule_manage import schedule_manage


def _rookie_workspace(open_id: str) -> str:
    """这个新人自己的 workspace 目录。

    从当前 workspace 的父目录(feishu-workspace-root, 形如 .../users)拼出
    ``<root>/<open_id>`` —— Gateway 就是按这个结构给每个飞书用户建 workspace 的。
    推不出来时回退空串, 由调用方沿用默认解析(单机/测试场景下两者本就同一个)。
    """
    if not open_id:
        return ""
    current = Path(_paths.workspace_dir())
    root = current.parent
    if not root.name or root.name == current.name:
        return ""
    candidate = root / open_id
    return str(candidate)


async def _upsert_schedule(**kwargs: Any) -> str:
    """建定时; 已存在就改用 patch 把参数刷一遍。

    刻意不只是「已存在就跳过」—— fresh_start 会删旧文档、建新文档, 而定时里的
    document_id 是创建时写死的。跳过就等于让它继续指向已删的文档, 同步每轮报
    ``read doc failed: resource deleted``, 那个人的进度从此不再更新。
    (实测踩过: 22 个定时里 2 个指向已删文档。)
    """
    created = await schedule_manage(action="create", **kwargs)
    if not (created.startswith("[Error]") and "already exists" in created):
        return created
    # 已存在: 用 patch 覆盖 cron/tool_args 等, 让它指向当前文档与正确的 workspace
    patched = await schedule_manage(action="patch", **kwargs)
    return created if patched.startswith("[Error]") else f"{patched} (patched existing)"


def _docsync_cron(open_id: str) -> str:
    """同步定时的 cron: 每 10 分钟一次, 但按 open_id 把起始分钟错开。

    刻意不用 ``*/10 * * * *`` —— 那让所有人都在 :00/:10/:20… 同一瞬间点火, 每人各
    读一次文档, 撞上飞书的读接口频率限制:
    ``read doc failed: request trigger frequency limit``。实测 20 人时最近 40 次
    同步里 14 次栽在这上面(能自愈, 下一轮补上, 但人越多越频繁)。

    改成 ``m-59/10``: 用 open_id 的稳定哈希取 0-9 的偏移, 于是同一个人每次算出的
    分钟固定(不会每次发卡都漂移), 而不同人分散在 10 个不同的分钟上。
    用 md5 而不是内置 hash(): 后者带进程级随机种子, 重启后同一个人会换到别的分钟,
    定时文件每次发卡都白改一遍。
    """
    offset = int(hashlib.md5(open_id.encode("utf-8")).hexdigest(), 16) % 10
    return f"{offset}-59/10 * * * *"


def _existing_doc_of(state: dict[str, Any], open_id: str) -> str:
    """这个新人名下已有的清单文档 token; 没有则空串。

    一人只该有一份文档。docs 索引是 {document_id: open_id}, 所以这里反查 ——
    若历史上误建了多份(force_resend 曾每次新建), 取 block_map 齐全的那一份:
    没有 block_map 的文档同步时无从对齐条目, 等于废文档。
    """
    docs = state.get("docs")
    if not isinstance(docs, dict):
        return ""
    mine = [str(d) for d, owner in docs.items() if str(owner) == open_id]
    if not mine:
        return ""
    maps = state.get("doc_block_maps")
    maps = maps if isinstance(maps, dict) else {}
    with_map = [d for d in mine if maps.get(d)]
    return (with_map or mine)[0]


async def rookie_sop_card_send(
    open_id: str = "",
    name: str = "",
    event_payload_json: str = "",
    onboard_date: str = "",
    force_resend: bool = False,
    fresh_start: bool = True,
) -> str:
    """Send a new hire the 入职卡 (entry card + per-person doc checklist).

    Primary caller: the agent, after resolving a name HR gave it (e.g. "给张三发
    入职卡") to an ``open_id``. Also callable with empty ``open_id``/``name`` from
    a ``feishu.hr.user_created`` trigger — Session injects ``event_payload_json``
    in that case — though that trigger path is secondary/fallback by default (see
    ``triggers/rookie-sop-welcome/TRIGGER.md``). Partially idempotent: a repeat
    call for the same person never re-seeds detail rows and never re-sends cards
    or re-creates the reminder schedule (both are skipped once detail rows
    already exist) — it only recomputes the overview row so it stays accurate.
    Pass ``force_resend=True`` to deliberately re-send the entry card and
    re-create the reminder schedule anyway (e.g. manual troubleshooting).

    Args:
        open_id: New hire Feishu open_id (ou_...). Empty → read from event_payload_json.
        name: Display name. Empty → from payload, else the open_id.
        event_payload_json: The event envelope payload (injected by Session).
        onboard_date: 'YYYY-MM-DD'; empty means today.
        force_resend: When true, re-send all module cards and re-create the
            reminder schedule even if detail rows already exist. Default False.
    """
    payload = _store._parse_result(event_payload_json) if event_payload_json else {}
    resolved_open_id = (open_id or "").strip() or str(payload.get("open_id") or "").strip()
    resolved_name = (name or "").strip() or str(payload.get("name") or "").strip() or resolved_open_id
    if not resolved_open_id:
        return json.dumps({"ok": False, "error": "open_id is required"}, ensure_ascii=False)

    try:
        onboard = datetime.strptime(onboard_date.strip(), "%Y-%m-%d").date() if onboard_date.strip() else date.today()
    except ValueError:
        return json.dumps({"ok": False, "error": f"invalid onboard_date {onboard_date!r}"}, ensure_ascii=False)

    cfg = await _store.load_config()
    items = _cfg.load_sop(cfg)
    if not items:
        return json.dumps({"ok": False, "error": "config/rookie_sop.yaml has no items"}, ensure_ascii=False)

    # 新人自己的 workspace —— 不能用 resolve_workspace() 的默认值: HR 说
    # 「给某人发入职卡」时本工具跑在 HR 的 session 里, 默认解析出的是 HR 的目录,
    # state 会落到 HR 名下(实测: 罗霖发卡, state 写进了他自己的 users/ 目录),
    # 于是新人那侧的定时同步与催办都找不到数据, 且每个 HR 各攒一份、同一个新人
    # 被不同 HR 发卡就分裂成多份表。
    target_workspace = _rookie_workspace(resolved_open_id)

    state = await _rt.ensure_base(cfg, target_workspace)
    missing = [k for k in ("app_token", "detail_table_id", "overview_table_id") if not state.get(k)]
    if missing:
        return json.dumps(
            {"ok": False, "error": f"bitable base unavailable, missing {missing}: {state}"}, ensure_ascii=False
        )

    bitable = _rt.bitable_adapter()
    app_token = str(state["app_token"])
    detail_table = str(state["detail_table_id"])
    overview_table = str(state["overview_table_id"])

    # 幂等: 已有明细行就不再建, 免得重复入职事件写出两套
    rows, truncated = await _store.fetch_detail(bitable, app_token, detail_table, resolved_open_id)
    is_first_send = not rows
    if is_first_send:
        seed_raw = await bitable.create_records(
            app_token,
            detail_table,
            json.dumps(
                [
                    _store.detail_row_fields(i, open_id=resolved_open_id, name=resolved_name, onboard=onboard)
                    for i in items
                ],
                ensure_ascii=False,
            ),
        )
        # 不查 ok 就往下走是致命的: 播种被飞书拒绝时明细表还是空的, 但下面仍会
        # 当满员发出模块卡与建定时 —— 新人看见卡却点不出任何已存在的行, 且后续
        # 事件因 is_first_send 已被此次(失败的)调用判过而永远不会再播种一次。
        seed_ok, seed_error = _store._write_ok(seed_raw)
        if not seed_ok:
            return json.dumps({"ok": False, "error": f"create_records rejected: {seed_error}"}, ensure_ascii=False)
        rows, truncated_2 = await _store.fetch_detail(bitable, app_token, detail_table, resolved_open_id)
        truncated = truncated or truncated_2

    today = date.today()
    await _store.recompute_overview(
        bitable,
        app_token,
        overview_table,
        open_id=resolved_open_id,
        name=resolved_name,
        role="",
        rows=rows,
        today=today,
    )

    # 卡片与催办定时同样幂等: 重复事件不该把两套卡都摆在新人面前(旧卡仍可点,
    # 会跟新卡的行竞争), 也不该把提醒任务重建一遍。只有首次发或显式 force_resend
    # 才走下面这一段。
    if not _rt.should_send_cards(is_first_send=is_first_send, force_resend=force_resend):
        return json.dumps(
            {
                "ok": True,
                "open_id": resolved_open_id,
                "items": len(items),
                "cards_sent": [],
                "cards_skipped": "detail rows already existed; pass force_resend=True to resend",
            },
            ensure_ascii=False,
        )

    sop_url = str(cfg.get("sop_doc_url") or "")

    # 详情页: 为这个新人建一份自己的清单文档, 只授权他本人, 并订阅其变更。
    # 刻意为之: 原先一次性发 7 张模块卡, 用户反馈观感太糟 —— 改成一张入口卡 +
    # 一个跳转按钮。33 项在文档里一屏勾完, 勾选由文档变更事件同步回明细表。
    # 文档权限能精确到「单文档 + 单人」(bitable 最细只到 base 级, 做不到),
    # 所以每人只看得到自己那一份。
    # 一人一份文档, 严格幂等: 已经有了就复用, 只重发卡片。
    #
    # 刻意为之: 原先每次调用都新建一份文档, force_resend 于是造出第二份 ——
    # 定时任务记住了新那份, 而用户手里的卡片链接还指向旧那份。结果同步「成功」
    # 但读的是空白新文档, 把「什么都没勾」如实写回表, 用户看到的进度永远不动。
    # (实测踩过, 而且返回值是 ok:true, 最难发现。)
    # HR 点名发卡 = 重新开始, 所以先把这个人的进度清零、并弃用旧文档;
    # 第二天的自动催办卡则沿用现有文档与进度(它只是提醒, 不是重新入职)。
    # 两条路径靠 fresh_start 区分: HR 走 rookie_sop_card_send(默认 True),
    # 催办走 rookie_sop_remind 自己的 remind_card, 压根不经过这里。
    existing_doc = "" if fresh_start else _existing_doc_of(state, resolved_open_id)
    # 待弃用的旧文档。**必须在分支外先初始化** —— 下面无条件读它, 只在 fresh_start
    # 分支里赋值会让 fresh_start=False 那条路直接 UnboundLocalError。
    # (实测踩过: 用 fresh_start=False 批量重发 6 个人, 6 个全挂在这一行; 好在异常
    #  发生在任何写入之前, 没人的进度被动过。)
    stale_docs: list[str] = []
    if fresh_start:
        reset = await _store.reset_progress(bitable, app_token, detail_table, open_id=resolved_open_id)
        if reset.get("ok") is not True:
            return json.dumps(
                {"ok": False, "error": f"reset progress failed: {reset.get('error')}"}, ensure_ascii=False
            )
        # 清零后必须重取 rows —— 上面那份是清零**之前**读的, 下面 provision_doc 用它
        # 渲染文档, 用旧值就会把已勾选状态照搬进新文档: 表清干净了、文档还是满的。
        # (实测踩过: fresh_start 重发后表里 28 未完成, 新文档却仍显示 27/32 已勾。)
        rows, truncated = await _store.fetch_detail(bitable, app_token, detail_table, resolved_open_id)
        # 旧文档留着会让新人对着一份已勾满的清单, 且它的 block_map 还在 state 里,
        # 同步会把旧勾选又写回表 —— 所以连同映射一起弃用, 下面会重建一份。
        #
        # 但删之前必须先把**旧卡**的按钮改指到新文档, 否则新人手上那张卡的链接会
        # 变成死链: 飞书对已删除和无权限用同一句「文档已被删除」提示, 他只会以为
        # 东西丢了。(实测踩过: HR 给同一个人发了两次卡, 第一份文档被删, 对方点旧
        # 卡就报文档已删除。)
        #
        # 顺序: 先建新文档 → 改旧卡指向它 → 再删旧文档。所以这里只收集待删列表,
        # 真正的删除挪到新文档就位、旧卡改好之后。
        stale_docs = [d for d, o in (state.get("docs") or {}).items() if str(o) == resolved_open_id]
    if existing_doc:
        doc = {
            "document_id": existing_doc,
            "url": await _docapi.fetch_doc_url(feishu_api, existing_doc),
            "block_map": (state.get("doc_block_maps") or {}).get(existing_doc) or {},
            "reused": True,
        }
    else:
        doc = await _docapi.provision_doc(
            feishu_api,
            open_id=resolved_open_id,
            name=resolved_name,
            rows=rows,
            sop_url=sop_url,
        )
    if not doc.get("document_id"):
        return json.dumps({"ok": False, "error": f"provision doc failed: {doc.get('error')}"}, ensure_ascii=False)
    doc_url = str(doc.get("url") or "")

    # 新文档已就位: 先把旧卡的按钮改指到它, 再删旧文档。
    # 反过来做(先删后改)会留下一段时间窗, 新人正好在这时点旧卡就看到「文档已被
    # 删除」—— 而飞书对已删除与无权限用同一句提示, 他无从判断是系统问题还是自己
    # 没权限。edit_card 能改按钮 url(实测 ok), 所以这个窗口完全可以避掉。
    relink_note = ""
    if stale_docs:
        old_mid = str((state.get("entry_cards") or {}).get(resolved_open_id) or "")
        if old_mid:
            old_card, _old_handlers = _card.entry_card(resolved_name, rows, doc_url, today)
            relinked = _store._parse_result(
                await feishu_message_edit_card(old_mid, json.dumps(old_card, ensure_ascii=False), resolved_open_id)
            )
            if relinked.get("ok") is not True:
                # 改不动旧卡就别删旧文档 —— 宁可留一份孤儿文档, 也不要给新人一个死链接
                relink_note = f"old card relink failed: {relinked.get('message') or relinked.get('error')}"
                stale_docs = []
        for stale in stale_docs:
            with contextlib.suppress(Exception):
                await feishu_api(
                    "DELETE",
                    f"/open-apis/drive/v1/files/{stale}",
                    query_json=json.dumps({"type": "docx"}, ensure_ascii=False),
                )

    # 文档索引: 同步工具靠它反查是谁的清单。
    # 一人只留一条 —— 先清掉这个人名下的旧条目, 再写当前这份, 否则 docs 里会
    # 同时存在多份、反查取到哪一份取决于字典顺序(上面那个 bug 的根源)。
    state_docs = state.get("docs")
    state_docs = {
        d: o for d, o in (state_docs or {}).items() if isinstance(state_docs, dict) and str(o) != resolved_open_id
    }
    state_docs[str(doc["document_id"])] = resolved_open_id
    state["docs"] = state_docs
    # block_id → "item_id:role" 映射: 同步时靠它认条目, 所以文档正文里不写 item 标记。
    # 没有它同步就无从对齐, 所以与 docs 索引一起存。
    block_maps = state.get("doc_block_maps")
    block_maps = dict(block_maps) if isinstance(block_maps, dict) else {}
    block_maps[str(doc["document_id"])] = doc.get("block_map") or {}
    state["doc_block_maps"] = block_maps
    await _rt.save_state(state, target_workspace)

    # 入口卡: 一条消息交代全貌, 没有回调动作(勾选在文档里做)。
    card, handlers = _card.entry_card(resolved_name, rows, doc_url, today)
    business = {
        "type": "rookie_sop",
        "open_id": resolved_open_id,
        "name": resolved_name,
        "module": "入口",
        "app_token": app_token,
        "detail_table_id": detail_table,
        "overview_table_id": overview_table,
        "document_id": doc["document_id"],
    }
    # 发卡结果不能丢: 飞书会因卡片 JSON 不合规整张拒收(实测踩过 200410/11310),
    # 吞掉返回值就会把「卡被拒」报成「已发送」, 新人什么都没收到而工具说成功了。
    sent_raw = await feishu_message_send_card(
        resolved_open_id,
        json.dumps(card, ensure_ascii=False),
        "open_id",
        "",
        json.dumps(business, ensure_ascii=False),
        json.dumps(handlers, ensure_ascii=False),
        False,
    )
    sent_result = _store._parse_result(sent_raw)
    sent: list[str] = []
    failed: list[dict[str, str]] = []
    # 分支外初始化: 卡没发出去 / 没标加急时下面仍要读它(与 stale_docs 踩过的同一个坑)
    urgent_note = ""
    if sent_result.get("ok") is not True:
        failed.append(
            {
                "module": "入口卡",
                "error": str(sent_result.get("message") or sent_result.get("error") or "send_card failed"),
            }
        )
    else:
        sent.append("入口卡")
        # 记住入口卡的 message_id —— 同步后要靠它把卡上的进度重绘。
        # 没有它, 表里数字变了而卡片永远停在发出时的那一刻(实测踩过: 用户反馈
        # 「卡片与文档的数字都没有更新」, 因为同步工具只写表、从不回头更新卡片)。
        card_mid = str(sent_result.get("message_id") or "")
        if card_mid:
            state["entry_cards"] = {
                **{
                    d: m
                    for d, m in (state.get("entry_cards") or {}).items()
                    if isinstance(state.get("entry_cards"), dict) and d != resolved_open_id
                },
                resolved_open_id: card_mid,
            }
            await _rt.save_state(state, target_workspace)

        # 标为加急 —— 飞书会给应用内强提醒弹窗, 普通卡片混在日常消息里太容易被划过去
        # (用户反馈「很多人都没看见推送的卡片」)。
        # 用 urgent_app 而不是 urgent_phone: 应用内弹窗已经足够醒目, 打电话给新人
        # 发一份入职清单不成比例。
        # 加急失败不算发卡失败 —— 卡本身已经送到了, 只是少一次弹窗, 所以只记不拦。
        if card_mid:
            urged = _store._parse_result(
                await feishu_api(
                    "PATCH",
                    f"/open-apis/im/v1/messages/{card_mid}/urgent_app",
                    query_json=json.dumps({"user_id_type": "open_id"}, ensure_ascii=False),
                    body_json=json.dumps({"user_id_list": [resolved_open_id]}, ensure_ascii=False),
                )
            )
            if urged.get("ok") is not True:
                urgent_note = str(urged.get("message") or urged.get("error") or "urgent_app failed")

    # 每人一份催办定时任务, 落在这个新人自己的 Session workspace 里。结果不能丢:
    # schedule_manage 失败时返回 "[Error] ..." 字符串而不是抛异常, 吞掉它就等于
    # 新人从此收不到提醒却没有任何人知道。重复调用(force_resend 场景)大概率撞见
    # "already exists", 这是预期内的, 不算失败。
    schedule_result = await _upsert_schedule(
        schedule_name=f"rookie-remind-{resolved_open_id[-8:]}",
        cron="0 9 * * *",
        fire="tool",
        tool="rookie_sop_remind",
        # workspace 也要写进参数: 定时触发时框架不建路径上下文, 工具不传就会读到
        # 触发方的 state(另一个人的多维表格)。
        tool_args=json.dumps({"open_id": resolved_open_id, "workspace": target_workspace}, ensure_ascii=False),
        visibility="silent",
        description=f"{resolved_name} 入职 SOP 每日催办",
        # 建到**新人自己**的 workspace, 不是 HR 的 —— 默认会落在调用方(HR)名下,
        # 那样定时触发时工具读的是 HR 的 state: 同步报「no block map」、催办查的
        # 是另一个人的多维表格。实测踩过两轮。
        workspace=target_workspace,
    )
    schedule_failed = schedule_result.startswith("[Error]") and "already exists" not in schedule_result

    # 入职当天每 10 分钟把详情页文档的勾选同步进表, 让进度接近实时。
    #
    # 为什么不靠事件: 文档变更事件走不通, 而且是权限模型的死结, 不是配置问题 ——
    # 实测机器人**只能订阅自己拥有的**文档(靠 owner 身份; 把它加成协作者也没用,
    # subscribe 依然 forbidden), 而它自己拥有的文档, 编辑事件又不推给它。
    # 能订阅的收不到, 收得到的订不上, 所以只能拉取。
    #
    # 为什么只在当天高频: 入职当天新人最活跃, 值得每 10 分钟对齐; 过了当天,
    # rookie_sop_sync_doc 会把这个定时自删, 之后靠 9:00 催办前那次同步兜底 ——
    # 高频窗口因此限制在一天内, 不是长期空轮询。
    sync_schedule = await _upsert_schedule(
        schedule_name=f"rookie-docsync-{resolved_open_id[-8:]}",
        cron=_docsync_cron(resolved_open_id),
        fire="tool",
        tool="rookie_sop_sync_doc",
        # 把 document_id 与 workspace 都写死进参数, 不让定时那条路径依赖运行时上下文:
        # 框架的 schedule_registry._fire_tool 直接 await func(**args), 不建立
        # runtime_scope, 所以工具里 resolve_workspace() 会回落到 agent 包目录 ——
        # load_state() 读不到这个新人的 state, 反查 docs 索引必然失败。
        # (实测踩过: 定时每 10 分钟准时触发, 但每次都返回
        #  "open_id is not in the doc index", 进度从来没更新过。)
        tool_args=json.dumps(
            {
                "open_id": resolved_open_id,
                "document_id": str(doc["document_id"]),
                # 必须是新人的 workspace —— 这里原先用 resolve_workspace() 取到的是
                # HR 的目录(发卡跑在 HR 会话里), 定时到点就去 HR 那儿找数据。
                "workspace": target_workspace,
            },
            ensure_ascii=False,
        ),
        visibility="silent",
        description=f"{resolved_name} 入职清单同步（当天每 10 分钟，之后自删）",
        workspace=target_workspace,
    )
    sync_failed = sync_schedule.startswith("[Error]") and "already exists" not in sync_schedule

    result: dict[str, Any] = {
        # 有任何一张卡没发出去就不能报 ok —— 否则模型会告诉新人「卡片已送达」,
        # 而对方一张都没收到。
        "ok": not schedule_failed and not sync_failed and not failed,
        "open_id": resolved_open_id,
        "items": len(items),
        "cards_sent": sent,
        "schedule": schedule_result,
        "sync_schedule": sync_schedule,
    }
    # 旧卡改不动就没删旧文档 —— 必须报出来, 否则会留下一份无人知晓的孤儿文档
    if relink_note:
        result["old_card_relink"] = relink_note
    # 加急没成功要报出来: 卡是送到了, 但少了那次弹窗, 新人很可能又划过去
    if urgent_note:
        result["urgent"] = urgent_note
    if failed:
        result["cards_failed"] = failed
    if truncated:
        result["truncated"] = True
    return json.dumps(result, ensure_ascii=False)
