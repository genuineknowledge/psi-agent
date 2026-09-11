"""Run the deterministic daily-meeting analysis pipeline.

One run covers: prepare (fetch latest completed transcript + smart minutes) →
read the complete raw transcript chunk by chunk → AI analysis with versioned
SOP / positive-negative rule snapshots → fixed-route delivery with idempotent
receipts.  Every terminal state is appended to ``run_metrics.jsonl``; hard
failures additionally alert the job's ``alert_recipients`` (once per record /
per day), so a silent scheduler never fails silently.

Terminal states: ``completed`` / ``notifications_pending`` /
``transcript_prepare_failed`` / ``transcript_pending`` and **``analysis_empty``**
(刻意为之): when every AI call comes back without body text (or the parsed JSON
carries only empty fields), the run ends as ``analysis_empty`` instead of
``completed``, sends **no** placeholder message to the recipients, and persists
per-call stream diagnostics (``analysis.ai_content_chars`` /
``ai_reasoning_chars`` / ``ai_empty_calls`` in run metrics) so an empty-output
incident is distinguishable from a normal run on the next morning.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
import yaml
from _meeting_archive import archive_meeting_record
from _meeting_automation import (
    MeetingJob,
    atomic_write_text,
    automation_resources,
    automation_runtime,
    meeting_artifact_root,
    meeting_job_for,
    path_lock,
    read_meeting_manifest,
)
from _meeting_card import notify_meeting_card, render_meeting_summary_card
from meeting_session_notify import meeting_session_notify
from meeting_session_read import meeting_session_read
from meeting_session_write import meeting_session_write
from meeting_transcript_prepare import meeting_transcript_prepare

from psi_agent._appdata import resolve_appdata_root
from psi_agent._session_context import get_session_id
from psi_agent.session.agent import current_tool_ai_socket
from psi_agent.session.ai_client import AiClient

DAILY_MEETING_NAME = "weekday-alignment"
DAILY_MEETING_CODE = "57152787045"
#: 引擎运行参数单一来源: config/meeting-automation.yaml 的 runtime 段。
_MEETING_RUNTIME = automation_runtime()
_MEETING_RESOURCES = automation_resources()
AGENT_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_CHUNK_CHARS = int(_MEETING_RUNTIME["analysis"]["chunk_chars"])
ANALYSIS_TEMPERATURE = float(_MEETING_RUNTIME["analysis"]["temperature"])
ALERT_MESSAGE_PREFIX = str(_MEETING_RUNTIME["alerts"]["message_prefix"])
ALERT_ERROR_TRUNCATE_CHARS = int(_MEETING_RUNTIME["alerts"]["error_truncate_chars"])
SKILLS_ROOT = AGENT_ROOT / "skills"
#: 会议 SOP 判定口径与正负面规则快照的路径来自 yaml resources (相对 agent 包根);
#: 缺失/契约损坏由 _load_analysis_rules 显式失败。
MEETING_SOP_CONFIG_PATH = AGENT_ROOT / str(_MEETING_RESOURCES["meeting_sop_config_file"])
POSITIVE_NEGATIVE_RULES_PATH = AGENT_ROOT / str(_MEETING_RESOURCES["positive_rules_file"])


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _write_json(path: Path, value: dict[str, Any]) -> None:
    await atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.S).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return {"analysis_text": text.strip()}
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return {"analysis_text": text.strip()}
    return value if isinstance(value, dict) else {"analysis_text": text.strip()}


#: 模型未产出任何正文时的 analysis_text 兜底文案。判定「空分析」时该值视为空
#: (见 _analysis_has_content), 避免占位文案被当成真内容投递出去(曾经整段占位
#: 消息被发给收件人, 见 analysis_empty 状态引入的原因)。
_EMPTY_ANALYSIS_MARKER = "未生成分析结果。"


#: 分析正文的三个字段: 契约要求值必须是自然语言字符串(可含换行), 不得是对象/数组/数字。
_ANALYSIS_TEXT_KEYS: tuple[str, ...] = ("analysis_text", "meeting_summary", "positive_negative_overview")

#: 兼容模型/历史状态用过的别名字段。
_ANALYSIS_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "analysis_text": ("analysis",),
    "meeting_summary": ("minutes",),
    "positive_negative_overview": ("ledger_overview",),
}

#: Python 字面量痕迹: 值曾被 str() 成 repr 时的特征(如 ``{'meeting_type': '周中对齐会'}``)。
_PYTHON_REPR_MARKERS: tuple[str, ...] = ("{'", "'}", "': '", "[{'", "'}]")


def _analysis_has_content(analysis: dict[str, Any]) -> bool:
    """True 当三个正文字段中至少一个非空且不等于占位文案。"""
    for key in _ANALYSIS_TEXT_KEYS:
        value = str(analysis.get(key) or "").strip()
        if value and value != _EMPTY_ANALYSIS_MARKER:
            return True
    return False


def _analysis_stats_entry(stats: dict[str, int]) -> dict[str, int]:
    """run_metrics 的 analysis 统计块: 调用数/输入字符 + AI 流诊断。

    ai_content_chars / ai_reasoning_chars / ai_empty_calls 区分两类空分析事故:
    content=0 且 reasoning>0 说明模型只思考未产出正文, 两者都 0 说明上游空回。
    """
    return {
        "ai_calls": stats.get("ai_calls", 0),
        "ai_input_chars": stats.get("ai_input_chars", 0),
        "ai_content_chars": stats.get("ai_content_chars", 0),
        "ai_reasoning_chars": stats.get("ai_reasoning_chars", 0),
        "ai_empty_calls": stats.get("ai_empty_calls", 0),
    }


def _flatten_value(value: Any, *, _depth: int = 0) -> str:
    """把模型写成对象/数组的字段展平成人类可读文本。

    契约要求这三个字段是自然语言字符串, 但模型偶尔会写成嵌套对象。以前这里用
    ``str(value)``, 对 dict 得到的是带单引号的 Python 字面量, 于是「一坨字典」被原样
    发进会议群/私聊(2026-09-11 事故)。现在逐层展平成 ``键: 值`` / ``- 值`` 文本,
    模型再犯也不会把 repr 发出去。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        pad = "  " * _depth
        lines: list[str] = []
        for key, item in value.items():
            name = str(key)
            if name.startswith("_"):  # 内部诊断块(如 _ai_stream)不进正文
                continue
            flat = _flatten_value(item, _depth=_depth + 1)
            if not flat:
                continue
            if isinstance(item, (dict, list, tuple)):
                lines.append(f"{pad}{name}:")
                lines.append(flat)
            else:
                lines.append(f"{pad}{name}: {flat}")
        return "\n".join(lines).strip()
    if isinstance(value, (list, tuple)):
        pad = "  " * _depth
        bullets: list[str] = []
        for item in value:
            flat = _flatten_value(item, _depth=_depth + 1)
            if not flat:
                continue
            if isinstance(item, (dict, list, tuple)):
                bullets.append(f"{pad}-")
                bullets.append(flat)
            else:
                bullets.append(f"{pad}- {flat}")
        return "\n".join(bullets).strip()
    return str(value).strip()


