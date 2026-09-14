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


def _search_artifact(content: str, needle: str, context: int, limit: int) -> dict[str, object]:
    """在整份产物里做字面检索, 返回行号/行文本/出现次数。"""
    lines = content.splitlines()
    hits = [index for index, line in enumerate(lines) if needle in line]
    shown = hits[:limit]
    matches: list[dict[str, object]] = []
    for index in shown:
        item: dict[str, object] = {"line": index + 1, "text": lines[index][:400]}
        if context:
            item["before"] = [line[:400] for line in lines[max(0, index - context) : index]]
            item["after"] = [line[:400] for line in lines[index + 1 : index + 1 + context]]
        matches.append(item)
    return {
        "match_count": len(hits),
        "occurrence_count": content.count(needle),
        "line_count": len(lines),
        "truncated": len(hits) > len(shown),
        "matches": matches,
    }


async def meeting_session_read(
    meeting_name: str,
    artifact: str = "transcript",
    chunk_index: int = 0,
    record_file_id: str = "",
    appdata_root: str = "",
    search: str = "",
    context: int = 0,
    limit: int = 20,
) -> str:
    """读取会议专用 Session 中的原始转写或分析结果, 按块返回以避免截断。

    默认读「最新一场」(共享存储根); 传 ``record_file_id`` 时改读该场次的历史
    永久档 ``{root}/meeting-session/<meeting_name>/archive/<record_file_id>/``
    (每场转写/纪要/manifest/分析产物按场次一档, 由归档机制持续写入)。

    传 ``search`` 时切换为**全文逐行检索**: 在整份产物 (不分块) 里查找字面子串,
    返回命中行号、行文本与命中总次数 ``occurrence_count``; ``context`` 附带命中行
    前后各 N 行。要「逐字核对某段原文是否确实出现在转写里」「数一共出现几次」时
    用这个参数 —— 分块读取只看得到一块, 而这类核对要的恰是全局, 所以**不要**另开
    shell 去 grep。
    """
    try:
        if chunk_index < 0:
            raise ValueError("chunk_index must be non-negative")
        if not isinstance(search, str):
            raise ValueError("search must be a string")
        if search and (not isinstance(context, int) or isinstance(context, bool) or not 0 <= context <= 20):
            raise ValueError("context must be an integer from 0 to 20")
        if search and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100):
            raise ValueError("limit must be an integer from 1 to 100")
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
        if search:
            found = _search_artifact(content, search, context, limit)
            return json.dumps(
                {
                    "ok": True,
                    "status": "ok",
                    "meeting_name": meeting_name,
                    "artifact": artifact,
                    "search": search,
                    **found,
                },
                ensure_ascii=False,
            )
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
