"""Send meeting-session outputs to the fixed business recipients."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import _feishu_api_impl as _api
import _feishu_impl as _f
import anyio
from _meeting_automation import atomic_write_text, automation_runtime, meeting_artifact_root, notify_recipients

from psi_agent._appdata import resolve_appdata_root

# 单段上限来自 meeting-automation.yaml runtime.notify.chunk_chars (测试可覆写)
MAX_NOTIFICATION_CHARS = int(automation_runtime()["notify"]["chunk_chars"])
_RECEIPT_LOCKS: dict[str, anyio.Lock] = {}
_RECEIPT_LOCKS_GUARD = anyio.Lock()

#: receive_id 类型。``ou_``/``oc_``/``on_`` 三种前缀能自证类型; 租户级 ``user_id``
#: (如 ``dg429f6d``)与邮箱没有前缀, 必须显式写成 ``user_id:<值>`` / ``email:<值>``。
_INFERABLE_ID_PREFIXES = (("oc_", "chat_id"), ("ou_", "open_id"), ("on_", "union_id"))
_EXPLICIT_ID_TYPES = ("open_id", "user_id", "union_id", "chat_id", "email")


def _split_typed_id(value: str) -> tuple[str, str] | None:
    """``"user_id:dg429f6d"`` / ``"ou_xxx"`` → ``(receive_id_type, id)``; 不是 id 就 None。

    为什么类型必须显式: 租户级 ``user_id`` 没有前缀, 一旦被当成人名丢进通讯录查, 报出来
    的是"查无此人"而不是"类型不对"——最容易让人误以为配置里那个人不存在。而
    ``open_id``/``chat_id`` 是**按应用隔离**的(换个应用就失效, 飞书报 ``99992361
    open_id cross app``), ``user_id``/``union_id`` 是**租户级**的、跨应用不变。想用哪种
    由配置写明, 代码不猜。
    """
    for kind in _EXPLICIT_ID_TYPES:
        prefix = f"{kind}:"
        if value.startswith(prefix) and value[len(prefix) :].strip():
            return kind, value[len(prefix) :].strip()
    for prefix, kind in _INFERABLE_ID_PREFIXES:
        if value.startswith(prefix):
            return kind, value
    return None


async def _resolve_with_bot(name: str) -> tuple[str, str]:
    """Resolve an exact member name with the bot's tenant token.

    The global ``search/v1/user`` endpoint is user-token-only in many tenants,
    which is not available to the fixed scheduler Session.  The existing
    department roster helper uses the tenant token and walks the whole org when
    requested, so use it as the bot-side fallback instead of guessing an
    identity or routing through the triggering user's session.
    """
    try:
        response = await _f.list_department_members_impl(
            department_id="0",
            department_id_type="open_department_id",
            user_id_type="open_id",
            recursive=True,
        )
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"
    if not isinstance(response, dict) or not response.get("ok"):
        return "", str((response or {}).get("message") or (response or {}).get("error") or "按通讯录查询失败")
    users = response.get("members") or []
    exact = []
    for item in users if isinstance(users, list) else []:
        if not isinstance(item, dict):
            continue
        display = str(item.get("name") or item.get("display_name") or "").strip()
        identity = str(item.get("open_id") or item.get("user_id") or item.get("id") or "").strip()
        if display == name and identity.startswith("ou_"):
            exact.append((identity, display))
    if len(exact) == 1:
        return exact[0]
    return "", f"未找到姓名为“{name}”的唯一成员"


def _chat_items(response: Any) -> list[dict[str, Any]]:
    """从 chats 接口回包里取出 items (兼容 data.items 嵌套)。"""
    if not isinstance(response, dict):
        return []
    raw_items: Any = response.get("items")
    if not isinstance(raw_items, list):
        data = response.get("data")
        raw_items = data.get("items") if isinstance(data, dict) else []
    found: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                found.append(item)
    return found


def _exact_chat_match(items: list[dict[str, Any]], name: str) -> tuple[str, str]:
    """群名精确匹配; 命中唯一一个才返回 chat_id。"""
    matches: list[tuple[str, str]] = []
    for item in items:
        display = str(item.get("name") or item.get("chat_name") or "").strip()
        chat_id = str(item.get("chat_id") or item.get("id") or "").strip()
        if display == name and chat_id.startswith("oc_"):
            matches.append((chat_id, display))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return "", f"群名“{name}”匹配到 {len(matches)} 个群聊, 请在配置里直接写 chat_id"
    return "", f"未找到群名为“{name}”的唯一群聊"


async def _search_chat_by_name(name: str) -> tuple[str, str]:
    """按群名走 chats/search (只覆盖机器人可见的群)。"""
    try:
        response = await _api.call_api_impl(
            method="GET",
            uri="/open-apis/im/v1/chats/search",
            query_json=json.dumps({"query": name, "page_size": 50}, ensure_ascii=False),
            prefer="tenant",
        )
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"
    if not isinstance(response, dict) or not response.get("ok"):
        return "", str((response or {}).get("message") or (response or {}).get("error") or "按群名查询失败")
    return _exact_chat_match(_chat_items(response), name)


async def _list_bot_chats() -> tuple[list[dict[str, Any]], str]:
    """机器人所在群列表(分页最多 5 页)。

    搜索索引未收录、但机器人确已在群里的场景 (群刚建立/刚被拉进群) 只有列表接口看得到 ——
    生产上它表现为「群名解析不到 → 纪要发不进群」。
    """
    items: list[dict[str, object]] = []
    page_token = ""
    for _ in range(5):
        query: dict[str, object] = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        try:
            response = await _api.call_api_impl(
                method="GET",
                uri="/open-apis/im/v1/chats",
                query_json=json.dumps(query, ensure_ascii=False),
                prefer="tenant",
            )
        except Exception as exc:
            return items, f"{type(exc).__name__}: {exc}"
        if not isinstance(response, dict) or not response.get("ok"):
            return items, str((response or {}).get("message") or (response or {}).get("error") or "群列表查询失败")
        items.extend(_chat_items(response))
        raw_data = response.get("data")
        data: dict[str, object] = raw_data if isinstance(raw_data, dict) else {}
        if not (response.get("has_more") or data.get("has_more")):
            break
        page_token = str(response.get("page_token") or data.get("page_token") or "")
        if not page_token:
            break
    return items, ""


async def _resolve_group_with_bot(name: str) -> tuple[str, str]:
    """Resolve one exact group name with the scheduler bot's tenant token.

    先走 ``chats/search``, 搜不到再列机器人所在群: 两个接口的可见范围不同, 只依赖搜索
    会在「群存在但索引没收录」时把纪要卡在收件人解析这一步。
    """
    identity, search_error = await _search_chat_by_name(name)
    if identity:
        return identity, name
    items, list_error = await _list_bot_chats()
    if items:
        listed_id, listed_error = _exact_chat_match(items, name)
        if listed_id:
            return listed_id, name
        return "", f"{search_error}; 机器人所在 {len(items)} 个群里{listed_error}"
    return "", f"{search_error}; 群列表查询失败: {list_error}"


async def _resolve_recipient(recipient: str, user_key: str) -> tuple[str, str, str]:
    """把一个收件人标识解析成 ``(id, 展示名, receive_id_type)``。

    四条路, 顺序固定:

    1. 本身就是 id —— ``ou_``/``oc_``/``on_`` 前缀自证类型, 租户级 ``user_id`` 与邮箱写成
       ``user_id:<值>``/``email:<值>`` → **直接使用, 不做任何姓名解析**;
    2. 命中 ``config/meeting-automation.yaml`` 的 ``runtime.notify.recipients`` → 按表里
       声明的目标解析(``person`` 走通讯录、``chat`` 走群名、或直接给一个 id);
    3. 都不命中 → 按姓名解析一次(未声明的收件人仍可直接写姓名);
    4. 失败 → 报不支持并指路配置, **不猜身份、不向触发者兜底**。

    群收件人请在表里声明成 ``chat``(或直接给 ``chat_id:<oc_…>``): 拿群名当人名查必然失败。
    另注: ``open_id`` 与 ``chat_id`` 都**按应用隔离** —— 从别的应用抄来的这两类 id 到这里
    一定会跨应用报错; 要跨应用稳定就用租户级 ``user_id``(群没有租户级 id, 只能用群名)。
    """
    value = recipient.strip()
    if not value:
        return "", "会议收件人为空", ""
    declared = notify_recipients().get(value)
    if isinstance(declared, dict):
        person = declared.get("person", "")
        chat = declared.get("chat", "")
        if person:
            identity, detail = await _resolve_with_bot(person)
            if identity:
                return identity, person, "open_id"
            return "", f"收件人 {value!r} 按姓名 {person!r} 解析失败({detail}); 想免解析请改配 user_id:<租户 id>", ""
        if chat:
            identity, detail = await _resolve_group_with_bot(chat)
            if identity:
                return identity, chat, "chat_id"
            return "", f"收件人 {value!r} 按群名 {chat!r} 解析失败({detail})", ""
        for kind in _EXPLICIT_ID_TYPES:
            raw = str(declared.get(kind) or "").strip()
            if raw:
                return raw, value, kind
        return "", f"配置的收件人 {value!r} 没给任何目标(person / chat / 某类 id)", ""
    split = _split_typed_id(value)
    if split:
        kind, identity = split
        return identity, value, kind
    identity, detail = await _resolve_with_bot(value)
    if identity:
        return identity, detail or value, "open_id"
    return (
        "",
        (
            f"不支持的会议收件人: {value}({detail})。群收件人请在 config/meeting-automation.yaml 的 "
            "runtime.notify.recipients 里声明 {chat: 群名}; 人名可直接写姓名, 要免姓名解析就写 "
            "user_id:<租户 user_id>(跨应用稳定)。"
        ),
        "",
    )


async def _reply_in_topic(message_id: str, text: str) -> dict[str, object]:
    """Reply to a native topic root without opening another topic."""
    response = await _api.call_api_impl(
        method="POST",
        uri="/open-apis/im/v1/messages/:message_id/reply",
        paths_json=json.dumps({"message_id": message_id}, ensure_ascii=False),
        body_json=json.dumps(
            {
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "msg_type": "text",
                "reply_in_thread": True,
            },
            ensure_ascii=False,
        ),
        prefer="tenant",
    )
    if not isinstance(response, dict) or not response.get("ok"):
        return {
            "ok": False,
            "message": str((response or {}).get("message") or "topic reply failed"),
        }
    raw_data = response.get("data")
    data: dict[str, object] = raw_data if isinstance(raw_data, dict) else {}
    return {
        "ok": True,
        "message_id": str(data.get("message_id") or ""),
        "thread_id": str(data.get("thread_id") or ""),
    }


async def _receipt_lock(key: str) -> anyio.Lock:
    async with _RECEIPT_LOCKS_GUARD:
        lock = _RECEIPT_LOCKS.get(key)
        if lock is None:
            lock = anyio.Lock()
            _RECEIPT_LOCKS[key] = lock
        return lock


async def _read_receipts(path: Path) -> dict[str, object]:
    try:
        value = json.loads(await anyio.Path(str(path)).read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _write_receipts(path: Path, receipts: dict[str, object]) -> None:
    await atomic_write_text(path, json.dumps(receipts, ensure_ascii=False, indent=2))


async def meeting_session_notify(
    meeting_name: str,
    recipient: str,
    text: str,
    record_file_id: str = "",
    user_key: str = "",
    appdata_root: str = "",
) -> str:
    """向固定个人或群聊收件人发送会议结果, 并记录可恢复的幂等回执。"""
    try:
        if not meeting_name.strip() or not recipient.strip() or not text.strip():
            raise ValueError("meeting_name, recipient, and text are required")
        chunks = [text[index : index + MAX_NOTIFICATION_CHARS] for index in range(0, len(text), MAX_NOTIFICATION_CHARS)]
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        artifact.mkdir(parents=True, exist_ok=True)
        identity, display_name, receive_id_type = await _resolve_recipient(recipient, user_key)
        if not identity:
            return json.dumps(
                {"ok": False, "status": "recipient_unresolved", "recipient": recipient, "error": display_name},
                ensure_ascii=False,
            )
        receipt_path = artifact / "notification_receipts.json"
        # Use the resolved identity rather than the caller's alias (whatever key
        # the config table maps to this person).  Different task wording must
        # still converge on one delivery.
        receipt_key = hashlib.sha256(f"{record_file_id}\n{identity}".encode()).hexdigest()
        async with await _receipt_lock(str(receipt_path) + ":" + receipt_key):
            receipts = await _read_receipts(receipt_path)
            previous = receipts.get(receipt_key)
            if isinstance(previous, dict) and previous.get("ok"):
                return json.dumps(
                    {
                        "ok": True,
                        "status": "already_sent",
                        "recipient": display_name,
                        "message_id": previous.get("message_id", ""),
                    },
                    ensure_ascii=False,
                )
            text_hash = hashlib.sha256(text.encode()).hexdigest()
            if isinstance(previous, dict) and previous.get("text_sha256") not in (None, "", text_hash):
                result = {
                    "ok": False,
                    "status": "receipt_content_mismatch",
                    "recipient": display_name,
                    "delivery": "topic" if receive_id_type == "chat_id" else "direct",
                    "recipient_id": identity,
                    "message_ids": previous.get("message_ids", []),
                    "error": "内容已变化, 拒绝在部分发送后混用旧消息和新消息",
                }
                return json.dumps(result, ensure_ascii=False)

            delivery = "topic" if receive_id_type == "chat_id" else "direct"
            previous_ids = previous.get("message_ids", []) if isinstance(previous, dict) else []
            message_ids = [str(value) for value in previous_ids] if isinstance(previous_ids, list) else []
            previous_index = previous.get("next_chunk_index") if isinstance(previous, dict) else None
            try:
                next_chunk_index = int(str(previous_index)) if previous_index is not None else len(message_ids)
            except TypeError, ValueError:
                next_chunk_index = len(message_ids)
            thread_id = str(previous.get("thread_id", "")) if isinstance(previous, dict) else ""
            root_message_id = str(previous.get("root_message_id", "")) if isinstance(previous, dict) else ""

            def progress(status: str, *, error: str = "", ok: bool = False) -> dict[str, object]:
                return {
                    "ok": ok,
                    "status": status,
                    "recipient": display_name,
                    "delivery": delivery,
                    "recipient_id": identity,
                    "message_id": root_message_id or (message_ids[0] if message_ids else ""),
                    "root_message_id": root_message_id,
                    "thread_id": thread_id,
                    "message_ids": message_ids,
                    "next_chunk_index": next_chunk_index,
                    "total_chunks": len(chunks),
                    "text_sha256": text_hash,
                    "error": error,
                }

            if delivery == "topic" and next_chunk_index == 0:
                first_text = chunks[0] if len(chunks) == 1 else f"[1/{len(chunks)}]\n{chunks[0]}"
                try:
                    sent = await _f.start_topic_impl(identity, first_text, None, False)
                except Exception as exc:
                    sent = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
                if not isinstance(sent, dict) or not sent.get("ok"):
                    result = progress("send_failed", error=str((sent or {}).get("message") or "topic start failed"))
                    receipts[receipt_key] = result
                    await _write_receipts(receipt_path, receipts)
                    return json.dumps(result, ensure_ascii=False)
                root_message_id = str(sent.get("message_id") or "")
                thread_id = str(sent.get("thread_id") or "")
                if not root_message_id:
                    result = progress("send_failed", error="topic root did not return a message id")
                    receipts[receipt_key] = result
                    await _write_receipts(receipt_path, receipts)
                    return json.dumps(result, ensure_ascii=False)
                message_ids.append(root_message_id)
                next_chunk_index = 1
                receipts[receipt_key] = progress("sending")
                await _write_receipts(receipt_path, receipts)

            if delivery == "topic" and not root_message_id:
                result = progress("send_failed", error="cannot resume topic without its root message id")
                receipts[receipt_key] = result
                await _write_receipts(receipt_path, receipts)
                return json.dumps(result, ensure_ascii=False)

            while next_chunk_index < len(chunks):
                index = next_chunk_index
                body = chunks[index] if len(chunks) == 1 else f"[{index + 1}/{len(chunks)}]\n{chunks[index]}"
                try:
                    if delivery == "topic":
                        sent = await _reply_in_topic(root_message_id, body)
                    else:
                        # 用解析出来的类型发(open_id / user_id / union_id / email):
                        # 租户级 user_id 没有前缀, 写死 "open_id" 会被飞书判成 230001。
                        sent = await _f.send_message_impl(identity, body, receive_id_type)
                except Exception as exc:
                    sent = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
                if not isinstance(sent, dict) or not sent.get("ok"):
                    result = progress("send_failed", error=str((sent or {}).get("message") or "message send failed"))
                    receipts[receipt_key] = result
                    await _write_receipts(receipt_path, receipts)
                    return json.dumps(result, ensure_ascii=False)
                message_id = str(sent.get("message_id") or "")
                if message_id:
                    message_ids.append(message_id)
                if delivery == "topic" and sent.get("thread_id"):
                    thread_id = str(sent["thread_id"])
                next_chunk_index += 1
                receipts[receipt_key] = progress("sending")
                await _write_receipts(receipt_path, receipts)

            result = progress("sent", ok=True)
            receipts[receipt_key] = result
            await _write_receipts(receipt_path, receipts)
            return json.dumps(result, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_notification_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_session_notify"]
