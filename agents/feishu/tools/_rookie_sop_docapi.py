"""文档详情页的运行时: 建文档、写块、读块、授权、订阅变更 —— 唯一碰飞书文档接口的模块。

刻意为之: 所有飞书调用通过注入的 `api` 可调用对象(签名与 feishu_api 工具一致),
这样单测传 fake 就能跑, 不需要凭据 —— 与 _rookie_sop_store 对 bitable 的做法一致。

一人一份文档: 建好后只把这个新人授权成 edit, 别人无权访问。文档权限能精确到
「单个文档 + 单个人」(实测), 这是 bitable 做不到的(它最细只到 base 级)。
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_doc as _doc

_MAX_BLOCKS_PER_CALL = 50


def _parsed(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, TypeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    d = payload.get("data")
    return d if isinstance(d, dict) else {}


async def create_doc(api: Any, title: str) -> dict[str, Any]:
    """建一个空文档, 返回 {ok, document_id, url}。"""
    res = _parsed(
        await api("POST", "/open-apis/docx/v1/documents", body_json=json.dumps({"title": title}, ensure_ascii=False))
    )
    if res.get("ok") is not True:
        return {"ok": False, "error": str(res.get("msg") or res.get("message") or "create document failed")}
    doc = _data(res).get("document")
    document_id = str((doc or {}).get("document_id") or "")
    if not document_id:
        return {"ok": False, "error": f"no document_id in response: {res}"}
    return {"ok": True, "document_id": document_id, "url": await fetch_doc_url(api, document_id)}


def doc_url(document_id: str) -> str:
    """回退用的通用链接。

    刻意为之: 正式链接要用 fetch_doc_url() 从飞书查, 因为每个租户有自己的域名
    (本租户是 genuineknowledge.feishu.cn)。硬编码 feishu.cn 生成的链接新人点开
    是「页面不存在」—— 实测踩过。这里只作为查询失败时的兜底。
    """
    return f"https://feishu.cn/docx/{document_id}"


async def fetch_doc_url(api: Any, document_id: str) -> str:
    """问飞书要这份文档的真实链接(带租户域名); 查不到时回退到通用链接。"""
    try:
        res = _parsed(
            await api(
                "POST",
                "/open-apis/drive/v1/metas/batch_query",
                body_json=json.dumps(
                    {"request_docs": [{"doc_token": document_id, "doc_type": "docx"}], "with_url": True},
                    ensure_ascii=False,
                ),
            )
        )
        metas = _data(res).get("metas") or res.get("metas") or []
        for meta in metas:
            if isinstance(meta, dict):
                url = str(meta.get("url") or "").strip()
                if url:
                    return url
    except Exception:  # 查链接失败不该让整次发卡失败
        pass
    return doc_url(document_id)


async def append_blocks(
    api: Any, document_id: str, blocks: list[dict[str, Any]], parent_block_id: str = ""
) -> dict[str, Any]:
    """把块追加到 *parent_block_id*(默认文档根节点)。飞书对单次子块数有上限, 故分批。

    ``parent_block_id`` 留空时追加到文档根 —— 普通条目与阅读类条目的表格都走这条路。
    传具体 block_id 时追加到该块下, 目前只用来把理解勾选的 todo 塞进表格读回后
    发现的格子(block_type 32)里(见 _rookie_sop_doc.build_doc_blocks 的 slots 说明
    与 provision_doc), 因为飞书建表格时不会把两个格子的 block_id 一并给出。
    """
    if not blocks:
        return {"ok": True, "written": 0, "todo_block_ids": [], "table_block_ids": [], "children": []}
    parent = parent_block_id.strip() or document_id
    written = 0
    # 飞书在返回体里给出每个新建块的 block_id —— 收集 todo/表格块的 id, 用来建
    # block_id → item_id 映射, 于是文档正文不必写任何 item 标记(见 _rookie_sop_doc)。
    todo_block_ids: list[str] = []
    all_children: list[dict[str, Any]] = []  # 保序的原始 children, 供按位配对用(见 provision_doc)
    for start in range(0, len(blocks), _MAX_BLOCKS_PER_CALL):
        chunk = blocks[start : start + _MAX_BLOCKS_PER_CALL]
        res = _parsed(
            await api(
                "POST",
                f"/open-apis/docx/v1/documents/{document_id}/blocks/{parent}/children",
                body_json=json.dumps({"children": chunk}, ensure_ascii=False),
            )
        )
        if res.get("ok") is not True:
            return {
                "ok": False,
                "error": str(res.get("msg") or res.get("message") or "append blocks failed"),
                "written": written,
                "todo_block_ids": todo_block_ids,
                "children": all_children,
            }
        children = _data(res).get("children") or res.get("children") or []
        for child in children:
            if not isinstance(child, dict):
                continue
            bid = str(child.get("block_id") or "")
            if not bid:
                continue
            all_children.append(child)
            if child.get("block_type") == _doc.BLOCK_TODO:
                todo_block_ids.append(bid)
        written += len(chunk)
    return {
        "ok": True,
        "written": written,
        "todo_block_ids": todo_block_ids,
        "children": all_children,
    }


async def read_blocks(api: Any, document_id: str) -> dict[str, Any]:
    """读回文档全部块 —— 翻页直到 has_more 为假, 同时防不动点循环。

    与 fetch_detail 同一套守卫: 上限 + 已见 token 集合, 否则服务端若一直回
    has_more=true 就会无限转(fire=tool 的同步会挂死整个回合)。
    """
    blocks: list[dict[str, Any]] = []
    seen: set[str] = {""}
    token = ""
    for _ in range(50):
        query: dict[str, Any] = {"page_size": 500}
        if token:
            query["page_token"] = token
        res = _parsed(
            await api(
                "GET",
                f"/open-apis/docx/v1/documents/{document_id}/blocks",
                query_json=json.dumps(query, ensure_ascii=False),
            )
        )
        if res.get("ok") is not True:
            return {"ok": False, "error": str(res.get("msg") or res.get("message") or "read blocks failed")}
        payload = _data(res) or res
        items = payload.get("items")
        if isinstance(items, list):
            blocks.extend(b for b in items if isinstance(b, dict))
        nxt = str(payload.get("page_token") or "")
        if not payload.get("has_more") or not nxt or nxt in seen:
            break
        seen.add(nxt)
        token = nxt
    else:
        return {"ok": True, "blocks": blocks, "truncated": True}
    return {"ok": True, "blocks": blocks}


async def grant_edit(api: Any, document_id: str, open_id: str) -> dict[str, Any]:
    """把文档授权给这个新人(edit, 他要能勾)。

    参数位置是飞书的坑: `type`(文件类型)必须在 query, 而 body 里的 `type` 是
    **成员类别**、只认 user/chat/department/group —— 端点表会在发请求前拦下写错的组合。
    """
    res = _parsed(
        await api(
            "POST",
            f"/open-apis/drive/v1/permissions/{document_id}/members",
            query_json=json.dumps({"type": "docx", "need_notification": "true"}, ensure_ascii=False),
            body_json=json.dumps(
                {"member_type": "openid", "member_id": open_id, "perm": "edit", "type": "user"},
                ensure_ascii=False,
            ),
        )
    )
    if res.get("ok") is not True:
        return {"ok": False, "error": str(res.get("msg") or res.get("message") or "grant failed")}
    return {"ok": True}


async def subscribe_changes(api: Any, document_id: str) -> dict[str, Any]:
    """订阅这份文档的变更事件 —— 勾选后由事件驱动同步, 不靠轮询。

    刻意为之: 事件只说「这个文档被编辑了」, 不说哪一项被勾。所以收到事件后仍要
    整份读回来对比。好处是只在真有变更时才干活: 10 个新人若用 5 分钟轮询是每天
    2880 次调用, 事件驱动只有实际勾选那几十次。
    """
    res = _parsed(
        await api(
            "POST",
            f"/open-apis/drive/v1/files/{document_id}/subscribe",
            query_json=json.dumps({"file_type": "docx"}, ensure_ascii=False),
        )
    )
    if res.get("ok") is not True:
        return {"ok": False, "error": str(res.get("msg") or res.get("message") or "subscribe failed")}
    return {"ok": True}


def _tracked_children(children: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """筛出 children 里的 todo/表格块, 保持出现顺序 —— 用来跟 slots 按位配对。

    与 append_blocks 里收集 todo_block_ids/table_block_ids 同一份 children,
    但这里要保序: 一个 slot 项对应「一个 todo」或「一个表格」, 顺序错了配对就全错。
    """
    out: list[tuple[int, str]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        block_type = child.get("block_type")
        bid = str(child.get("block_id") or "")
        # 跟踪两类块: todo(可勾选条目) 与 heading2(分节小计「到岗准备 3/5」)。
        # 小计块也要进映射, 否则同步后没法把它改成最新值 —— 用户勾完会看到条目
        # 划掉了、分节标题却还停在 0/5。
        if bid and block_type in (_doc.BLOCK_TODO, _doc.BLOCK_HEADING2):
            out.append((block_type, bid))
    return out


async def update_tallies(
    api: Any, document_id: str, block_map: dict[str, str], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """把文档里各分节的小计「x/y」改成最新值。

    刻意为之: 小计是建文档时算好写死的, 同步只改 todo 的勾选状态, 不碰标题 ——
    于是用户勾完会看到条目划掉了、分节标题却还停在 0/5(实测反馈过)。
    这里按 block_map 里 role=tally 的条目反查模块名, 重算后逐块 PATCH。
    单块失败不影响其余块: 小计只是显示, 一个没改对不该让整次同步失败。
    """
    tallies = {
        bid: mapped.rsplit(":", 1)[0] for bid, mapped in block_map.items() if mapped.endswith(f":{_doc.ROLE_TALLY}")
    }
    if not tallies:
        return {"ok": True, "updated": 0, "note": "no tally blocks in map"}

    updated = 0
    failures: list[str] = []
    for bid, module in tallies.items():
        module_rows = [r for r in rows if str(r.get("模块") or "") == module]
        if not module_rows:
            continue
        done_n = sum(1 for r in module_rows if str(r.get("状态") or "") == "已完成")
        res = _parsed(
            await api(
                "PATCH",
                f"/open-apis/docx/v1/documents/{document_id}/blocks/{bid}",
                body_json=json.dumps(
                    {
                        "update_text_elements": {
                            "elements": [
                                {"text_run": {"content": f"{_doc.module_emoji(module)} {module}"}},
                                {
                                    "text_run": {
                                        "content": f"\u3000{done_n}/{len(module_rows)}",
                                        "text_element_style": {"text_color": 5},
                                    }
                                },
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
            )
        )
        if res.get("ok") is True:
            updated += 1
        else:
            failures.append(f"{module}: {res.get('msg') or res.get('message')}")
    out: dict[str, Any] = {"ok": not failures, "updated": updated}
    if failures:
        out["failures"] = failures
    return out


async def provision_doc(
    api: Any,
    *,
    open_id: str,
    name: str,
    rows: list[dict[str, Any]],
    sop_url: str = "",
) -> dict[str, Any]:
    """为一个新人备好详情页: 建文档 → 写清单 → 授权给他 → 订阅变更。

    每一步的失败都往上报, 不吞 —— 授权失败意味着新人打不开自己的清单,
    订阅失败意味着他勾了没人知道, 两者都不能当成功。

    阅读类条目的两个理解勾选放进一张 1x2 表格的两个格子里(见 _rookie_sop_doc), 而建表
    的返回体不会给出格子的 block_id —— 必须**读回文档**才能拿到。所以这里比纯 todo
    版多两轮调用: 建完所有根节点块后, 若有阅读类条目, 统一读一次文档(不管有几个阅读类
    条目, 只读一次), 拿到每张表各自的两个格子 block_id; 再各往两个格子里追加一个 todo
    (这一步没法合并 —— 两个格子是两个不同的父块, 飞书的 /children 端点一次只认一个
    父块)。一个必读条目因此多花 2 次追加调用, 3 个必读条目就是 6 次, 加上共享的那 1 次
    读回, 比旧版(所有 todo 一次批量追加完事)多 7 次调用 —— 这是为了让两个理解勾选
    并排在同一行而接受的代价。

    block_id → "item_id:role" 的配对绝不靠猜: slots 与「根节点追加返回的 todo/表格块」
    按顺序一一配对, 数量或类型不对就直接报错退出, 不会把状态同步错到别的条目上;
    表格格子数不是 2、或格子里追加的 todo 数不是 1, 同样报错退出。
    """
    created = await create_doc(api, f"{name} · 入职卡")
    if created.get("ok") is not True:
        return created
    document_id = str(created["document_id"])

    blocks, slots = _doc.build_doc_blocks(rows, name=name, sop_url=sop_url)
    appended = await append_blocks(api, document_id, blocks)
    if appended.get("ok") is not True:
        return {"ok": False, "error": f"blocks: {appended.get('error')}", "document_id": document_id}

    tracked = _tracked_children(appended.get("children") or [])
    if len(tracked) != len(slots):
        return {
            "ok": False,
            "error": f"todo/table block count mismatch: got {len(tracked)}, expected {len(slots)}",
            "document_id": document_id,
        }

    # 按顺序配对 slots 与实际建出的块。两类 slot 期望不同的块类型:
    #   ROLE_TALLY → heading2(分节小计「到岗准备 3/5」那一行, 同步后要改它)
    #   其余       → todo(可勾选的条目)
    # 类型不符就报错而不是硬配 —— 映射错位会把勾选写到别的条目上。
    block_map: dict[str, str] = {}
    for (block_type, bid), (item_id, role) in zip(tracked, slots, strict=True):
        expected = _doc.BLOCK_HEADING2 if role == _doc.ROLE_TALLY else _doc.BLOCK_TODO
        if block_type != expected:
            return {
                "ok": False,
                "error": (f"expected block_type {expected} for {item_id}:{role}, got {block_type}"),
                "document_id": document_id,
            }
        block_map[bid] = f"{item_id}:{role}"

    granted = await grant_edit(api, document_id, open_id)
    subscribed = await subscribe_changes(api, document_id)
    return {
        "ok": granted.get("ok") is True and subscribed.get("ok") is True,
        "document_id": document_id,
        # 用飞书给的真实链接(带租户域名), 不用硬编码的通用域名
        "url": await fetch_doc_url(api, document_id),
        "blocks_written": appended.get("written"),
        "block_map": block_map,
        "granted": granted.get("ok") is True,
        "grant_error": granted.get("error", ""),
        "subscribed": subscribed.get("ok") is True,
        "subscribe_error": subscribed.get("error", ""),
    }
