"""Shared meeting automation primitives.

Meeting jobs are deliberately code-owned rather than user-configured.  Their
schedules ship as static company seed tasks under ``agents/feishu/schedules``
(one ``TASK.md`` per job and per retry, projected from :data:`MEETING_JOBS` by
:func:`meeting_schedule_files`); the generic SchedulerManager seed mechanism
drops them into the configured company workspace and owns the scheduler
Session.  Meeting artifacts live in AppData so every Feishu Session can read
the same source and analysis results.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import anyio
import yaml

from psi_agent._appdata import resolve_appdata_root

_AGENT_ROOT = Path(__file__).resolve().parent.parent
#: 会议自动化运行口径 (config/meeting-automation.yaml, 契约同 todo-sop.yaml)。
#: 硬边界: 白名单 (name/code/cron/retry/token_env 配对) 一律留代码, 见
#: ``_MEETING_JOBS_WHITELIST`` 与加载器对覆盖键的白名单校验。
MEETING_AUTOMATION_CONFIG_PATH = _AGENT_ROOT / "config" / "meeting-automation.yaml"
_MEETING_OVERLAY_KEYS = frozenset(
    {
        "title",
        "summary_recipients",
        "overview_recipients",
        "analysis_sop_skills",
        "alert_recipients",
    }
)
_WHITELIST_ONLY_KEYS = frozenset(
    {"name", "meeting_code", "cron", "retry_crons", "fire", "tool_name", "tool_args", "token_env"}
)
#: 收件人解析表允许的目标键(见 ``notify_recipients``): person/chat 走解析, 其余是显式 id 类型。
_RECIPIENT_TARGET_KEYS = ("person", "chat", "open_id", "user_id", "union_id", "chat_id", "email")


def load_meeting_automation_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取并契约校验 ``meeting-automation.yaml``; 缺失/解析失败/越权键一律显式报错。"""
    config_path = Path(path) if path is not None else MEETING_AUTOMATION_CONFIG_PATH
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"会议自动化配置缺失: {config_path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"会议自动化配置无法解析: {config_path}: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), dict) for key in ("meetings", "runtime", "resources")
    ):
        raise RuntimeError(f"会议自动化配置不符合契约(需 meetings/runtime/resources 段): {config_path}")
    meetings = data["meetings"]
    for name, overlay in meetings.items():
        if not isinstance(overlay, dict):
            raise RuntimeError(f"meetings.{name} 必须是对象: {config_path}")
        extra = set(overlay) - _MEETING_OVERLAY_KEYS
        if extra:
            hint = "授权(白名单/token_env/调度)不可经配置文件修改" if extra & _WHITELIST_ONLY_KEYS else "未知字段"
            raise RuntimeError(f"meetings.{name} 含不允许的键 {sorted(extra)} ({hint}): {config_path}")
    return data


MEETING_AUTOMATION_CONFIG = load_meeting_automation_config()


def automation_runtime() -> dict[str, Any]:
    """meeting-automation.yaml 的 ``runtime`` 段 (引擎运行参数)。"""
    return MEETING_AUTOMATION_CONFIG["runtime"]


def automation_resources() -> dict[str, Any]:
    """meeting-automation.yaml 的 ``resources`` 段 (相对 agent 包根的引用路径)。"""
    return MEETING_AUTOMATION_CONFIG["resources"]


#: 引擎常量: 单一来源为 yaml (runtime.analysis.chunk_chars); 此处仅为同值导出, 测试可覆写。
TRANSCRIPT_CHUNK_CHARS = int(MEETING_AUTOMATION_CONFIG["runtime"]["analysis"]["chunk_chars"])


@dataclass(frozen=True)
class MeetingJob:
    name: str
    meeting_code: str
    cron: str
    title: str
    retry_crons: tuple[str, ...] = ()
    # 投递收件人一律来自 meeting-automation.yaml: 代码里不留任何具体人名/群名,
    # 缺收件人的 job 在模块导入时就报错(见 _validate_recipients), 不会静默变成
    # "没人收"。改人/改群只改配置, 不需要动代码。
    summary_recipients: tuple[str, ...] = ()
    overview_recipients: tuple[str, ...] = ()
    # Versioned SOP skill(s) whose text is injected verbatim into scheduled
    # analysis prompts ("meeting-sop/<name>").  Missing file is a hard error.
    analysis_sop_skills: tuple[str, ...] = ()
    # Ops contacts notified when a run fails (prepare/analysis/notify).
    alert_recipients: tuple[str, ...] = ()
    token_env: str = "TENCENT_MEETING_TOKEN"
    fire: str = "tool"
    tool_name: str = "meeting_pipeline_run"
    tool_args: tuple[tuple[str, str], ...] = ()


