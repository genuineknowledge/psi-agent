"""Export one meeting record as a self-contained, shareable package (read-only).

Why this exists: reading a transcript is already possible (``meeting_session_read``
over the pipeline artifacts), but turning one record into a **deliverable package**
— transcript + paragraphs + smart minutes + analysis + the Tencent cloud-recording
metadata/link, with a human-readable 场次信息 sheet — had no tool, so the agent
wrote its own fetch script (production, 2026-09-10). That script duplicated paging,
copying and naming logic and left no audit trail.

This tool is deliberately read-only with respect to the pipeline:

- it reads the shared store (``{appdata}/meeting-session/<meeting>/``) or, when a
  ``record_file_id`` is given, the permanent per-record archive
  (``.../archive/<record_file_id>/``);
- it writes only into its own export directory (default
  ``{appdata}/meeting-exports/<date>-<title>-<code>/``) via atomic writes, and
  refuses an ``output_dir`` that points inside the pipeline artifacts;
- ``notification_receipts.json`` / ``pipeline_state.json`` are intentionally NOT
  exported: they carry internal identities and run state, not meeting content.
"""

# ruff: noqa: E402, RUF001, RUF002

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _meeting_archive import _safe_record_dir_name
from _meeting_automation import (
    MEETING_JOBS,
    _record_candidates,
    atomic_write_text,
    meeting_artifact_root,
    meeting_credential_env,
    read_meeting_manifest,
)
from meeting_transcript_prepare import _call as _tencent_call

from psi_agent._appdata import resolve_appdata_root

#: 打包内容 = 会议内容本身; 缺哪个跳过哪个。
_EXPORT_FILES = (
    "transcript.md",
    "transcript_paragraphs.json",
    "smart_minutes.json",
    "manifest.json",
    "analysis.md",
    "analysis.json",
)

#: 腾讯 records_list 里值得进「场次信息」的展示字段。
_META_KEYS = (
    "meeting_record_id",
    "record_file_id",
    "record_name",
    "record_type",
    "state",
    "state_int",
    "meeting_start_time",
    "meeting_end_time",
    "record_start_time",
    "record_end_time",
    "record_size",
    "view_address",
    "download_address",
)


def _job_by_name(meeting_name: str) -> Any | None:
    for job in MEETING_JOBS:
        if job.name == meeting_name:
            return job
    return None


def _unwrap_payload(payload: Any) -> Any:
    """逐层拆 JSON-RPC / MCP content 信封, 直到拿到工具结果对象。

    Tencent skill 会把结果套在 ``{"jsonrpc":…,"result":…}`` 里, 工具结果又可能再套一层
    ``content:[{type:text,text:"<json>"}]``; 直接对原始串做 ``_record_candidates`` 会
    一个 record 都找不到 (实测)。
    """
    current = payload
    for _ in range(4):
        if isinstance(current, dict) and "result" in current and isinstance(current["result"], (dict, list)):
            current = current["result"]
            continue
        break
    if isinstance(current, dict) and isinstance(current.get("content"), list):
        texts = [
            item["text"].strip()
            for item in current["content"]
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()
        ]
        for text in texts:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
        if texts:
            return {"content_text": "\n".join(texts)}
    return current


def _pick_record(payload: Any, record_file_id: str) -> tuple[dict[str, Any], bool]:
    """挑出与本次导出对应的录制场次。

    转写 record_file_id 与**云录制文件** record_file_id 不是同一个值(实测 9/9 场:
    转写 2097519504906416129 / 录制 2097519494798155777), 所以先按 id 精确匹配,
    匹配不到时按"最新场次优先"取一个, 并由调用方在信息页如实标注这是近似匹配。
    """
    records = _record_candidates(payload)
    if not records:
        return {}, False
    if record_file_id:
        for record in records:
            ids = {str(record.get(key) or "").strip() for key in ("record_file_id", "file_id", "meeting_record_id")}
            if record_file_id in ids:
                return record, True
    newest = max(records, key=lambda item: str(item.get("record_start_time") or item.get("meeting_start_time") or ""))
    return newest, False


def _record_meta(record: dict[str, Any]) -> dict[str, Any]:
    """把一条录制记录压成展示字段(空值剔除)。"""
    return {key: record[key] for key in _META_KEYS if record.get(key) not in (None, "", [], {})}


