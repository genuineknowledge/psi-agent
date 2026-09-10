"""Persist meeting analysis and delivery receipts in the shared meeting store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from _meeting_automation import atomic_write_text, meeting_artifact_root

from psi_agent._appdata import resolve_appdata_root


async def meeting_session_write(
    meeting_name: str,
    meeting_code: str = "",
    record_file_id: str = "",
    analysis_text: str = "",
    source_chunks: str = "",
    recipient_receipts_json: str = "{}",
    status: str = "analyzed",
    appdata_root: str = "",
) -> str:
    """保存会议分析、会议标识、原文分块来源和发送回执, 不写入正式正负面总表。"""
    try:
        if not analysis_text.strip():
            raise ValueError("analysis_text is required")
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        artifact.mkdir(parents=True, exist_ok=True)
        try:
            receipts: Any = json.loads(recipient_receipts_json) if recipient_receipts_json.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"recipient_receipts_json is not valid JSON: {exc}") from exc
        if not isinstance(receipts, (dict, list)):
            raise ValueError("recipient_receipts_json must be an object or list")
        await atomic_write_text(artifact / "analysis.md", analysis_text)
        payload = {
            "meeting_name": meeting_name,
            "meeting_code": meeting_code.strip(),
            "record_file_id": record_file_id.strip(),
            "source_chunks": [
                int(value.strip())
                for value in source_chunks.replace("\uff0c", ",").split(",")
                if value.strip().isdigit()
            ],
            "recipient_receipts": receipts,
            "status": status.strip() or "analyzed",
            "updated_at": datetime.now(UTC).isoformat(),
            "formal_ledger_written": False,
        }
        await atomic_write_text(artifact / "analysis.json", json.dumps(payload, ensure_ascii=False, indent=2))
        return json.dumps(
            {"ok": True, "status": payload["status"], "meeting_name": meeting_name, "formal_ledger_written": False},
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_session_write_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_session_write"]
