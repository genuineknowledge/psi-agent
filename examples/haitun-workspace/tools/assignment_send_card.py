from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from feishu_message import feishu_message_send_card


async def assignment_send_card(
    receive_id: str,
    assignment_id: str,
    title: str,
    assigner_name: str,
    summary: str = "",
    receive_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Send a deterministic Feishu card entry for a Fusion Memory work assignment."""
    normalized_receive_id = _required_text(receive_id, "receive_id")
    normalized_assignment_id = _required_text(assignment_id, "assignment_id")
    normalized_title = _required_text(title, "title")
    normalized_assigner_name = _required_text(assigner_name, "assigner_name")
    normalized_summary = _optional_text(summary)
    if normalized_receive_id is None:
        return _invalid_argument("receive_id must be a non-empty string")
    if normalized_assignment_id is None:
        return _invalid_argument("assignment_id must be a non-empty string")
    if normalized_title is None:
        return _invalid_argument("title must be a non-empty string")
    if normalized_assigner_name is None:
        return _invalid_argument("assigner_name must be a non-empty string")

    card = _build_assignment_card(
        assignment_id=normalized_assignment_id,
        title=normalized_title,
        assigner_name=normalized_assigner_name,
        summary=normalized_summary,
    )
    business_context = {
        "type": "work_assignment",
        "assignment_id": normalized_assignment_id,
        "title": normalized_title,
        "assigner_name": normalized_assigner_name,
    }
    action_handlers = {
        "view_assignment_detail": "assignment_get",
        "confirm_assignment_receipt": "assignment_transition",
    }
    return await feishu_message_send_card(
        normalized_receive_id,
        json.dumps(card, ensure_ascii=False),
        receive_id_type,
        user_key,
        json.dumps(business_context, ensure_ascii=False),
        json.dumps(action_handlers, ensure_ascii=False),
    )


def _build_assignment_card(
    *,
    assignment_id: str,
    title: str,
    assigner_name: str,
    summary: str | None,
) -> dict[str, Any]:
    lines = [
        f"**任务:** {title}",
        f"**安排者:** {assigner_name}",
    ]
    if summary is not None:
        lines.append(f"**摘要:** {summary}")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "新的工作安排"},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": "\n".join(lines)},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看详情"},
                        "type": "default",
                        "value": {"action": "view_assignment_detail", "assignment_id": assignment_id},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认接收"},
                        "type": "primary",
                        "value": {"action": "confirm_assignment_receipt", "assignment_id": assignment_id},
                    },
                ],
            },
        ],
    }


def _required_text(value: str, field_name: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _optional_text(value: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _invalid_argument(message: str) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": "invalid_argument", "message": message, "retryable": False}},
        ensure_ascii=False,
    )
