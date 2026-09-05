"""Map Feishu ``drive.file.edit_v1`` → ``haitun.rookie.doc_edited``."""

from __future__ import annotations

from typing import Any


def map_event(raw: dict[str, Any]) -> list[dict[str, Any]]:
    header_raw = raw.get("header")
    header: dict[str, Any] = header_raw if isinstance(header_raw, dict) else {}
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    if not isinstance(event, dict):
        return []

    # 文档 token 在不同 payload 形态下键名不一 —— 都试一遍, 认不出就不产出信封,
    # 免得下游拿着空 token 去读文档。
    token = ""
    for key in ("file_token", "token", "document_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            token = value.strip()
            break
    if not token:
        return []

    file_type = str(event.get("file_type") or "")
    # 只关心文档 —— 同一个应用可能还订阅了表格/其他文件的变更
    if file_type and file_type not in {"docx", "doc"}:
        return []

    operator = event.get("operator_id")
    operator_open_id = ""
    if isinstance(operator, dict):
        operator_open_id = str(operator.get("open_id") or "").strip()

    event_id = str(header.get("event_id") or "").strip()
    payload: dict[str, Any] = {"document_id": token, "file_token": token}
    if operator_open_id:
        payload["operator_open_id"] = operator_open_id
    if file_type:
        payload["file_type"] = file_type
    if event_id:
        payload["platform_event_id"] = event_id

    # 幂等键带 event_id: 飞书 at-least-once 投递, 同一次编辑可能推多遍。
    idem = f"feishu:rookie_doc_edited:{event_id}" if event_id else f"feishu:rookie_doc_edited:{token}"
    return [
        {
            "schema_version": 1,
            "source": "feishu",
            "event": "haitun.rookie.doc_edited",
            "payload": payload,
            "raw_event": "drive.file.edit_v1",
            "raw_payload": {"file_token": token, "file_type": file_type},
            "idempotency_key": idem,
            "routing": {"open_id": operator_open_id} if operator_open_id else {},
        }
    ]