#: 代码权威白名单: name/meeting_code/cron/retry/token_env 配对 (授权门) 只能改这里。
#: 投递收件人与告警收件人**不在代码里**: 它们必须由 meeting-automation.yaml 的
#: meetings 段给出(缺了直接报错)。SOP skill 与展示标题属结构性口径, 仍留代码。
_MEETING_JOBS_WHITELIST: tuple[MeetingJob, ...] = (
    MeetingJob(
        name="weekday-alignment",
        meeting_code="57152787045",
        cron="0 12 * * 1,3,5",
        title="周中对齐会",
        # 腾讯的文字转写是异步产出的, 主跑时常还没生成(实测 09-11: 12:00 主跑没有,
        # 21:59 才生成)。这条会议此前没有任何兜底 → 当天没赶上就永远不重跑
        # (管道只认最新 occurrence, 下一次主跑时最新已是下一场)。故补一条 17:30 的补偿重跑。
        # 17:30 是历史档位, 覆盖上述"晚上才出转写"的情形。
        # 幂等: 主跑已成功投递时按 record 去重自动跳过, 不会重复发卡。
        retry_crons=("30 17 * * 1,3,5",),
        analysis_sop_skills=("meeting-sop/weekday-alignment",),
        tool_args=(("meeting_name", "weekday-alignment"), ("meeting_code", "57152787045")),
    ),
    MeetingJob(
        name="weekday-alignment-1100",
        meeting_code="42654699903",
        cron="0 13 * * 1,3,5",
        title="日会",
        # 日会同样是异步转写: 13:00 主跑常在转写生成前, 故与周中会一致留一条 17:30 兜底。
        retry_crons=("30 17 * * 1,3,5",),
        analysis_sop_skills=("meeting-sop/weekday-alignment",),
        token_env="TENCENT_MEETING_TOKEN_42654699903",
        tool_args=(("meeting_name", "weekday-alignment-1100"), ("meeting_code", "42654699903")),
    ),
)


def _meeting_jobs_with_overlay() -> tuple[MeetingJob, ...]:
    """白名单 (代码) + yaml meetings 覆盖 (仅允许的五个口径键) 合并出运行时会议列表。

    yaml 里出现的会议名必须全部命中代码白名单 (授权不可经配置文件扩展); 白名单中的
    会议也必须有 yaml 条目 (防止误删后口径悄然回退代码缺省)——收件人正是靠这一条
    从代码里搬进了配置。
    """
    meetings: dict[str, Any] = MEETING_AUTOMATION_CONFIG["meetings"]
    whitelist_names = {job.name for job in _MEETING_JOBS_WHITELIST}
    extra = set(meetings) - whitelist_names
    if extra:
        raise RuntimeError(f"meeting-automation.yaml 含白名单外会议, 授权不可经配置文件扩展: {sorted(extra)}")
    merged: list[MeetingJob] = []
    for job in _MEETING_JOBS_WHITELIST:
        overlay = meetings.get(job.name)
        if overlay is None:
            raise RuntimeError(f"代码白名单会议 {job.name} 缺少 meeting-automation.yaml 的 meetings.{job.name} 条目")
        kwargs: dict[str, Any] = {}
        for key, value in overlay.items():
            kwargs[key] = tuple(value) if isinstance(value, list) else value
        merged.append(replace(job, **kwargs))
    jobs = tuple(merged)
    _validate_recipients(jobs)
    return jobs


def _validate_recipients(jobs: tuple[MeetingJob, ...]) -> None:
    """每场会议都必须有投递收件人; 缺了在导入期报错, 而不是发不出去才被发现。

    收件人只从 meeting-automation.yaml 来(代码不再留缺省)。空的收件人清单会让整条
    流水线「跑成功但不投递」—— 报告里看不见、日志里也不报错, 所以这里 fail-fast。
    """
    for job in jobs:
        pairs = (("summary_recipients", job.summary_recipients), ("overview_recipients", job.overview_recipients))
        for field, values in pairs:
            if not values:
                raise RuntimeError(
                    f"meeting-automation.yaml 的 meetings.{job.name} 缺少 {field}: 收件人只从配置来, "
                    f"代码不留缺省: {MEETING_AUTOMATION_CONFIG_PATH}"
                )
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError(f"meetings.{job.name}.{field} 含空收件人: {values!r}")