def _looks_like_python_repr(text: str) -> bool:
    """文本里是否残留 Python 字面量痕迹(如 ``{'meeting_type': '周中对齐会'}``)。"""
    head = text[:400]
    return any(marker in head for marker in _PYTHON_REPR_MARKERS)


def _raw_analysis_value(value: dict[str, Any], key: str) -> Any:
    """取某字段的原始值: 先看规范键, 再看历史别名字段。"""
    if key in value:
        return value.get(key)
    for alias in _ANALYSIS_KEY_ALIASES.get(key, ()):
        if alias in value:
            return value.get(alias)
    return None


def _non_string_value_keys(value: Any) -> list[str]:
    """三个正文字段里被写成非字符串(对象/数组/数字)的键; 空表示契约成立。"""
    if not isinstance(value, dict):
        return list(_ANALYSIS_TEXT_KEYS)
    invalid: list[str] = []
    for key in _ANALYSIS_TEXT_KEYS:
        item = _raw_analysis_value(value, key)
        if item is None or isinstance(item, str):
            continue
        invalid.append(key)
    return invalid


def _degrade_python_repr(analysis: dict[str, str], issues: list[str] | None = None) -> dict[str, str]:
    """摘要/概览残留 Python 字面量痕迹时, 降级用 analysis_text 并标记(不阻塞投递)。

    展平已经能兜住模型把值写成对象的情况; 这条是第三层防御 —— 只要最终文本里还留着
    repr 特征(老 state、模型把 repr 写进字符串等), 就换成完整分析正文, 并记一条
    issues 供 run_metrics / 告警使用, 不再"静默发出看不懂的东西"。
    """
    marker = str(analysis.get("analysis_text") or "").strip()
    for key in ("meeting_summary", "positive_negative_overview"):
        if not _looks_like_python_repr(str(analysis.get(key) or "")):
            continue
        if issues is not None:
            issues.append(f"{key}_downgraded_to_analysis_text")
        if marker:
            analysis[key] = marker
    return analysis


def _normalize_analysis(value: dict[str, Any]) -> dict[str, str]:
    analysis = _flatten_value(_raw_analysis_value(value, "analysis_text"))
    summary = _flatten_value(_raw_analysis_value(value, "meeting_summary"))
    overview = _flatten_value(_raw_analysis_value(value, "positive_negative_overview"))
    if not analysis:
        analysis = "\n\n".join(part for part in (summary, overview) if part).strip()
    if not summary:
        summary = analysis
    if not overview:
        overview = analysis
    return {
        "analysis_text": analysis or _EMPTY_ANALYSIS_MARKER,
        "meeting_summary": summary,
        "positive_negative_overview": overview,
    }


