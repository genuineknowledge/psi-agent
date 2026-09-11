"""Send meeting-session outputs to the fixed business recipients."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import _feishu_api_impl as _api
import _feishu_impl as _f
import anyio
import yaml
from _meeting_automation import atomic_write_text, automation_runtime, meeting_artifact_root

from psi_agent._appdata import resolve_appdata_root

# 单段上限来自 meeting-automation.yaml runtime.notify.chunk_chars (测试可覆写)
MAX_NOTIFICATION_CHARS = int(automation_runtime()["notify"]["chunk_chars"])
_RECEIPT_LOCKS: dict[str, anyio.Lock] = {}
_RECEIPT_LOCKS_GUARD = anyio.Lock()


def _configured_hr_identity() -> str:
    """Reuse the existing onboarding HR identity without adding meeting config."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "rookie_sop.yaml"
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError, yaml.YAMLError:
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("hr_notify_id")
    return str(value).strip() if isinstance(value, str) else ""


async def _resolve_hr_recipient() -> tuple[str, str]:
    """HR 收件人(罗霖): 先按姓名用本应用凭据解析, 解析不到才用配置里的 id。

    ``rookie_sop.yaml`` 的 ``hr_notify_id`` 是一个固定 open_id。一旦它来自别的飞书应用
    (换过应用/换过租户), 发送就会报 ``99992361 open_id cross app`` —— 生产上两场会议的
    overview 收件人因此长期投递失败, 状态停在 ``notifications_pending``。按姓名走通讯录
    拿到的一定是**当前应用**的 open_id, 所以优先用它; 配置值仅作兜底。
    """
    identity, error = await _resolve_with_bot("罗霖")
    if identity:
        return identity, "罗霖"
    configured = _configured_hr_identity()
    if configured:
        return configured, "罗霖"
    return "", f"HR 收件人未配置或无法解析({error})"


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


def _chat_items(response: object) -> list[dict[str, object]]:
    """从 chats 接口回包里取出 items (兼容 data.items 嵌套)。"""
    if not isinstance(response, dict):
        return []
    items = response.get("items")
    if not isinstance(items, list):
        data = response.get("data")
        items = data.get("items") if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _exact_chat_match(items: list[dict[str, object]], name: str) -> tuple[str, str]:
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


async def _list_bot_chats() -> tuple[list[dict[str, object]], str]:
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
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
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


async def _resolve_recipient(recipient: str, user_key: str) -> tuple[str, str]:
    value = recipient.strip()
    if value.startswith(("ou_", "oc_", "user_")):
        return value, value
    if value in {"hr", "罗霖"}:
        return await _resolve_hr_recipient()
    person_aliases = {"cheng": "程秀秀", "程秀秀": "程秀秀", "张浩": "张浩", "王金旺": "王金旺"}
    if value in person_aliases:
        return await _resolve_with_bot(person_aliases[value])
    if value == "HaiTun Agent主战场":
        return await _resolve_group_with_bot(value)
    if value:
        # 其它中文姓名不再直接判"不支持": 先按姓名走一次通讯录(拿当前应用的 open_id),
        # 解析不到才报不支持。配置里写错名字时的报错同时给出两个原因。
        identity, detail = await _resolve_with_bot(value)
        if identity:
            return identity, detail or value
        return "", f"不支持的会议收件人: {value}({detail})"
    return "", "会议收件人为空"


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
        identity, display_name = await _resolve_recipient(recipient, user_key)
        if not identity:
            return json.dumps(
                {"ok": False, "status": "recipient_unresolved", "recipient": recipient, "error": display_name},
                ensure_ascii=False,
            )
        receipt_path = artifact / "notification_receipts.json"
        # Use the resolved identity rather than the caller's alias (``hr`` vs
        # ``罗霖``).  Different task wording must still converge on one delivery.
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
                    "delivery": "topic" if identity.startswith("oc_") else "direct",
                    "recipient_id": identity,
                    "message_ids": previous.get("message_ids", []),
                    "error": "内容已变化, 拒绝在部分发送后混用旧消息和新消息",
                }
                return json.dumps(result, ensure_ascii=False)

            delivery = "topic" if identity.startswith("oc_") else "direct"
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
                        sent = await _f.send_message_impl(identity, body, "open_id")
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