def _find_address(value: Any) -> str:
    """从任意嵌套结果里找第一个 http(s) 地址(地址字段名各家不一, 只认值)。"""
    if isinstance(value, str):
        return value if value.startswith("http") else ""
    if isinstance(value, dict):
        for child in value.values():
            found = _find_address(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_address(child)
            if found:
                return found
    return ""


async def _fetch_record_meta(job: Any, record_file_id: str, token_env: str) -> tuple[dict[str, Any], bool, str]:
    """取该场次的云录制定位信息; 返回 (meta, 是否精确匹配, 错误说明)。"""
    try:
        payload = _unwrap_payload(
            await _tencent_call("get_records_list", {"meeting_code": job.meeting_code}, token_env=token_env)
        )
    except Exception as exc:
        return {}, False, f"{type(exc).__name__}: {exc}"
    record, exact = _pick_record(payload, record_file_id)
    if not record:
        return {}, False, "腾讯返回里没有该会议号的录制记录"
    meta = _record_meta(record)
    if exact and not (meta.get("view_address") or meta.get("download_address")):
        # 列表不带地址时, 按云录制文件 id 单独取一次下载/观看地址(短时效, 每次现取)。
        recording_id = str(meta.get("record_file_id") or "").strip()
        if recording_id:
            try:
                address_payload = _unwrap_payload(
                    await _tencent_call("get_record_addresses", {"record_file_id": recording_id}, token_env=token_env)
                )
                address = _find_address(address_payload)
                if address:
                    meta["view_address"] = address
            except Exception as exc:
                meta["address_error"] = f"{type(exc).__name__}: {exc}"
    return meta, exact, ""


def _date_part(value: Any, fallback: str) -> str:
    """从 ``2026-09-09 10:56:28`` / epoch 毫秒里取出日期; 取不到用兜底。"""
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        millis = int(float(text))
    except TypeError, ValueError:
        return fallback
    if millis <= 0:
        return fallback
    return datetime.fromtimestamp(millis / 1000).astimezone().date().isoformat()


def _render_info_md(
    *,
    job: Any,
    record_file_id: str,
    files: list[tuple[str, int]],
    meta: dict[str, Any],
    meta_error: str,
    source_label: str,
    token_env: str,
    match_note: str = "",
) -> str:
    lines = [
        f"# {job.title}（{job.meeting_code}）会议资料包",
        "",
        "## 场次",
        f"- 会议号：{job.meeting_code}（配置名 {job.name}，标题「{job.title}」）",
        f"- 转写 record_file_id：{record_file_id}",
        f"- 会议开始：{meta.get('meeting_start_time') or '未获取'}",
        f"- 录制区间：{meta.get('record_start_time') or '未获取'} → {meta.get('record_end_time') or '未获取'}",
        "",
        "## 内容（来自会议管道落盘）",
    ]
    lines.extend(f"- {name}（{size} bytes）" for name, size in files)
    lines += [
        "- 未包含：notification_receipts.json / pipeline_state.json（含内部身份与运行状态，默认不导出）",
        "",
        "## 腾讯云录制",
    ]
    if meta_error:
        lines.append(f"- 录制元信息未取到：{meta_error}")
    else:
        lines += [
            f"- 云录制 meeting_record_id：{meta.get('meeting_record_id') or '未获取'}",
            f"- 云录制文件 record_file_id：{meta.get('record_file_id') or '未获取'}",
            f"- 录制状态：{meta.get('state') or meta.get('state_int') or '未获取'}",
            f"- 在线观看地址：{meta.get('view_address') or '未获取'}",
            "- 说明：腾讯开放接口只返回在线观看地址，且为短时效链接；需要文件本体时用有录制权限的账号打开该页。",
        ]
        if match_note:
            lines.append(f"- 注意：{match_note}")
        if meta.get("address_error"):
            lines.append(f"- 地址补充获取失败：{meta['address_error']}")
    lines += [
        "",
        "## 来源与生成",
        f"- 转写/分析：{source_label}",
        f"- 录制元信息：腾讯会议 MCP `get_records_list`（凭据来自环境变量 `{token_env}`，值不落盘）",
        f"- 生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
    ]
    return "\n".join(lines)


async def meeting_record_export(
    meeting_name: str,
    record_file_id: str = "",
    include_record_links: bool = True,
    output_dir: str = "",
    appdata_root: str = "",
) -> str:
    """把一场会议打包成可交付的资料包（只读管道产物，不重跑分析）。

    参数：``meeting_name`` 必须是代码白名单里的会议名（如 ``weekday-alignment-1100``）；
    ``record_file_id`` 省略时为共享存储里"最新一场"，给了就读该场的永久归档；
    ``include_record_links`` 关闭就不调腾讯接口（离线可用）；``output_dir`` 省略时写到
    ``{appdata}/meeting-exports/<日期>-<标题>-<会议号>/``。
    """
    try:
        job = _job_by_name(meeting_name.strip())
        if job is None:
            return json.dumps(
                {
                    "ok": False,
                    "status": "unknown_meeting",
                    "meeting_name": meeting_name,
                    "allowed_meetings": [item.name for item in MEETING_JOBS],
                    "hint": "会议名必须是代码白名单里的任务名；新增会议要走代码 PR。",
                },
                ensure_ascii=False,
            )
        base = await resolve_appdata_root(appdata_root)
        root = meeting_artifact_root(base, job.name)
        manifest = read_meeting_manifest(base, job.name)
        requested_id = record_file_id.strip()
        if requested_id:
            source = root / "archive" / _safe_record_dir_name(requested_id)
            source_label = f"永久归档 archive/{requested_id}/"
        else:
            source = root
            requested_id = str(manifest.get("record_file_id") or "").strip()
            source_label = "共享存储最新一场（管道落盘产物）"
        if not await anyio.Path(str(source)).is_dir():
            return json.dumps(
                {
                    "ok": False,
                    "status": "record_not_found",
                    "meeting_name": job.name,
                    "record_file_id": record_file_id.strip(),
                    "hint": "该场次没有归档目录；先确认 record_file_id，或用省略参数导出最新一场。",
                },
                ensure_ascii=False,
            )
        if not requested_id:
            return json.dumps(
                {"ok": False, "status": "no_record", "meeting_name": job.name, "hint": "该会议还没有已处理场次。"},
                ensure_ascii=False,
            )

        token_env = meeting_credential_env(job.name, job.meeting_code)
        meta: dict[str, Any] = {}
        meta_error = ""
        match_note = ""
        if include_record_links:
            meta, exact, meta_error = await _fetch_record_meta(job, requested_id, token_env)
            if meta and not exact:
                match_note = (
                    "腾讯列表里没有与该转写 record_file_id 完全对应的场次，"
                    "上面的云端信息取自最新一场（近似匹配，请人工核对）"
                )
            elif not meta and not meta_error:
                meta_error = "腾讯返回里没有该会议的录制记录"

        fallback_date = datetime.now().astimezone().date().isoformat()
        meeting_date = (
            _date_part(meta.get("meeting_start_time"), "")
            or _date_part(meta.get("record_start_time"), "")
            or fallback_date
        )
        if output_dir.strip():
            candidate = output_dir.strip()
            # anyio.Path: 仓库约定 async 上下文不用同步 pathlib / os.path。
            if not anyio.Path(candidate).is_absolute():
                return json.dumps(
                    {"ok": False, "status": "invalid_output_dir", "error": "output_dir 必须是绝对路径"},
                    ensure_ascii=False,
                )
            resolved = await anyio.Path(candidate).resolve()
            pipeline_root = await anyio.Path(str(root)).resolve()
            if str(resolved).startswith(str(pipeline_root)):
                return json.dumps(
                    {
                        "ok": False,
                        "status": "unsafe_output_dir",
                        "error": "output_dir 不能落在会议管道产物目录内",
                    },
                    ensure_ascii=False,
                )
            dest = Path(str(resolved))
        else:
            dest = Path(base) / "meeting-exports" / f"{meeting_date}-{job.title}-{job.meeting_code}"

        await anyio.Path(str(dest)).mkdir(parents=True, exist_ok=True)
        exported: list[tuple[str, int]] = []
        missing: list[str] = []
        for name in _EXPORT_FILES:
            src = anyio.Path(str(source / name))
            if not await src.is_file():
                missing.append(name)
                continue
            text = await src.read_text(encoding="utf-8")
            await atomic_write_text(dest / name, text)
            exported.append((name, len(text.encode("utf-8"))))
        info_md = _render_info_md(
            job=job,
            record_file_id=requested_id,
            files=exported,
            meta=meta,
            meta_error=meta_error,
            source_label=source_label,
            token_env=token_env,
            match_note=match_note,
        )
        await atomic_write_text(dest / "录制与转写信息.md", info_md)
        return json.dumps(
            {
                "ok": True,
                "status": "exported",
                "meeting_name": job.name,
                "meeting_code": job.meeting_code,
                "record_file_id": requested_id,
                "meeting_date": meeting_date,
                "output_dir": str(dest),
                "files": [name for name, _ in exported],
                "missing": missing,
                "record_links": {
                    "view_address": str(meta.get("view_address") or ""),
                    "meeting_record_id": str(meta.get("meeting_record_id") or ""),
                },
                **({"record_error": meta_error} if meta_error else {}),
                "hint": "把 output_dir 里的文件用 [SEND:] 发给用户；不要重跑分析，也不要自写拉取脚本。",
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_record_export_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_record_export"]