async def _stream_ai_json(
    ai_client: AiClient,
    *,
    system_prompt: str,
    user_content: str,
) -> dict[str, Any]:
    request = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
        "temperature": ANALYSIS_TEMPERATURE,
        # Route analysis turns into the scheduler Session that fired this tool
        # (any fixed org session id), never into a personal conversation.
        "routing": {"session_id": get_session_id()},
    }
    chunks: list[str] = []
    reasoning_chars = 0
    finish_reason: str | None = None
    async for delta in ai_client.stream(request):
        if delta.content:
            chunks.append(delta.content)
        if delta.reasoning:
            reasoning_chars += len(delta.reasoning)
        if delta.finish_reason:
            finish_reason = delta.finish_reason
    value = _extract_json("".join(chunks))
    if isinstance(value, dict):
        # 诊断块(非分析字段, 消费方须在拼装前剥离): 记录本调用的流统计, 让
        # 「空分析」事故能区分 content 空回 / 只思考无正文 / 正常但字段空。
        value["_ai_stream"] = {
            "content_chars": sum(len(chunk) for chunk in chunks),
            "reasoning_chars": reasoning_chars,
            "finish_reason": finish_reason or "",
        }
    return value


def _analysis_system_prompt(job: MeetingJob | None = None) -> str:
    """Per-job system prompt.  Rules text is injected in the user content so a
    changed snapshot never goes unnoticed; this side states only identity,
    evidence hierarchy and output contract."""

    if job is not None:
        title = job.title
        name = job.name
    else:
        title = "周中对齐会"
        name = DAILY_MEETING_NAME
    return (
        f"你是 HaiTun 的 {title} 分析器 (任务 {name})。只分析白名单固定会议, "
        "不写入正式正负面总表, 不计分, 不进入绩效。"
        "必须以原始转写为主要证据, 智能纪要只能辅助。输出严格 JSON, 键为:"
        "analysis_text、meeting_summary、positive_negative_overview。"
        "这三个键的值都必须是自然语言字符串(可含换行与 Markdown 列表), 不得写成对象、"
        "数组或数字; 需要结构化明细(会议类型、参会人、逐条候选等)时一律写进 analysis_text "
        "的正文里, 不要塞进 meeting_summary / positive_negative_overview。"
        "正负面判断必须区分事实、证据缺口和推断; 证据不足写待补充证据, 不要臆测。"
        "负面候选必须给出正确做法、立即补救和预防措施; 同时判断会议是否符合其会议 SOP。"
    )


def _meeting_meta(job: MeetingJob | None, chunk_index: int | None = None, chunk_total: int | None = None) -> str:
    """会议身份元数据: 让每段分析都可归属到具体场次/日期, 不靠模型猜。"""
    now = datetime.now().astimezone()
    part = f" | 分块 {chunk_index}/{chunk_total}" if chunk_index is not None and chunk_total else ""
    if job is None:
        return f"会议元数据: 未知会议 | 日期={now.date().isoformat()} | 时刻={now.strftime('%H:%M:%S %z')}{part}"
    return (
        f"会议元数据: 会议名={job.name} | 会议标题={job.title}"
        f" | 会议号={job.meeting_code} | 日期={now.date().isoformat()}"
        f" | 时刻={now.strftime('%H:%M:%S %z')}{part}"
    )


def _flat_rule_text(value: Any) -> str:
    """多行口径正文折叠成单行, 保证注入清单每条目一行可审计。"""
    return " ".join(str(value or "").split())