def notify_recipients() -> dict[str, dict[str, str]]:
    """``runtime.notify.recipients`` —— 收件人解析表(键 → 目标)。

    键就是调用方传给 ``meeting_session_notify`` 的 recipient 字符串。目标**恰好一个**:
    ``person``(姓名, 走通讯录拿当前应用的 open_id)、``chat``(群名, 走群名解析)、或一个
    显式 id 类型 ``open_id`` / ``user_id`` / ``union_id`` / ``chat_id`` / ``email``
    (直接使用, **不做任何姓名解析**)。

    **免解析优先用租户级 ``user_id``**: ``open_id``/``chat_id`` 按应用隔离(换个应用就
    失效, 飞书报 99992361), ``user_id``/``union_id`` 是租户级的、跨应用不变。

    表缺失 = 空表(此时只剩"直接给 id"与"按姓名解析"两条路), 但**形状写错一律显式报错**
    —— 一个写错的收件人不能静默失效。
    """
    notify_section = MEETING_AUTOMATION_CONFIG["runtime"].get("notify", {})
    table = notify_section.get("recipients", {}) if isinstance(notify_section, dict) else {}
    if not isinstance(table, dict):
        raise RuntimeError(f"runtime.notify.recipients 必须是「键 → 对象」的映射: {MEETING_AUTOMATION_CONFIG_PATH}")
    parsed: dict[str, dict[str, str]] = {}
    for key, entry in table.items():
        name = str(key).strip()
        if not name:
            raise RuntimeError(f"runtime.notify.recipients 含空键: {MEETING_AUTOMATION_CONFIG_PATH}")
        if isinstance(entry, str):
            entry = {"person": entry}
        if not isinstance(entry, dict):
            raise RuntimeError(f"runtime.notify.recipients.{name} 必须是对象(或直接写姓名): {entry!r}")
        unknown = set(entry) - set(_RECIPIENT_TARGET_KEYS)
        if unknown:
            raise RuntimeError(
                f"runtime.notify.recipients.{name} 含未知键 {sorted(unknown)}; 只允许 {list(_RECIPIENT_TARGET_KEYS)}"
            )
        targets = {k: str(entry.get(k) or "").strip() for k in _RECIPIENT_TARGET_KEYS}
        filled = [k for k, value in targets.items() if value]
        if len(filled) != 1:
            raise RuntimeError(
                f"runtime.notify.recipients.{name} 必须**恰好**给一个目标({list(_RECIPIENT_TARGET_KEYS)}), "
                f"现在给了 {filled or '零个'}: {entry!r}"
            )
        parsed[name] = targets
    return parsed


#: 导入期就校验一次收件人解析表: 表写错 = 启动即失败, 而不是等到某天发纪要才发现。
#: 调用方仍用 ``notify_recipients()`` 取当前表(测试可 monkeypatch 该函数)。
NOTIFY_RECIPIENTS: dict[str, dict[str, str]] = notify_recipients()


MEETING_JOBS: tuple[MeetingJob, ...] = _meeting_jobs_with_overlay()


def meeting_credential_env(meeting_name: str, meeting_code: str) -> str:
    """Return the process environment variable for one fixed meeting.

    Credentials stay outside code and tool arguments.  The meeting name/code
    pair is deliberately checked here so a task cannot accidentally use the
    host credential for a different meeting.
    """

    name = meeting_name.strip()
    code = meeting_code.strip()
    return meeting_job_for(name, code).token_env


def meeting_job_for(meeting_name: str, meeting_code: str) -> MeetingJob:
    """Return the code-owned job matching a meeting name and number."""

    name = meeting_name.strip()
    code = meeting_code.strip()
    for job in MEETING_JOBS:
        if job.name == name and job.meeting_code == code:
            return job
    raise ValueError(f"meeting {name!r} and code {code!r} does not match a fixed meeting")


