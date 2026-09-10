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
from _meeting_archive import archive_meeting_record
from _meeting_automation import (
    _json_payload,
    atomic_write_text,
    automation_runtime,
    chunk_text,
    extract_latest_transcript_record,
    extract_paragraph_ids,
    extract_paragraph_items,
    meeting_artifact_root,
    meeting_credential_env,
    path_lock,
    read_meeting_manifest,
    render_transcript_paragraphs,
)
from tencent_meeting import _tencent_meeting_call_with_token_env, tencent_meeting_call

from psi_agent._appdata import resolve_appdata_root

#: 单次 API 调用的有限重试 (指数退避)。只用于自愈瞬时失败: 网络错误、上游挂起
#: (超时)、5xx/429; 业务错误 (参数/权限/会议不存在) 同样会重试到次数上限后
#: 以明确异常失败 —— 宁可显式失败, 不再被静默当成"没有录制/没有转写"。
#: 参数来自 meeting-automation.yaml runtime.tencent (测试可覆写模块同名常量)。
_TENCENT_RUNTIME = automation_runtime()["tencent"]
_CALL_ATTEMPTS = int(_TENCENT_RUNTIME["retry_attempts"])
_CALL_BACKOFF_SECONDS = tuple(float(v) for v in _TENCENT_RUNTIME["retry_backoff_seconds"])


def _tool_params(name: str, arguments: dict[str, Any]) -> str:
    payload = {
        "name": name,
        "arguments": {
            **arguments,
            "_client_info": {"os": "linux", "agent": "haitun", "model": "meeting-session"},
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _transport_status(payload: Any) -> int | None:
    """信封 ``_transport.status_code`` (由 ``_json_payload`` 从 HTTP 信封带出)。"""
    if isinstance(payload, dict):
        transport = payload.get("_transport")
        if isinstance(transport, dict):
            code = transport.get("status_code")
            if isinstance(code, int):
                return code
            if isinstance(code, str) and code.isdigit():
                return int(code)
    return None


async def _call_once(name: str, arguments: dict[str, Any], *, token_env: str) -> Any:
    params = _tool_params(name, arguments)
    if token_env == "TENCENT_MEETING_TOKEN":
        # Keep the normal path patchable for local tests and existing callers.
        raw = await tencent_meeting_call("tools/call", params)
    else:
        raw = await _tencent_meeting_call_with_token_env("tools/call", params, token_env=token_env)
    if str(raw).lstrip().startswith(("[错误]", "Error:")):
        raise RuntimeError(str(raw).strip())
    payload = _json_payload(raw)
    # JSON-RPC 协议校验: 顶层 error / result 缺失 → 显式失败, 不静默吞成业务结果。
    if isinstance(payload, dict) and payload.get("error") is not None and "result" not in payload:
        raise RuntimeError(f"Tencent Meeting RPC error: {json.dumps(payload['error'], ensure_ascii=False)[:240]}")
    return payload


async def _call(name: str, arguments: dict[str, Any], *, token_env: str = "TENCENT_MEETING_TOKEN") -> Any:
    last_error: Exception | None = None
    for attempt in range(1, _CALL_ATTEMPTS + 1):
        try:
            payload = await _call_once(name, arguments, token_env=token_env)
        except Exception as exc:  # 统一按"本次失败"处理并决定是否重试
            last_error = exc
        else:
            status = _transport_status(payload)
            if status is not None and (status >= 500 or status == 429):
                last_error = RuntimeError(f"Tencent Meeting HTTP {status}")
            else:
                return payload
        if attempt < _CALL_ATTEMPTS:
            await anyio.sleep(_CALL_BACKOFF_SECONDS[min(attempt - 1, len(_CALL_BACKOFF_SECONDS) - 1)])
    if last_error is not None:
        raise last_error
    raise RuntimeError("Tencent Meeting call failed without an error")


def _merge_paragraphs(target: list[dict[str, Any]], payload: Any) -> None:
    seen = {str(item.get("pid") or item.get("paragraph_id") or item.get("id") or "") for item in target}
    for item in extract_paragraph_items(payload):
        pid = str(item.get("pid") or item.get("paragraph_id") or item.get("id") or "")
        if pid and pid not in seen:
            target.append(item)
            seen.add(pid)


def _next_detail_cursor(payload: Any) -> tuple[str, str] | None:
    """Return the next detail cursor together with the parameter it belongs to."""
    if not isinstance(payload, dict) or not payload.get("has_more"):
        return None
    next_pid = payload.get("next_pid")
    if next_pid:
        return "pid", str(next_pid)
    next_token = payload.get("next_page_token") or payload.get("next_token")
    if next_token:
        return "page_token", str(next_token)
    return None


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
            cursor_arg, cursor = "pid", pid
            visited_detail_cursors: set[tuple[str, str]] = set()
            while cursor and (cursor_arg, cursor) not in visited_detail_cursors:
                visited_detail_cursors.add((cursor_arg, cursor))
                detail = await _call(
                    "get_transcripts_details",
                    {"record_file_id": record_file_id, cursor_arg: cursor, "limit": 100},
                    token_env=token_env,
                )
                _merge_paragraphs(paragraphs, detail)
                next_cursor = _next_detail_cursor(detail)
                if next_cursor is None:
                    break
                cursor_arg, cursor = next_cursor
        return paragraphs

    # Some recordings do not expose the paragraph index.  Fall back to the
    # details cursor and continue until the server says there is no next page.
    cursor_arg, cursor = "pid", "0"
    visited: set[tuple[str, str]] = set()
    for _ in range(10_000):
        if (cursor_arg, cursor) in visited:
            break
        visited.add((cursor_arg, cursor))
        detail = await _call(
            "get_transcripts_details",
            {"record_file_id": record_file_id, cursor_arg: cursor, "limit": 100},
            token_env=token_env,
        )
        _merge_paragraphs(paragraphs, detail)
        next_cursor = _next_detail_cursor(detail)
        if next_cursor is None:
            break
        cursor_arg, cursor = next_cursor
    return paragraphs


async def _write(path: Path, content: str) -> None:
    await atomic_write_text(path, content)


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
        manifest_path = artifact / "manifest.json"
        async with await path_lock(manifest_path):
            # 提交阶段在锁内重读 manifest 并取并集: 定时触发与手动重跑并发时,
            # 彼此的 processed_record_file_ids 不互相覆盖 (防丢已处理标记)。
            committed = read_meeting_manifest(base, meeting_name)
            committed_ids = {str(value) for value in committed.get("processed_record_file_ids", []) if value}
            if committed.get("record_file_id"):
                committed_ids.add(str(committed["record_file_id"]))
            committed_ids.add(record_file_id)
            next_manifest = {
                "meeting_name": meeting_name,
                "meeting_code": meeting_code.strip(),
                "record_file_id": record_file_id,
                "processed_record_file_ids": sorted(committed_ids),
                "transcript_chars": len(transcript),
                "paragraph_count": len(paragraphs),
                "chunk_count": len(chunks),
                "chunk_chars": 8_000,
                "status": "ready",
            }
            await _write(manifest_path, json.dumps(next_manifest, ensure_ascii=False, indent=2))
        # 新场次正文落位后立即入永久档(archive/<record_file_id>/): 即使后续分析
        # 失败或换场覆盖, 本场原文/纪要/manifest 也已保留。归档失败不阻断主线。
        await archive_meeting_record(base, meeting_name, record_file_id)
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