def _render_sop_checklist(config: dict[str, Any]) -> str:
    """把已定稿 (rules[].active=true) 的会议 SOP 业务条目渲染成「生效判定清单」。

    生效范围由代码按 YAML 渲染, 模型无需自行解析注释; 无效配置在此显式抛错:
    重复规则 id、axis 不在 observation_axes、生效规则判定标准为空。
    """
    meta_version = str(config.get("meta", {}).get("version") or "?")
    axes = {
        str(axis.get("id")): str(axis.get("title") or "")
        for axis in config.get("observation_axes") or []
        if isinstance(axis, dict)
    }
    seen: dict[str, int] = {}
    active_rules: list[dict[str, Any]] = []
    inactive_rules: list[dict[str, Any]] = []
    for rule in config.get("rules") or []:
        if not isinstance(rule, dict) or not str(rule.get("id") or "").strip():
            raise RuntimeError(f"会议 SOP 配置不符合契约(rule 需含非空 id): {MEETING_SOP_CONFIG_PATH}")
        rule_id = str(rule["id"])
        seen[rule_id] = seen.get(rule_id, 0) + 1
        if seen[rule_id] > 1:
            raise RuntimeError(f"会议 SOP 配置不符合契约(规则 id 重复: {rule_id}): {MEETING_SOP_CONFIG_PATH}")
        if rule.get("axis") not in axes:
            raise RuntimeError(
                f"会议 SOP 配置不符合契约(规则 {rule_id} 的 axis 不在 observation_axes): {MEETING_SOP_CONFIG_PATH}"
            )
        if rule.get("active") is True and not str(rule.get("criteria") or "").strip():
            raise RuntimeError(f"会议 SOP 配置不符合契约(生效规则 {rule_id} 判定标准为空): {MEETING_SOP_CONFIG_PATH}")
        (active_rules if rule.get("active") is True else inactive_rules).append(rule)

    def _item(rule: dict[str, Any]) -> str:
        return (
            f"- [{rule['id']}] {_flat_rule_text(rule.get('title'))}"
            f" [axis {axes.get(rule.get('axis'), '')}]\n"
            f"  判定标准: {_flat_rule_text(rule.get('criteria'))}\n"
            f"  符合示例: {_flat_rule_text(rule.get('compliant_example'))}\n"
            f"  不符合示例: {_flat_rule_text(rule.get('violation_example'))}\n"
            f"  例外/宽容边界: {_flat_rule_text(rule.get('exception'))}"
        )

    lines = [
        f"===== 生效判定清单 (口径 {meta_version}, {len(active_rules)} 条生效规则) =====",
    ]
    if active_rules:
        lines.append(
            "对以下每条生效规则逐一给出 judgment_states 四态之一(符合/部分符合/不符合/证据不足), "
            "引用规则 id 并附原文依据(时间+发言人):"
        )
        lines.extend(_item(rule) for rule in active_rules)
    else:
        lines.append("(当前无生效规则, 全部业务条目按未生效处理)")
    if inactive_rules:
        inactive_ids = "、".join(f"{rule['id']}({_flat_rule_text(rule.get('title'))})" for rule in inactive_rules)
        lines.append(f"以下条目未生效(不得判 符合/部分符合/不符合, 一律按'证据不足/待补充证据'): {inactive_ids}")
    return "\n".join(lines)


async def _load_analysis_rules(job: MeetingJob) -> tuple[str, str]:
    """读取会议 SOP 引擎/口径与正负面规则快照; 缺失或契约损坏即显式失败, 不静默降级。

    会议 SOP = SKILL 引擎纪律 (meeting-sop/*/SKILL.md) + 判定口径
    (config/meeting-sop.yaml, 与 todo-sop.yaml 同一契约模式); 口径缺失/字段结构不符
    契约时本次运行直接失败。
    """
    sop_parts: list[str] = []
    for rel in job.analysis_sop_skills:
        skill_md = SKILLS_ROOT / Path(rel) / "SKILL.md"
        if not skill_md.is_file():
            raise RuntimeError(f"会议 SOP skill 缺失: {skill_md} (检查 MeetingJob.analysis_sop_skills)")
        sop_parts.append(f"===== {rel} (引擎) =====\n{await anyio.Path(str(skill_md)).read_text(encoding='utf-8')}")
    if not MEETING_SOP_CONFIG_PATH.is_file():
        raise RuntimeError(f"会议 SOP 配置缺失: {MEETING_SOP_CONFIG_PATH}")
    config_text = await anyio.Path(str(MEETING_SOP_CONFIG_PATH)).read_text(encoding="utf-8")
    try:
        config = yaml.safe_load(config_text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"会议 SOP 配置无法解析: {MEETING_SOP_CONFIG_PATH}: {exc}") from exc
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("meta"), dict)
        or not isinstance(config.get("rules"), list)
        or not config["meta"].get("version")
    ):
        raise RuntimeError(f"会议 SOP 配置不符合契约(需 meta.version + rules): {MEETING_SOP_CONFIG_PATH}")
    sop_parts.append(f"===== config/meeting-sop.yaml (判定口径, 业务条目以 active 为准) =====\n{config_text}")
    # 生效判定清单由代码渲染: 契约校验 + 生效条目逐条列出, 与 rules[].active 严格一致。
    sop_parts.append(_render_sop_checklist(config))
    if not POSITIVE_NEGATIVE_RULES_PATH.is_file():
        raise RuntimeError(f"正负面规则快照缺失: {POSITIVE_NEGATIVE_RULES_PATH}")
    positive_rules = await anyio.Path(str(POSITIVE_NEGATIVE_RULES_PATH)).read_text(encoding="utf-8")
    return "\n\n".join(sop_parts), positive_rules


