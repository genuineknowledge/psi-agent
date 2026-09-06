"""Send meeting-session outputs to the fixed business recipients."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import _feishu_api_impl as _api
import _feishu_impl as _f
import anyio
import yaml
from _meeting_automation import meeting_artifact_root

from psi_agent._appdata import resolve_appdata_root

MAX_NOTIFICATION_CHARS = 8_000


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


async def _resolve_group_with_bot(name: str) -> tuple[str, str]:
    """Resolve one exact group name with the scheduler bot's tenant token."""
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
    items = response.get("items")
    if not isinstance(items, list):
        data = response.get("data")
        items = data.get("items") if isinstance(data, dict) else []
    matches: list[tuple[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        display = str(item.get("name") or item.get("chat_name") or "").strip()
        chat_id = str(item.get("chat_id") or item.get("id") or "").strip()
        if display == name and chat_id.startswith("oc_"):
            matches.append((chat_id, display))
    if len(matches) == 1:
        return matches[0]
    return "", f"未找到群名为“{name}”的唯一群聊"


async def _resolve_recipient(recipient: str, user_key: str) -> tuple[str, str]:
    value = recipient.strip()
    if value.startswith(("ou_", "oc_", "user_")):
        return value, value
    if value in {"hr", "罗霖"}:
        identity = _configured_hr_identity()
        return (identity, "罗霖") if identity else ("", "HR 收件人未配置")
    if value in {"cheng", "程秀秀"}:
        return await _resolve_with_bot("程秀秀")
    if value == "HaiTun Agent主战场":
        return await _resolve_group_with_bot(value)
    return "", f"不支持的会议收件人: {value}"


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
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return {
        "ok": True,
        "message_id": str(data.get("message_id") or ""),
        "thread_id": str(data.get("thread_id") or ""),
    }


async def meeting_session_notify(
    meeting_name: str,
    recipient: str,
    text: str,
    record_file_id: str = "",
    user_key: str = "",
    force: bool = False,
    appdata_root: str = "",
) -> str:
    """向固定个人或群聊收件人发送会议结果, 并记录幂等回执。"""
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
        try:
            receipts = json.loads(receipt_path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            receipts = {}
        if not isinstance(receipts, dict):
            receipts = {}
        # Use the resolved identity rather than the caller's alias (``hr`` vs
        # ``罗霖``).  Different task wording must still converge on one delivery.
        receipt_key = hashlib.sha256(f"{record_file_id}\n{identity}".encode()).hexdigest()
        if not force and isinstance(receipts.get(receipt_key), dict) and receipts[receipt_key].get("ok"):
            return json.dumps(
                {
                    "ok": True,
                    "status": "already_sent",
                    "recipient": display_name,
                    "message_id": receipts[receipt_key].get("message_id", ""),
                },
                ensure_ascii=False,
            )
        try:
            message_ids: list[str] = []
            thread_id = ""
            if identity.startswith("oc_"):
                first_text = chunks[0] if len(chunks) == 1 else f"[1/{len(chunks)}]\n{chunks[0]}"
                sent = await _f.start_topic_impl(identity, first_text, None, False)
                delivery = "topic"
                if isinstance(sent, dict) and sent.get("ok"):
                    first_id = str(sent.get("message_id") or "")
                    if first_id:
                        message_ids.append(first_id)
                    thread_id = str(sent.get("thread_id") or "")
                    for index, chunk in enumerate(chunks[1:], start=2):
                        reply = await _reply_in_topic(first_id, f"[{index}/{len(chunks)}]\n{chunk}")
                        if not reply.get("ok"):
                            sent = reply
                            break
                        reply_id = str(reply.get("message_id") or "")
                        if reply_id:
                            message_ids.append(reply_id)
                    else:
                        sent = {**sent, "message_ids": message_ids, "thread_id": thread_id}
            else:
                delivery = "direct"
                sent = {"ok": True, "message_ids": []}
                for index, chunk in enumerate(chunks, start=1):
                    body = chunk if len(chunks) == 1 else f"[{index}/{len(chunks)}]\n{chunk}"
                    part = await _f.send_message_impl(identity, body, "open_id")
                    if not isinstance(part, dict) or not part.get("ok"):
                        sent = part if isinstance(part, dict) else {"ok": False, "message": "direct send failed"}
                        break
                    part_id = str(part.get("message_id") or "")
                    if part_id:
                        sent["message_ids"].append(part_id)
                else:
                    sent["message_id"] = sent["message_ids"][0] if sent["message_ids"] else ""
        except Exception as exc:
            sent = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
            delivery = "topic" if identity.startswith("oc_") else "direct"
        result = {
            "ok": bool(isinstance(sent, dict) and sent.get("ok")),
            "status": "sent" if isinstance(sent, dict) and sent.get("ok") else "send_failed",
            "recipient": display_name,
            "delivery": delivery,
            "recipient_id": identity,
            "message_id": str(sent.get("message_id") or "") if isinstance(sent, dict) else "",
            "thread_id": str(sent.get("thread_id") or "") if isinstance(sent, dict) else "",
            "message_ids": sent.get("message_ids", []) if isinstance(sent, dict) else [],
            "error": str(sent.get("message") or "") if isinstance(sent, dict) and not sent.get("ok") else "",
        }
        receipts[receipt_key] = result
        await anyio.Path(str(receipt_path)).write_text(
            json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return json.dumps(result, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_notification_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_session_notify"]