def meeting_store_root(appdata_root: str = "") -> Path:
    """Return the shared artifact root without relying on a user Session."""

    explicit = appdata_root.strip() or os.environ.get("PSI_APPDATA", "").strip()
    # resolve_appdata_root is async for filesystem normalization; tools call the
    # async ``async_meeting_store_root`` helper below.  This sync helper is for
    # tests and callers that already have an absolute env path.
    if explicit:
        return Path(explicit).expanduser().resolve() / "meeting-session"
    return Path.home() / ".local" / "share" / "Haitun" / "meeting-session"


async def async_meeting_store_root(appdata_root: str = "") -> Path:
    root = await resolve_appdata_root(appdata_root)
    return Path(root) / "meeting-session"


def chunk_text(text: str, max_chars: int = TRANSCRIPT_CHUNK_CHARS) -> list[str]:
    """Split text without dropping bytes or returning an oversized tool result."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)] or [""]


def _json_payload(raw: str | dict[str, Any] | list[Any]) -> Any:
    """Decode a Tencent MCP text response, tolerating a small log prefix."""
    if isinstance(raw, (dict, list)):
        payload: Any = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # The proxy normally prints JSON, but error messages or wrappers can put
            # a line before it.  Find the first complete object/array conservatively.
            for match in re.finditer(r"[\[{]", text):
                try:
                    payload = json.loads(text[match.start() :])
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise ValueError(f"Tencent Meeting response is not JSON: {text[:240]}")

    if isinstance(payload, dict) and "status_code" in payload and "body" in payload:
        body = payload.get("body")
        decoded = _json_payload(body) if isinstance(body, (str, dict, list)) else {}
        if isinstance(decoded, dict):
            headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
            decoded = dict(decoded)
            decoded["_transport"] = {
                "status_code": payload.get("status_code"),
                "x_tc_trace": headers.get("X-Tc-Trace") or headers.get("x-tc-trace") or "",
                "rpc_uuid": headers.get("rpcUuid") or headers.get("Rpcuuid") or "",
            }
        return decoded
    return payload


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _record_file_type(record: dict[str, Any]) -> str:
    return (
        str(
            record.get("record_file_type")
            or record.get("file_type")
            or record.get("record_type")
            or record.get("type")
            or ""
        )
        .strip()
        .casefold()
    )


def _is_transcript_record(record: dict[str, Any]) -> bool:
    value = _record_file_type(record)
    return any(token in value for token in ("文字转写", "转写", "transcript", "transcription", "text"))


def _record_sort_key(record: dict[str, Any]) -> str:
    for key in (
        "media_start_time",
        "record_start_time",
        "start_time",
        "end_time",
        "create_time",
        "created_at",
        "update_time",
    ):
        value = record.get(key)
        if value:
            return str(value)
    return ""


def _record_candidates(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(payload, dict):
        meetings = payload.get("record_meetings")
        if isinstance(meetings, list):
            for meeting in meetings:
                if not isinstance(meeting, dict):
                    continue
                files = meeting.get("record_files")
                if not isinstance(files, list):
                    continue
                for record_file in files:
                    if not isinstance(record_file, dict):
                        continue
                    normalized = {key: value for key, value in meeting.items() if key != "record_files"}
                    normalized.update(record_file)
                    normalized["record_file_type"] = normalized.get("record_file_type") or meeting.get("record_type")
                    record_file_id = str(normalized.get("record_file_id") or "").strip()
                    if record_file_id and record_file_id not in seen:
                        candidates.append(normalized)
                        seen.add(record_file_id)
    for record in _walk_dicts(payload):
        record_file_id = str(record.get("record_file_id") or record.get("file_id") or "").strip()
        if record_file_id and record_file_id not in seen:
            candidates.append(dict(record))
            seen.add(record_file_id)
    return candidates


def extract_latest_transcript_record(payload: Any, processed_ids: set[str] | None = None) -> dict[str, Any] | None:
    """Find a completed transcript from the newest meeting occurrence only."""
    processed = processed_ids or set()
    records = _record_candidates(payload)
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        occurrence_id = str(record.get("sub_meeting_id") or "").strip()
        if occurrence_id:
            occurrences.setdefault(occurrence_id, []).append(record)
    if occurrences:
        latest_occurrence_id = max(
            occurrences,
            key=lambda occurrence_id: max(_record_sort_key(record) for record in occurrences[occurrence_id]),
        )
        records = occurrences[latest_occurrence_id]
        # 「还没就绪」的检查只对**转写类**记录生效: 同一 occurrence 里常常还有云录制,
        # 其中一段可能仍在"录制中/转码中", 那不是"这场会议的转写还没好"。此前对整组
        # 所有记录做状态检查, 于是一条未完成的云录制会把**已经完成的文字转写**一起否掉
        # (实测 09-11 那场: state=3 的文字转写 + state=1 的云录制 → 返回「没有转写」,
        # 而当天 21:59 转写其实已经生成)。
        if any(
            _is_transcript_record(record)
            and record.get("state_int", record.get("state")) is not None
            and not should_process_recording({**record, "record_file_id": "__state_check__"}, set())
            for record in records
        ):
            return None
    candidates: list[dict[str, Any]] = []
    for record in records:
        record_file_id = str(record.get("record_file_id") or record.get("file_id") or "").strip()
        if not record_file_id or not _is_transcript_record(record):
            continue
        normalized = dict(record)
        normalized["record_file_id"] = record_file_id
        if should_process_recording(normalized, processed):
            candidates.append(normalized)
    return max(candidates, key=_record_sort_key) if candidates else None


def _record_day(record: dict[str, Any]) -> date | None:
    """记录所属的日历日 (腾讯返回的是带时区的 ISO 时间戳)。解析不出来返回 ``None``。"""
    raw = str(record.get("media_start_time") or record.get("record_start_time") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def same_day_record_candidates(
    payload: Any, record: dict[str, Any], *, processed_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """同一天里其他**已完成且未处理**的记录 —— 主记录取不到正文时的回退候选 (新到旧)。

    为什么需要这个: 腾讯**不保证每场会都产出「文字转写」记录**。实测 2026-09-11 周中会:
    当天唯一那条「文字转写」属于一段 46 秒的杂散录制 (21:59:58-22:01:34), 在腾讯侧没有
    内容 —— 段落索引 ``total=0``、详情 ``HTTP 500``、智能纪要 ``500260 会议无有效转写内容``;
    而那场真会 (09:48 起, 14 人) 的正文只挂在它的**云录制**记录上。管道原先只认「文字转写」,
    于是"明明有转写却报 prepare_failed"。

    只取**同一日历日**: 退到前一天的记录等于把上一场当今天发出去 —— 那是另一个已经修过的
    问题 (见 ``meeting_pipeline_run`` 的 ``_NO_NEW_TRANSCRIPT_STATUSES``)。
    """
    anchor_day = _record_day(record)
    if anchor_day is None:
        return []
    selected_id = str(record.get("record_file_id") or "").strip()
    processed = processed_ids or set()
    candidates: list[dict[str, Any]] = []
    for candidate in _record_candidates(payload):
        record_file_id = str(candidate.get("record_file_id") or candidate.get("file_id") or "").strip()
        if not record_file_id or record_file_id == selected_id:
            continue
        if _record_day(candidate) != anchor_day:
            continue
        normalized = dict(candidate)
        normalized["record_file_id"] = record_file_id
        if not should_process_recording(normalized, processed):
            continue
        candidates.append(normalized)
    return sorted(candidates, key=_record_sort_key, reverse=True)


def _paragraph_id(item: dict[str, Any]) -> str:
    return str(item.get("pid") or item.get("paragraph_id") or item.get("id") or "").strip()


def extract_paragraph_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    for item in _walk_dicts(payload):
        pid = _paragraph_id(item)
        if pid and pid not in ids and any(key in item for key in ("pid", "paragraph_id")):
            ids.append(pid)
    return ids


def extract_paragraph_items(payload: Any) -> list[dict[str, Any]]:
    """Return paragraph-like objects while avoiding duplicate nested wrappers."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk_dicts(payload):
        pid = _paragraph_id(item)
        has_content = any(key in item for key in ("sentences", "words", "content", "text"))
        if not pid or not has_content or pid in seen:
            continue
        seen.add(pid)
        items.append(item)
    return items