async def _analyze_meeting_transcript(
    transcript: str,
    smart_minutes: str = "",
    *,
    job: MeetingJob | None = None,
    sop_rules: str = "",
    positive_rules: str = "",
    stats: dict[str, int] | None = None,
    issues: list[str] | None = None,
) -> dict[str, str]:
    """Analyze every transcript chunk, then synthesize the chunk analyses.

    The transcript is never shortened by taking a prefix. Each bounded chunk is
    analyzed independently, and the final call receives all chunk conclusions.
    When *job* is given, every request carries meeting identity metadata plus
    the versioned SOP / positive-negative rule snapshots; *stats* (optional)
    collects ``ai_calls`` / ``ai_input_chars`` for run metrics; *issues* (optional)
    collects output-contract problems (值写成对象/数组、repr 降级) for run_metrics 与告警。
    """
    ai_socket = current_tool_ai_socket()
    if not ai_socket:
        raise RuntimeError("meeting pipeline must run inside a Gateway Session with an AI backend")
    ai_client = AiClient(ai_socket)
    transcript_chunks = [
        transcript[index : index + ANALYSIS_CHUNK_CHARS] for index in range(0, len(transcript), ANALYSIS_CHUNK_CHARS)
    ] or [""]

    def _rules_block() -> str:
        parts: list[str] = []
        if sop_rules.strip():
            parts.append(f"会议 SOP 规则(版本化快照):\n{sop_rules}")
        if positive_rules.strip():
            parts.append(f"正负面分析规则(快照):\n{positive_rules}")
        return "\n\n".join(parts)

    async def _counted_call(system_prompt: str, user_content: str) -> Any:
        if stats is not None:
            stats["ai_calls"] = stats.get("ai_calls", 0) + 1
            stats["ai_input_chars"] = stats.get("ai_input_chars", 0) + len(user_content)
        result = await _stream_ai_json(ai_client, system_prompt=system_prompt, user_content=user_content)
        if stats is not None and isinstance(result, dict):
            stream_info = result.get("_ai_stream")
            if isinstance(stream_info, dict):
                content_chars = int(stream_info.get("content_chars") or 0)
                stats["ai_content_chars"] = stats.get("ai_content_chars", 0) + content_chars
                stats["ai_reasoning_chars"] = stats.get("ai_reasoning_chars", 0) + int(
                    stream_info.get("reasoning_chars") or 0
                )
                if content_chars == 0:
                    stats["ai_empty_calls"] = stats.get("ai_empty_calls", 0) + 1
        return result

    #: 更正重试时追加的说明: 只针对「值不是字符串」这一条契约, 不动其它判定纪律。
    retry_note = (
        "\n\n上一次输出不合格: 键 {keys} 的值不是自然语言字符串。请重新输出完整 JSON, "
        "这三个键的值都必须是字符串(可含换行与 Markdown 列表), 结构化明细只能写进 "
        "analysis_text 的正文里。"
    )

    async def _ensure_string_contract(raw: Any, user_content: str) -> dict[str, Any]:
        """校验三个正文字段是不是字符串; 不合格时纠正重试一次。

        仍不合格不再重试(避免无限循环): 交给 _normalize_analysis 的展平与
        _degrade_python_repr 的降级兜底, 并把问题写进 *issues* 供告警。
        """
        value = raw if isinstance(raw, dict) else {}
        offending = _non_string_value_keys(raw)
        if not offending:
            return value
        if issues is not None:
            issues.append("analysis_value_not_string:" + ",".join(offending))
        retried = await _counted_call(
            _analysis_system_prompt(job),
            user_content=user_content + retry_note.format(keys="、".join(offending)),
        )
        if isinstance(retried, dict):
            retried.pop("_ai_stream", None)
        if not _non_string_value_keys(retried):
            if issues is not None:
                issues.append("analysis_value_recovered_after_retry")
            return retried if isinstance(retried, dict) else value
        if issues is not None:
            still = _non_string_value_keys(retried)
            issues.append("analysis_value_still_not_string:" + ",".join(still))
        return retried if isinstance(retried, dict) else value

    partials: list[dict[str, Any]] = []
    chunk_user_contents: list[str] = []
    for index, transcript_chunk in enumerate(transcript_chunks, start=1):
        meta = _meeting_meta(job, index, len(transcript_chunks)) if job is not None else ""
        guidance = (f"{meta}\n\n" if meta else "") + (
            f"这是该场原始转写的第 {index}/{len(transcript_chunks)} 段。"
            "只依据本段中明确出现的内容记录事实; 跨段无法确认的内容标记待补充证据。"
        )
        rules = _rules_block()
        user_content = f"{guidance}\n\n原始转写片段:\n{transcript_chunk}"
        if rules:
            user_content = f"{guidance}\n\n{rules}\n\n原始转写片段:\n{transcript_chunk}"
        partial = await _counted_call(_analysis_system_prompt(job), user_content)
        if isinstance(partial, dict):
            # 诊断块只服务 run_metrics, 不进各段分析(避免污染 synthesis 输入)。
            partial.pop("_ai_stream", None)
        partials.append(partial)
        chunk_user_contents.append(user_content)
    if len(partials) == 1:
        raw = await _ensure_string_contract(partials[0], chunk_user_contents[0])
        return _degrade_python_repr(_normalize_analysis(raw), issues)
    meta = _meeting_meta(job) if job is not None else ""
    synthesis_content = (f"{meta}\n\n" if meta else "") + (
        "下面是同一场会议原始转写各段的独立分析。请合并去重并只保留有证据的结论, "
        "不能遗漏任何片段中的行为事实; 无法互相印证的内容明确标记待补充证据。"
        "输出完整的会议分析 JSON。\n\n"
        f"智能纪要(仅供参考):\n{smart_minutes}\n\n"
        f"各段分析:\n{json.dumps(partials, ensure_ascii=False)}"
    )
    synthesis = await _counted_call(_analysis_system_prompt(job), user_content=synthesis_content)
    raw = await _ensure_string_contract(synthesis, synthesis_content)
    return _degrade_python_repr(_normalize_analysis(raw), issues)


