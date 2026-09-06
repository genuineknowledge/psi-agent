"""Publish a Tencent Meeting original transcript as a Feishu group topic."""

# ruff: noqa: E402, RUF001

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f

_start_topic_impl = _f.start_topic_impl


async def tencent_meeting_minutes_publish(
    chat_id: str,
    meeting_title: str = "",
    meeting_date: str = "",
    transcript_text: str = "",
    source_url: str = "",
) -> str:
    """Start a group topic containing the unmodified Tencent Meeting transcript.

    The caller supplies the real topic-enabled group ID. This tool deliberately
    does not look up or guess a group and does not replace the original transcript
    with smart minutes or an agent-generated summary.
    """
    if not chat_id.strip():
        return _f.dumps_result({"ok": False, "status": "chat_id_required"})
    if not transcript_text.strip():
        return _f.dumps_result({"ok": False, "status": "transcript_text_required"})

    heading = "腾讯会议原始全文转写"
    if meeting_title.strip():
        heading += f"｜{meeting_title.strip()}"
    if meeting_date.strip():
        heading += f"｜{meeting_date.strip()}"
    lines = [heading, "", transcript_text.strip()]
    if source_url.strip():
        lines.extend(("", f"原始来源：{source_url.strip()}"))
    try:
        response = await _start_topic_impl(chat_id.strip(), "\n".join(lines), None, False)
    except Exception as exc:
        return _f.dumps_result({"ok": False, "status": "topic_publish_failed", "error": f"{type(exc).__name__}: {exc}"})
    if not isinstance(response, dict) or not response.get("ok"):
        return _f.dumps_result(
            {
                "ok": False,
                "status": "topic_publish_failed",
                "error": str((response or {}).get("message") or "topic publish failed"),
            }
        )
    return _f.dumps_result(
        {
            "ok": True,
            "message_id": str(response.get("message_id") or ""),
            "thread_id": str(response.get("thread_id") or ""),
            "chat_id": str(response.get("chat_id") or chat_id.strip()),
        }
    )


__all__ = ["tencent_meeting_minutes_publish"]