def render_transcript_paragraphs(paragraphs: list[dict[str, Any]]) -> str:
    """Render all raw transcript paragraphs into readable, lossless text."""
    lines: list[str] = []
    for paragraph in paragraphs:
        timestamp = str(
            paragraph.get("timestamp") or paragraph.get("start_time") or paragraph.get("start") or ""
        ).strip()
        speaker = str(
            paragraph.get("speaker")
            or paragraph.get("speaker_name")
            or paragraph.get("user_name")
            or paragraph.get("username")
            or "未知发言人"
        ).strip()
        pieces: list[str] = []
        for sentence in paragraph.get("sentences", []) if isinstance(paragraph.get("sentences"), list) else []:
            if isinstance(sentence, dict):
                value = sentence.get("text") or sentence.get("content") or sentence.get("sentence")
                if not value and isinstance(sentence.get("words"), list):
                    value = "".join(
                        str(word.get("text") or word.get("content") or "")
                        for word in sentence["words"]
                        if isinstance(word, dict)
                    )
            else:
                value = sentence
            if value:
                pieces.append(str(value))
        if not pieces:
            content = paragraph.get("content") or paragraph.get("text") or ""
            if isinstance(content, list):
                pieces.extend(
                    str(item.get("text") or item.get("content") or "") for item in content if isinstance(item, dict)
                )
            elif content:
                pieces.append(str(content))
        content_text = "".join(piece for piece in pieces if piece)
        if not content_text:
            continue
        prefix = f"[{timestamp}] " if timestamp else ""
        lines.append(f"{prefix}{speaker}: {content_text}")
    return "\n".join(lines)