async def _read_full_transcript(base: str, meeting_name: str, chunk_count: int) -> tuple[str, list[int]]:
    chunks: list[str] = []
    indexes: list[int] = []
    index = 0
    visited: set[int] = set()
    # The manifest is only a starting estimate. Follow the reader's cursor so
    # a stale count cannot silently drop the tail of a long transcript.
    while index not in visited:
        visited.add(index)
        result = json.loads(
            await meeting_session_read(
                meeting_name=meeting_name,
                artifact="transcript",
                chunk_index=index,
                appdata_root=base,
            )
        )
        if not result.get("ok"):
            raise RuntimeError(f"transcript chunk {index} unavailable: {result.get('status', 'unknown')}")
        chunks.append(str(result.get("content") or ""))
        indexes.append(index)
        if not result.get("has_more"):
            break
        next_index = result.get("next_chunk_index")
        try:
            candidate = int(next_index) if next_index is not None else index + 1
        except TypeError, ValueError:
            candidate = index + 1
        if candidate <= index:
            candidate = index + 1
        index = candidate
    return "".join(chunks), indexes


async def _append_run_metrics(base: str, meeting_name: str, entry: dict[str, Any]) -> None:
    """Append one JSON line per run to ``run_metrics.jsonl`` (观察期指标)。"""
    path = meeting_artifact_root(base, meeting_name) / "run_metrics.jsonl"
    line = json.dumps(entry, ensure_ascii=False)
    # 指标写失败绝不掩盖主结果
    with suppress(Exception):
        lock = await path_lock(path)
        file_handle = await anyio.open_file(str(path), "a", encoding="utf-8")
        async with lock, file_handle:
            await file_handle.write(line + "\n")


async def _notify_failure_alert(
    job: MeetingJob,
    base: str,
    meeting_name: str,
    *,
    record_file_id: str,
    status: str,
    error: str,
) -> None:
    """向 ``alert_recipients`` 发一条失败告警 (幂等: 同一 record/同一天只发一次)。

    回执键按 record_file_id; 采集阶段失败拿不到录制时用日期伪键, 保证每天至多一条
    同状态告警, 不会刷屏。告警发送失败只吞掉, 主结果不受影响。
    """
    recipients = job.alert_recipients
    if not recipients or not base:
        return
    title = job.title
    date = datetime.now().astimezone().date().isoformat()
    key_record = record_file_id or f"__alert_no_record__{meeting_name}__{date}"
    text = (
        f"{ALERT_MESSAGE_PREFIX} {title}({meeting_name}) {status}"
        f"{f' | record={record_file_id}' if record_file_id else ''}"
        f" | {date}\n{error[:ALERT_ERROR_TRUNCATE_CHARS]}"
    )
    for recipient in recipients:
        # 告警失败不得影响主结果
        with suppress(Exception):
            await meeting_session_notify(
                meeting_name=meeting_name,
                recipient=recipient,
                text=text,
                record_file_id=key_record,
                appdata_root=base,
            )


