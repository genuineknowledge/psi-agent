"""Fetch and persist one completed Tencent Meeting raw transcript.

The tool deliberately returns only a small manifest.  The transcript itself is
stored in the shared meeting-session AppData directory and is read through
``meeting_session_read`` in bounded chunks by the scheduler Session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from _meeting_automation import (
    _json_payload,
    chunk_text,
    extract_latest_transcript_record,
    extract_paragraph_ids,
    extract_paragraph_items,
    meeting_artifact_root,
    meeting_credential_env,
    read_meeting_manifest,
    render_transcript_paragraphs,
)
from tencent_meeting import _tencent_meeting_call_with_token_env, tencent_meeting_call

from psi_agent._appdata import resolve_appdata_root


def _tool_params(name: str, arguments: dict[str, Any]) -> str:
    payload = {
        "name": name,
        "arguments": {
            **arguments,
            "_client_info": {"os": "linux", "agent": "haitun", "model": "meeting-session"},
        },
    }
    return json.dumps(payload, ensure_ascii=False)


async def _call(name: str, arguments: dict[str, Any], *, token_env: str = "TENCENT_MEETING_TOKEN") -> Any:
    params = _tool_params(name, arguments)
    if token_env == "TENCENT_MEETING_TOKEN":
        # Keep the normal path patchable for local tests and existing callers.
        raw = await tencent_meeting_call("tools/call", params)
    else:
        raw = await _tencent_meeting_call_with_token_env("tools/call", params, token_env=token_env)
    if str(raw).lstrip().startswith(("[错误]", "Error:")):
        raise RuntimeError(str(raw).strip())
    return _json_payload(raw)


def _merge_paragraphs(target: list[dict[str, Any]], payload: Any) -> None:
    seen = {str(item.get("pid") or item.get("paragraph_id") or item.get("id") or "") for item in target}
    for item in extract_paragraph_items(payload):
        pid = str(item.get("pid") or item.get("paragraph_id") or item.get("id") or "")
        if pid and pid not in seen:
            target.append(item)
            seen.add(pid)


async def _collect_paragraphs(record_file_id: str, *, token_env: str) -> list[dict[str, Any]]:
    paragraph_ids: list[str] = []
    paragraph_payload = await _call(
        "get_transcripts_paragraphs", {"record_file_id": record_file_id}, token_env=token_env
    )
    paragraph_ids.extend(extract_paragraph_ids(paragraph_payload))
    # The MCP provider may page the paragraph index even though the normal
    # response contains a list of ids. Follow either cursor spelling without
    # assuming one fixed envelope shape.
    visited_index_cursors: set[str] = set()
    while isinstance(paragraph_payload, dict) and paragraph_payload.get("has_more"):
        next_pid = paragraph_payload.get("next_pid")
        next_token = paragraph_payload.get("next_page_token") or paragraph_payload.get("next_token")
        cursor = next_pid or next_token
        if not cursor or str(cursor) in visited_index_cursors:
            break
        cursor_text = str(cursor)
        visited_index_cursors.add(cursor_text)
        cursor_arg = "pid" if next_pid else "page_token"
        paragraph_payload = await _call(
            "get_transcripts_paragraphs",
            {"record_file_id": record_file_id, cursor_arg: cursor_text},
            token_env=token_env,
        )
        paragraph_ids.extend(extract_paragraph_ids(paragraph_payload))
    paragraphs: list[dict[str, Any]] = []
    if paragraph_ids:
        for pid in paragraph_ids:
            cursor = pid
            visited_detail_cursors: set[str] = set()
            while cursor and cursor not in visited_detail_cursors:
                visited_detail_cursors.add(cursor)
                detail = await _call(
                    "get_transcripts_details",
                    {"record_file_id": record_file_id, "pid": cursor, "limit": 100},
                    token_env=token_env,
                )
                _merge_paragraphs(paragraphs, detail)
                if not isinstance(detail, dict) or not detail.get("has_more"):
                    break
                next_cursor = detail.get("next_pid") or detail.get("next_page_token") or detail.get("next_token")
                cursor = str(next_cursor) if next_cursor else ""
        return paragraphs

    # Some recordings do not expose the paragraph index.  Fall back to the
    # details cursor and continue until the server says there is no next page.
    pid = "0"
    visited: set[str] = set()
    for _ in range(10_000):
        if pid in visited:
            break
        visited.add(pid)
        detail = await _call(
            "get_transcripts_details",
            {"record_file_id": record_file_id, "pid": pid, "limit": 100},
            token_env=token_env,
        )
        _merge_paragraphs(paragraphs, detail)
        if not isinstance(detail, dict) or not detail.get("has_more"):
            break
        next_pid = detail.get("next_pid") or detail.get("next_page_token")
        if not next_pid:
            break
        pid = str(next_pid)
    return paragraphs


async def _write(path: Path, content: str) -> None:
    await anyio.Path(str(path)).write_text(content, encoding="utf-8")


async def meeting_transcript_prepare(
    meeting_code: str,
    meeting_name: str,
    appdata_root: str = "",
) -> str:
    """获取指定会议最新的完整原始文字转写并保存到独立会议存储。"""
    try:
        if not meeting_code.strip() or not meeting_name.strip():
            raise ValueError("meeting_code and meeting_name are required")
        token_env = meeting_credential_env(meeting_name, meeting_code)
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        artifact.mkdir(parents=True, exist_ok=True)
        manifest = read_meeting_manifest(base, meeting_name)
        processed_ids = {str(value) for value in manifest.get("processed_record_file_ids", []) if value}
        if manifest.get("record_file_id"):
            processed_ids.add(str(manifest["record_file_id"]))

        records_payload = await _call("get_records_list", {"meeting_code": meeting_code.strip()}, token_env=token_env)
        record = extract_latest_transcript_record(records_payload, processed_ids)
        if record is None:
            # Distinguish an already consumed latest recording from a recording
            # which is still being transcoded, without returning provider data.
            all_record = extract_latest_transcript_record(records_payload, set())
            status = (
                "already_processed"
                if all_record and str(all_record.get("record_file_id")) in processed_ids
                else "no_completed_transcript"
            )
            return json.dumps({"ok": True, "status": status, "meeting_name": meeting_name}, ensure_ascii=False)

        record_file_id = str(record["record_file_id"])
        paragraphs = await _collect_paragraphs(record_file_id, token_env=token_env)
        transcript = render_transcript_paragraphs(paragraphs)
        if not transcript.strip():
            raise RuntimeError("Tencent Meeting returned an empty transcript")
        smart_minutes: Any = {}
        try:
            smart_minutes = await _call("get_smart_minutes", {"record_file_id": record_file_id}, token_env=token_env)
        except Exception as exc:
            smart_minutes = {"error": f"{type(exc).__name__}: {exc}"}

        chunks = chunk_text(transcript)
        await _write(artifact / "transcript.md", transcript)
        await _write(artifact / "smart_minutes.json", json.dumps(smart_minutes, ensure_ascii=False, indent=2))
        await _write(artifact / "transcript_paragraphs.json", json.dumps(paragraphs, ensure_ascii=False))
        processed_ids.add(record_file_id)
        next_manifest = {
            "meeting_name": meeting_name,
            "meeting_code": meeting_code.strip(),
            "record_file_id": record_file_id,
            "processed_record_file_ids": sorted(processed_ids),
            "transcript_chars": len(transcript),
            "paragraph_count": len(paragraphs),
            "chunk_count": len(chunks),
            "chunk_chars": 8_000,
            "status": "ready",
        }
        await _write(artifact / "manifest.json", json.dumps(next_manifest, ensure_ascii=False, indent=2))
        return json.dumps(
            {
                "ok": True,
                "status": "ready",
                "meeting_name": meeting_name,
                "meeting_code": meeting_code.strip(),
                "record_file_id": record_file_id,
                "transcript_chars": len(transcript),
                "paragraph_count": len(paragraphs),
                "chunk_count": len(chunks),
                "chunk_indexes": list(range(len(chunks))),
                "smart_minutes_saved": bool(smart_minutes),
            },
            ensure_ascii=False,
        )
    except (TypeError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        return json.dumps(
            {"ok": False, "status": "transcript_prepare_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_transcript_prepare"]
