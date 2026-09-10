"""Per-recording archive of meeting artifacts (每场会议一档, 永久保留).

共享存储的「最新文件」语义是覆盖式: transcript.md / smart_minutes.json /
analysis.md 等只保留最近一次已处理场次。本模块把该场全部产物快照到按
record_file_id 分目录的永久档, 供日后回溯任意一场会议:

    {appdata}/meeting-session/<meeting_name>/archive/<record_file_id>/
        transcript.md / transcript_paragraphs.json / smart_minutes.json
        manifest.json(快照) / analysis.md / analysis.json
        pipeline_state.json / notification_receipts.json

调用点(两处, 均为确定性代码):
  - meeting_transcript_prepare 成功拉取新场次后: 正文三件套 + manifest 立即落档,
    即使之后分析失败也不丢原文;
  - meeting_pipeline_run 每轮收尾: 分析/状态/收据补进同一档。

幂等: 重复调用只按当前文件状态覆盖对应文件, 不产生重复档; 归档失败只返回
错误不抛异常, 不阻断主流程(归档是保底, 不是主线)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anyio
from _meeting_automation import atomic_write_text, meeting_artifact_root

#: 每场快照的文件清单(以共享存储当时的文件为准, 缺哪个就跳过哪个)。
_ARCHIVE_FILE_NAMES = (
    "transcript.md",
    "transcript_paragraphs.json",
    "smart_minutes.json",
    "manifest.json",
    "analysis.md",
    "analysis.json",
    "pipeline_state.json",
    "notification_receipts.json",
)

_SAFE_RECORD_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def record_archive_dir(base: str, meeting_name: str, record_file_id: str) -> Path:
    """该场会议的永久归档目录(调用方自行保证 record_file_id 安全)。"""
    return meeting_artifact_root(base, meeting_name) / "archive" / record_file_id


def _safe_record_dir_name(record_file_id: str) -> str:
    """record_file_id 直接用作目录名, 必须先防路径穿越(只允许普通字符)。"""
    value = str(record_file_id or "").strip()
    if not _SAFE_RECORD_ID.fullmatch(value):
        raise ValueError(f"unsafe record_file_id for archive: {value!r}")
    return value


async def archive_meeting_record(base: str, meeting_name: str, record_file_id: str) -> dict[str, Any]:
    """把共享存储当前该场全部产物快照进 archive/<record_file_id>/。

    Returns ``{"ok": True, "archived": [...], "archive_dir": "..."}`` or
    ``{"ok": False, "error": ...}`` — 归档失败不抛异常(调用方不应因保底
    归档失败而中断主线)。
    """
    try:
        safe_id = _safe_record_dir_name(record_file_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    source = meeting_artifact_root(base, meeting_name)
    archive_dir = record_archive_dir(base, meeting_name, safe_id)
    archived: list[str] = []
    try:
        await anyio.Path(str(archive_dir)).mkdir(parents=True, exist_ok=True)
        for name in _ARCHIVE_FILE_NAMES:
            src = anyio.Path(str(source / name))
            if not await src.is_file():
                continue
            text = await src.read_text(encoding="utf-8")
            await atomic_write_text(str(archive_dir / name), text)
            archived.append(name)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "archived": archived, "archive_dir": str(archive_dir)}


__all__ = ["archive_meeting_record", "record_archive_dir"]