async def meeting_pipeline_run(
    meeting_name: str = DAILY_MEETING_NAME,
    meeting_code: str = DAILY_MEETING_CODE,
    appdata_root: str = "",
) -> str:
    """获取日会完整转写, 分析并向固定收件人发送结果; 不写正式清单。"""
    try:
        meeting_job = meeting_job_for(meeting_name, meeting_code)
    except ValueError:
        return json.dumps({"ok": False, "status": "daily_meeting_only"}, ensure_ascii=False)
    started = time.perf_counter()

    def _ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    async def _record(status: str, *, record_file_id: str = "", entry: dict[str, Any] | None = None) -> None:
        row = {
            "ts": datetime.now().astimezone().isoformat(),
            "meeting_name": meeting_name,
            "meeting_code": meeting_code,
            "status": status,
            "record_file_id": record_file_id,
            "stages_ms": entry.pop("stages_ms", {}) if entry else {},
            **(entry or {}),
        }
        # 指标写失败不影响主结果
        with suppress(Exception):
            await _append_run_metrics(base, meeting_name, row)

    try:
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        state_path = artifact / "pipeline_state.json"
        state: dict[str, Any] = _json_file(state_path)
        prepare_started = time.perf_counter()
        prepare_result = json.loads(
            await meeting_transcript_prepare(
                meeting_code=meeting_code,
                meeting_name=meeting_name,
                appdata_root=base,
            )
        )
        prepare_ms = int((time.perf_counter() - prepare_started) * 1000)
        if not prepare_result.get("ok"):
            error_text = str(prepare_result.get("error", "Tencent transcript preparation failed"))
            await _write_json(state_path, {"status": "transcript_prepare_failed", "prepare": prepare_result})
            await _record(
                "transcript_prepare_failed", record_file_id="", entry={"error": error_text[:ALERT_ERROR_TRUNCATE_CHARS]}
            )
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id="",
                status="transcript_prepare_failed",
                error=error_text,
            )
            return json.dumps(
                {"ok": False, "status": "transcript_prepare_failed", "error": error_text},
                ensure_ascii=False,
            )
        manifest = read_meeting_manifest(base, meeting_name)
        record_file_id = str(
            prepare_result.get("record_file_id") or manifest.get("record_file_id") or state.get("record_file_id") or ""
        )
        if not record_file_id:
            await _write_json(state_path, {"status": "transcript_pending", "prepare": prepare_result})
            await _record("transcript_pending", record_file_id="")
            return json.dumps({"ok": True, "status": "transcript_pending"}, ensure_ascii=False)

        analysis_stats: dict[str, int] = {}
        analysis_issues: list[str] = []
        analysis_new = False
        if state.get("record_file_id") == record_file_id and state.get("analysis_text"):
            analysis = _degrade_python_repr(_normalize_analysis(state), analysis_issues)
        else:
            read_started = time.perf_counter()
            transcript, source_chunks = await _read_full_transcript(
                base, meeting_name, int(manifest.get("chunk_count") or 1)
            )
            read_ms = int((time.perf_counter() - read_started) * 1000)
            smart_minutes = ""
            smart_path = artifact / "smart_minutes.json"
            smart_path_anyio = anyio.Path(str(smart_path))
            if await smart_path_anyio.is_file():
                smart_minutes = await smart_path_anyio.read_text(encoding="utf-8")
            analyze_started = time.perf_counter()
            sop_rules, positive_rules = await _load_analysis_rules(meeting_job)
            analysis = await _analyze_meeting_transcript(
                transcript,
                smart_minutes,
                job=meeting_job,
                sop_rules=sop_rules,
                positive_rules=positive_rules,
                stats=analysis_stats,
                issues=analysis_issues,
            )
            analyze_ms = int((time.perf_counter() - analyze_started) * 1000)
            analysis_new = True
            if not _analysis_has_content(analysis):
                # 模型未产出任何正文(全调用 content 空或字段空)。不向收件人投递
                # 占位消息, 落 analysis_empty 终态与 AI 流统计供复盘; state 不保留
                # 可复用文本, 同一 record 下次触发会重新分析。
                await _write_json(
                    state_path,
                    {
                        "record_file_id": record_file_id,
                        "status": "analysis_empty",
                        "source_chunks": source_chunks,
                        "analysis_text": "",
                        "meeting_summary": "",
                        "positive_negative_overview": "",
                    },
                )
                await _record(
                    "analysis_empty",
                    record_file_id=record_file_id,
                    entry={
                        "stages_ms": {
                            "prepare": prepare_ms,
                            "read": read_ms,
                            "analyze": analyze_ms,
                            "notify": None,
                            "total": _ms(),
                        },
                        "analysis_reused": False,
                        "analysis": _analysis_stats_entry(analysis_stats),
                        "transcript_chars": int(manifest.get("transcript_chars") or 0),
                        "paragraph_count": int(manifest.get("paragraph_count") or 0),
                        "chunk_count": int(manifest.get("chunk_count") or 0),
                        "analysis_empty": True,
                    },
                )
                # 空分析也算一轮完整运行: 状态(analysis_empty)入档供复盘。
                await archive_meeting_record(base, meeting_name, record_file_id)
                return json.dumps(
                    {
                        "ok": False,
                        "status": "analysis_empty",
                        "error": "模型未产出会议分析正文(全部 AI 调用 content 为空), 未发送任何消息; "
                        "详见 run_metrics 的 ai_content_chars/ai_reasoning_chars/ai_empty_calls",
                    },
                    ensure_ascii=False,
                )
            state = {
                "record_file_id": record_file_id,
                "status": "notifications_pending",
                "source_chunks": source_chunks,
                **analysis,
            }
            if analysis_issues:
                # 输出契约问题随 state 入档: 复盘时能看到这一场是"模型写歪了"还是"真没内容"。
                state["analysis_issues"] = list(analysis_issues)
            await _write_json(state_path, state)

        source_chunks = [int(value) for value in state.get("source_chunks", []) if str(value).isdigit()]
        if analysis_new:
            await meeting_session_write(
                meeting_name=meeting_name,
                meeting_code=meeting_code,
                record_file_id=record_file_id,
                analysis_text=analysis["analysis_text"],
                source_chunks=",".join(str(value) for value in source_chunks),
                recipient_receipts_json=json.dumps(state.get("recipient_receipts", {}), ensure_ascii=False),
                status="notifications_pending",
                appdata_root=base,
            )
        raw_receipts = state.get("recipient_receipts")
        receipts: dict[str, Any] = dict(raw_receipts) if isinstance(raw_receipts, dict) else {}
        notify_started = time.perf_counter()
        notifications_ok = True
        # 卡片优先: 每收件人一张会议总结卡(summary/analysis/overview 合成一张,
        # 收件人 = summary + overview 名单去重)。渲染失败(模板缺失/数据异常)
        # 回落文本双名单, 与历史行为一致。
        card_render = render_meeting_summary_card(
            meeting_title=meeting_job.title,
            meeting_code=meeting_code,
            meeting_date=datetime.now().astimezone().date().isoformat(),
            analysis=analysis,
        )
        if card_render.get("ok") and isinstance(card_render.get("card"), dict):
            card_json = json.dumps(card_render["card"], ensure_ascii=False)
            card_recipients: list[str] = []
            for candidate in (*meeting_job.summary_recipients, *meeting_job.overview_recipients):
                if candidate not in card_recipients:
                    card_recipients.append(candidate)
            for recipient in card_recipients:
                receipt = json.loads(
                    await notify_meeting_card(
                        meeting_name=meeting_name,
                        recipient=recipient,
                        card_json=card_json,
                        record_file_id=record_file_id,
                        appdata_root=base,
                    )
                )
                receipts[recipient] = receipt
                notifications_ok = notifications_ok and bool(receipt.get("ok"))
        else:
            notification_targets = [
                (recipient, analysis["meeting_summary"]) for recipient in meeting_job.summary_recipients
            ] + [(recipient, analysis["positive_negative_overview"]) for recipient in meeting_job.overview_recipients]
            for recipient, text in notification_targets:
                if not text.strip():
                    text = analysis["analysis_text"]
                receipt = json.loads(
                    await meeting_session_notify(
                        meeting_name=meeting_name,
                        recipient=recipient,
                        text=text,
                        record_file_id=record_file_id,
                        appdata_root=base,
                    )
                )
                receipts[recipient] = receipt
                notifications_ok = notifications_ok and bool(receipt.get("ok"))
        notify_ms = int((time.perf_counter() - notify_started) * 1000)
        final_status = "completed" if notifications_ok else "notifications_pending"
        state.update({"status": final_status, "recipient_receipts": receipts})
        await _write_json(state_path, state)
        await meeting_session_write(
            meeting_name=meeting_name,
            meeting_code=meeting_code,
            record_file_id=record_file_id,
            analysis_text=analysis["analysis_text"],
            source_chunks=",".join(str(value) for value in source_chunks),
            recipient_receipts_json=json.dumps(receipts, ensure_ascii=False),
            status=final_status,
            appdata_root=base,
        )
        entry = {
            "stages_ms": {
                "prepare": prepare_ms,
                "read": read_ms if analysis_new else None,
                "analyze": analyze_ms if analysis_new else None,
                "notify": notify_ms,
                "total": _ms(),
            },
            "analysis_reused": not analysis_new,
            "analysis": _analysis_stats_entry(analysis_stats),
            "transcript_chars": int(manifest.get("transcript_chars") or 0),
            "paragraph_count": int(manifest.get("paragraph_count") or 0),
            "chunk_count": int(manifest.get("chunk_count") or 0),
            "notifications": {
                name: {"ok": bool(receipt.get("ok")), "status": receipt.get("status", "")}
                for name, receipt in receipts.items()
            },
        }
        if analysis_issues:
            entry["analysis_issues"] = list(analysis_issues)
        await _record(final_status, record_file_id=record_file_id, entry=entry)
        # 每轮收尾把该场全套产物(正文/纪要/分析/状态/收据)补进永久档。
        await archive_meeting_record(base, meeting_name, record_file_id)
        if not notifications_ok:
            failures = {
                name: str(r.get("status") or r.get("error") or "failed")
                for name, r in receipts.items()
                if not r.get("ok")
            }
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id=record_file_id,
                status=final_status,
                error=f"通知失败收件人: {json.dumps(failures, ensure_ascii=False)}",
            )
        if analysis_issues:
            # 输出契约问题不再静默: 展平/降级后照常投递, 但同时告警 alert_recipients
            # (同一 record + 同一天至多一条), 让人能主动去核这一场的分析质量。
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id=record_file_id,
                status="analysis_format_degraded",
                error="分析输出契约问题(已展平/降级后仍投递): " + "; ".join(analysis_issues),
            )
        return json.dumps(
            {
                "ok": True,
                "status": final_status,
                "meeting_name": meeting_name,
                "record_file_id": record_file_id,
                **({"analysis_issues": analysis_issues} if analysis_issues else {}),
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        # 兜底记录/告警失败不影响返回
        with suppress(Exception):
            await _record(
                "meeting_pipeline_failed", record_file_id="", entry={"error": error_text[:ALERT_ERROR_TRUNCATE_CHARS]}
            )
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id="",
                status="meeting_pipeline_failed",
                error=error_text,
            )
        return json.dumps(
            {"ok": False, "status": "meeting_pipeline_failed", "error": error_text},
            ensure_ascii=False,
        )


__all__ = ["meeting_pipeline_run"]
