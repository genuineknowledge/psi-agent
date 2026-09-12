"""List the archived sessions of a whitelisted meeting, with delivery state.

会议存储的「最新文件」是覆盖式的: transcript.md / analysis.md 只保留最近一场。
每次处理都会把该场快照进 ``archive/<record_file_id>/``(永久保留), 本工具把
这些档列成一张人类/模型都能读的清单, 解决"要查历史场次却先得猜 record_file_id"。

只读: 不触发抓取、不写任何文件、不投递。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _meeting_archive import record_archive_dir  # noqa: E402
from _meeting_automation import (  # noqa: E402
    MEETING_JOBS,
    read_meeting_manifest,
)

from psi_agent._appdata import resolve_appdata_root  # noqa: E402

_ARCHIVE_ARTIFACTS = ("transcript.md", "transcript_paragraphs.json", "smart_minutes.json", "analysis.md")


def _job_for_name(meeting_name: str):
    for job in MEETING_JOBS:
        if job.name == meeting_name:
            return job
    return None


def _receipt_summary(archive_dir: Path) -> dict[str, str]:
    """逐收件人的投递状态(键是回执哈希, 值含 recipient/status), 读不到就留空。"""
    path = archive_dir / "notification_receipts.json"
    try:
        receipts = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError, OSError:
        return {}
    if not isinstance(receipts, dict):
        return {}
    summary: dict[str, str] = {}
    for entry in receipts.values():
        if not isinstance(entry, dict):
            continue
        who = str(entry.get("recipient") or entry.get("recipient_id") or "?")
        status = "sent" if entry.get("ok") else str(entry.get("status") or "failed")
        summary[who] = status
    return summary


def _state_status(archive_dir: Path) -> str:
    path = archive_dir / "pipeline_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError, OSError:
        return ""
    if not isinstance(state, dict):
        return ""
    return str(state.get("status") or "")


def _record_entry(archive_dir: Path, record_file_id: str) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    try:
        value = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest = value if isinstance(value, dict) else {}
    except FileNotFoundError, json.JSONDecodeError, OSError:
        manifest = {}
    artifact_sizes: dict[str, int] = {}
    for name in _ARCHIVE_ARTIFACTS:
        path = archive_dir / name
        if path.is_file():
            artifact_sizes[name] = path.stat().st_size
    analysis_text = ""
    analysis_path = archive_dir / "analysis.md"
    if analysis_path.is_file():
        try:
            analysis_text = analysis_path.read_text(encoding="utf-8")
        except OSError:
            analysis_text = ""
    return {
        "record_file_id": record_file_id,
        "archived_at": int(archive_dir.stat().st_mtime),
        "status": _state_status(archive_dir) or str(manifest.get("status") or ""),
        "transcript_chars": int(manifest.get("transcript_chars") or 0),
        "paragraph_count": int(manifest.get("paragraph_count") or 0),
        "chunk_count": int(manifest.get("chunk_count") or 0),
        "chunk_chars": int(manifest.get("chunk_chars") or 0),
        "analysis_chars": len(analysis_text),
        "artifacts": sorted(artifact_sizes),
        "artifact_bytes": artifact_sizes,
        "delivered": _receipt_summary(archive_dir),
    }


async def meeting_records_list(
    meeting_name: str,
    limit: int = 20,
    appdata_root: str = "",
) -> str:
    """列出某场白名单会议的历史场次清单(最新在前)。

    ``meeting_name`` 必须是代码白名单里的任务名(如 ``weekday-alignment`` /
    ``weekday-alignment-1100``); ``limit`` 最多返回多少场(默认 20, 上限 200)。
    每场给出: record_file_id、归档时间、状态、原文字数与段数、是否有分析、
    逐收件人投递状态 —— 拿到 record_file_id 后可用 ``meeting_session_read``
    读转写/分析, 或用 ``meeting_pipeline_replay`` 补跑分析并补发。
    """
    try:
        name = str(meeting_name or "").strip()
        if not name:
            raise ValueError("meeting_name is required")
        job = _job_for_name(name)
        if job is None:
            return json.dumps(
                {
                    "ok": False,
                    "status": "unknown_meeting",
                    "error": f"未在白名单中的会议: {name}",
                    "allowed_meetings": [item.name for item in MEETING_JOBS],
                },
                ensure_ascii=False,
            )
        cap = max(1, min(int(limit or 20), 200))
        base = await resolve_appdata_root(appdata_root)
        manifest = read_meeting_manifest(base, name)
        archive_root = record_archive_dir(base, name, "placeholder").parent
        records: list[dict[str, Any]] = []
        archive_dirs: list[Path] = []
        if archive_root.is_dir():
            archive_dirs = sorted(
                (item for item in archive_root.iterdir() if item.is_dir()),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        for archive_dir in archive_dirs[:cap]:
            records.append(_record_entry(archive_dir, archive_dir.name))
        latest = str(manifest.get("record_file_id") or "")
        return json.dumps(
            {
                "ok": True,
                "status": "ok",
                "meeting_name": name,
                "meeting_code": job.meeting_code,
                "title": job.title,
                "latest_record_file_id": latest,
                "processed_record_file_ids": list(manifest.get("processed_record_file_ids") or []),
                "count": len(records),
                "records": records,
                "hint": (
                    "用 meeting_session_read(record_file_id=…) 读某场产物; "
                    "用 meeting_pipeline_replay(record_file_id=…) 补跑分析并补发。"
                ),
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_records_list_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_records_list"]