def meeting_artifact_root(appdata_root: str, meeting_name: str) -> Path:
    name = meeting_name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("meeting_name must be a simple directory name")
    return meeting_store_root(appdata_root) / name


def read_meeting_manifest(appdata_root: str | Path, meeting_name: str) -> dict[str, Any]:
    path = meeting_artifact_root(str(appdata_root), meeting_name) / "manifest.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def should_process_recording(record: dict[str, Any], processed_ids: set[str]) -> bool:
    """Only process completed, previously unseen transcript recording files."""

    record_file_id = str(record.get("record_file_id") or "").strip()
    if not record_file_id or record_file_id in processed_ids:
        return False
    state = record.get("state_int", record.get("state"))
    if isinstance(state, str):
        state_text = state.strip().lower()
        if state_text not in {"3", "completed", "转码完成", "transcoded"}:
            return False
    elif state is not None and int(state) != 3:
        return False
    return True


#: 补偿重跑条目在 description 与正文里都自报身份: 否则「有哪些定时任务」看到的 4 条
#: 里, 两条主任务与两条补偿重跑描述一模一样, 读不出哪条是兜底 (2026-09-11 评审)。
_RETRY_NOTE = "补偿重跑"


def _cron_clock(cron: str) -> str:
    """``"30 17 * * 1,3,5"`` → ``"17:30"`` —— 只给人读的时点文案, 解析失败原样返回。"""
    fields = cron.split()
    if len(fields) < 2 or not (fields[0].isdigit() and fields[1].isdigit()):
        return cron
    return f"{int(fields[1]):02d}:{int(fields[0]):02d}"


def _task_body(
    job: MeetingJob,
    *,
    name: str | None = None,
    cron: str | None = None,
    retry: bool = False,
) -> str:
    """渲染一条 seed ``TASK.md``。

    ``fire: tool`` 的正文不进入模型 (调度器直接调工具, 见
    ``psi_agent.session.schedule_registry._fire_tool``), 但它是读文件的人与调度历史
    里唯一的自述, 所以写清 做什么 / 口径在哪 / 失败了怎么补。

    正文是给人和调度历史读的中文文案, 全角标点刻意保留, 逐行豁免 RUF001 (与
    ``_card_dsl`` 的卡片文案同一处理)。
    """
    tool_args = json.dumps(dict(job.tool_args), ensure_ascii=False, separators=(",", ":"))
    schedule_cron = cron or job.cron
    description = f"会后自动获取{job.title}原始全文转写并分析, 产出本场评价与后续建议"
    lines = [
        f"本任务由调度器直接调用 {job.tool_name}（fire: {job.fire}，不经过模型）；"  # noqa: RUF001
        "上面的 tool_args 即全部入参。",
        "",
        f"- 做什么：取「{job.title}」最新已完成场次的原始转写，"  # noqa: RUF001
        "按 config/meeting-sop.yaml 的生效条目逐条判定；"  # noqa: RUF001
        "产出「本场 SOP 判定 + 本场会议评价 + 后续建议」，"  # noqa: RUF001
        "投递给 config/meeting-automation.yaml 的收件人。",
        "- 口径在哪：config/meeting-sop.yaml（可编辑的判定条目，改口径只改这里）+ "  # noqa: RUF001
        "skills/meeting-sop/weekday-alignment/SKILL.md（判定纪律与输出结构）。",  # noqa: RUF001
        "- 失败怎么办：按 record_file_id 去重；失败告警发给 alert_recipients；"  # noqa: RUF001
        "补跑/补发用 meeting_pipeline_replay(meeting_name, record_file_id)。",
    ]
    if retry:
        description += f"（{_cron_clock(schedule_cron)} {_RETRY_NOTE}，主任务已投递则自动跳过）"  # noqa: RUF001
        lines.append(
            f"- 本条是 {_cron_clock(schedule_cron)} 的{_RETRY_NOTE}："  # noqa: RUF001
            f"主任务（{job.cron}）已成功投递时，"  # noqa: RUF001
            "本次按 record 去重自动跳过，不重复投递。"  # noqa: RUF001
        )
    return f"""---
name: {name or job.name}
description: {description}
cron: \"{schedule_cron}\"
visibility: silent
fire: {job.fire}
tool: {job.tool_name}
tool_args: {tool_args}
---

{chr(10).join(lines)}
"""


