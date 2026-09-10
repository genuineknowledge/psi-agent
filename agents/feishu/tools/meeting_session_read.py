"""Read shared meeting-session artifacts in bounded chunks."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from _meeting_archive import _safe_record_dir_name
from _meeting_automation import TRANSCRIPT_CHUNK_CHARS, chunk_text, meeting_artifact_root

from psi_agent._appdata import resolve_appdata_root


def _artifact_path(root: Path, artifact: str) -> Path:
    names = {
        "transcript": "transcript.md",
        "analysis": "analysis.md",
        "smart_minutes": "smart_minutes.json",
        "manifest": "manifest.json",
    }
    if artifact not in names:
        raise ValueError(f"unsupported artifact: {artifact}; use transcript, analysis, smart_minutes, or manifest")
    return root / names[artifact]


async def meeting_session_read(
    meeting_name: str,
    artifact: str = "transcript",
    chunk_index: int = 0,
    record_file_id: str = "",
    appdata_root: str = "",
) -> str:
    """读取会议专用 Session 中的原始转写或分析结果, 按块返回以避免截断。

    默认读「最新一场」(共享存储根); 传 ``record_file_id`` 时改读该场次的历史
    永久档 ``{root}/meeting-session/<meeting_name>/archive/<record_file_id>/``
    (每场转写/纪要/manifest/分析产物按场次一档, 由归档机制持续写入)。
    """
    try:
        if chunk_index < 0:
            raise ValueError("chunk_index must be non-negative")
        base = await resolve_appdata_root(appdata_root)
        root = meeting_artifact_root(base, meeting_name)
        if record_file_id.strip():
            try:
                root = root / "archive" / _safe_record_dir_name(record_file_id)
            except ValueError as exc:
                return json.dumps(
                    {"ok": False, "status": "invalid_record_file_id", "error": str(exc)}, ensure_ascii=False
                )
        path = _artifact_path(root, artifact)
        if not path.is_file():
            return json.dumps(
                {"ok": False, "status": "artifact_not_found", "meeting_name": meeting_name, "artifact": artifact},
                ensure_ascii=False,
            )
        content = path.read_text(encoding="utf-8")
        if artifact in {"smart_minutes", "manifest"}:
            # Keep JSON readable while still respecting the tool-result bound.
            with suppress(json.JSONDecodeError):
                content = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
        chunks = chunk_text(content, TRANSCRIPT_CHUNK_CHARS)
        if chunk_index >= len(chunks):
            return json.dumps(
                {"ok": False, "status": "chunk_not_found", "chunk_count": len(chunks)}, ensure_ascii=False
            )
        return json.dumps(
            {
                "ok": True,
                "status": "ok",
                "meeting_name": meeting_name,
                "artifact": artifact,
                "chunk_index": chunk_index,
                "chunk_count": len(chunks),
                "has_more": chunk_index < len(chunks) - 1,
                "next_chunk_index": chunk_index + 1 if chunk_index < len(chunks) - 1 else None,
                "content": chunks[chunk_index],
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_session_read_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_session_read"]