def _job_schedules(job: MeetingJob) -> list[tuple[str, str]]:
    schedules = [(job.name, job.cron)]
    for retry_cron in job.retry_crons:
        fields = retry_cron.split()
        if len(fields) != 5:
            raise ValueError(f"invalid retry cron for {job.name}: {retry_cron}")
        minute, hour = fields[:2]
        schedules.append((f"{job.name}-retry-{hour.zfill(2)}{minute.zfill(2)}", retry_cron))
    return schedules


def meeting_schedule_files() -> dict[str, str]:
    """Project ``MEETING_JOBS`` onto static seed TASK.md files.

    Returns ``{schedule directory name: TASK.md content}`` for every job cron
    and every retry cron.  The committed files under
    ``agents/feishu/schedules/<name>/TASK.md`` must equal this projection —
    the scheduler seeds them into the company workspace verbatim (only when
    missing), and the consistency test pins both sides to this single source.
    """
    files: dict[str, str] = {}
    for job in MEETING_JOBS:
        for schedule_name, cron in _job_schedules(job):
            files[schedule_name] = _task_body(job, name=schedule_name, cron=cron, retry=cron != job.cron)
    return files


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_PATH_LOCKS: dict[str, anyio.Lock] = {}
_PATH_LOCKS_GUARD = anyio.Lock()


async def path_lock(path: str | Path) -> anyio.Lock:
    """进程内按路径去重的互斥锁 (读-改-写同一产物文件时使用)。

    跨进程仍依赖「同一 Gateway 只 watch 一份」的单实例约定 (与 notify 的回执锁
    同思路); 锁只防同进程内定时触发与手动重跑并发。
    """
    key = str(path)
    async with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = anyio.Lock()
            _PATH_LOCKS[key] = lock
        return lock


async def atomic_write_text(path: str | Path, text: str) -> None:
    """原子写文本文件: 同目录临时文件 + ``os.replace``, 读方永远不会看到半截内容。

    写失败时清理临时文件并把异常原样抛出 (不静默)。
    """
    target = Path(path)
    await anyio.Path(str(target.parent)).mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        await anyio.Path(str(tmp)).write_text(text, encoding="utf-8")
        await anyio.to_thread.run_sync(os.replace, str(tmp), str(target))  # ty: ignore
    finally:
        if tmp.exists():
            with suppress(OSError):
                tmp.unlink()


__all__ = [
    "MEETING_AUTOMATION_CONFIG_PATH",
    "MEETING_JOBS",
    "NOTIFY_RECIPIENTS",
    "MeetingJob",
    "async_meeting_store_root",
    "atomic_write_text",
    "automation_resources",
    "automation_runtime",
    "chunk_text",
    "extract_latest_transcript_record",
    "extract_paragraph_ids",
    "extract_paragraph_items",
    "json_text",
    "load_meeting_automation_config",
    "meeting_artifact_root",
    "meeting_credential_env",
    "meeting_job_for",
    "meeting_schedule_files",
    "meeting_store_root",
    "notify_recipients",
    "path_lock",
    "read_meeting_manifest",
    "render_transcript_paragraphs",
    "same_day_record_candidates",
    "should_process_recording",
]
