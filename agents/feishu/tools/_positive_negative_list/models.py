"""Typed case-draft model for positive-negative coaching workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_ALLOWED_EVIDENCE = frozenset({"当事人陈述", "他人陈述", "链接", "截图", "任务记录", "聊天记录", "其他"})
_FORBIDDEN_KEYS = frozenset(
    {
        "score",
        "ranking",
        "rank",
        "performance",
        "penalty",
        "pip",
        "termination",
        "dismissal",
        "points",
        "评分",
        "排名",
        "绩效",
        "处罚",
        "解除",
    }
)


def _safe_fields(fields_map: Mapping[str, Any]) -> dict[str, Any]:
    """Keep performance-related columns out of read/analyze payloads."""
    safe: dict[str, Any] = {}
    for key, value in fields_map.items():
        normalized = str(key).casefold()
        if any(token in normalized or token in str(key) for token in _FORBIDDEN_KEYS):
            continue
        safe[key] = value
    return safe


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return ""


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "case_id": ("case_id", "记录ID", "案件ID", "记录编号", "编号", "序号"),
    "reporter_user_key": ("reporter_user_key", "报告人", "记录人", "登记人"),
    "subject_user_key": ("subject_user_key", "涉事人", "涉事人员", "当事人", "员工姓名", "員工姓名"),
    "occurred_at": ("occurred_at", "发生时间", "日期", "发生日期", "记录日期"),
    "nature": ("nature", "behavior_nature", "行为性质", "正负面归属", "性质", "类型"),
    "category": ("category", "分类", "行为分类", "行为类别"),
    "fact_summary": ("fact_summary", "行为事实", "事实摘要", "事件描述", "描述", "行为描述", "事项"),
    "evidence_sources": ("evidence_sources", "证据来源", "证据", "来源"),
    "correct_behavior": ("correct_behavior", "正确做法"),
    "immediate_remedy": ("immediate_remedy", "立即补救"),
    "prevention": ("prevention", "预防措施", "防再犯"),
    "review_status": ("review_status", "复盘状态", "复盘"),
    "source_key": ("source_key",),
    "canonical_incident_id": ("canonical_incident_id",),
    "cross_source_fingerprint": ("cross_source_fingerprint",),
    "record_link": ("record_link", "记录链接"),
    "observed_behavior": ("observed_behavior", "观察到的行为", "行为"),
    "context": ("context", "场合/背景", "场合", "背景"),
    "impact": ("impact", "影响"),
}


def _field_candidates(semantic: str, field_names: Mapping[str, str] | None) -> tuple[str, ...]:
    configured = str((field_names or {}).get(semantic) or "").strip()
    return tuple(dict.fromkeys(name for name in (configured, *_FIELD_ALIASES.get(semantic, ())) if name))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    # Feishu text columns are commonly returned as a list of rich-text
    # fragments, e.g. [{"text": "...", "type": "text"}].  Normalize
    # those fragments here so the read/analyze/remind path sees the same
    # human-readable value as the table UI.  This remains deliberately
    # conservative: arbitrary mappings are not stringified wholesale.
    if isinstance(value, Mapping):
        for key in ("text", "value", "content", "name"):
            if key in value:
                text = _text(value[key])
                if text:
                    return text
        return ""
    if isinstance(value, (list, tuple)):
        return "".join(part for item in value if (part := _text(item)))
    return ""


def _date_text(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value) / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat()
        except OverflowError, OSError, ValueError:
            return str(value)
    return _text(value)


def _person(value: Any) -> str:
    """Normalize Feishu person fields and plain text identities for read paths."""
    if isinstance(value, Mapping):
        for key in ("open_id", "user_id", "id", "name"):
            candidate = _text(value.get(key))
            if candidate:
                return candidate
        return ""
    if isinstance(value, (list, tuple)):
        identities = [_person(item) for item in value]
        return ",".join(identity for identity in identities if identity)
    return _text(value)


def _nature(value: Any) -> str:
    normalized = _text(value).strip().casefold()
    return {
        "正面清单": "positive",
        "正面": "positive",
        "positive": "positive",
        "负面清单": "negative",
        "负面": "negative",
        "negative": "negative",
        "中性": "neutral",
        "neutral": "neutral",
        "证据不足": "insufficient_evidence",
        "insufficient_evidence": "insufficient_evidence",
    }.get(normalized, _text(value))


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(item for item in (_text(item) for item in value) if item)
    return ()


@dataclass(frozen=True)
class LedgerQuery:
    record_id: str = ""
    subject_user_key: str = ""
    reporter_user_key: str = ""
    nature: str = ""
    category: str = ""
    keyword: str = ""
    occurred_from: str = ""
    occurred_to: str = ""
    view_id: str = ""
    page_size: int = 100
    page_token: str = ""


@dataclass(frozen=True)
class LedgerRecord:
    record_id: str
    case_id: str
    reporter_user_key: str
    subject_user_key: str
    occurred_at: str
    nature: str
    category: str
    fact_summary: str
    evidence_sources: tuple[str, ...]
    correct_behavior: str
    immediate_remedy: str
    prevention: str
    review_status: str
    source_key: str
    canonical_incident_id: str
    cross_source_fingerprint: str
    fields: Mapping[str, Any]
    record_link: str = ""
    observed_behavior: str = ""
    context: str = ""
    impact: str = ""

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], *, field_names: Mapping[str, str] | None = None) -> LedgerRecord:
        if not isinstance(mapping, Mapping):
            raise TypeError("ledger record must be a mapping")
        raw_fields = mapping.get("fields", mapping)
        fields_map = _safe_fields(dict(raw_fields)) if isinstance(raw_fields, Mapping) else {}
        if raw_fields is not mapping:
            # Normalized records carry canonical values at the top level while
            # retaining the original Feishu fields for traceability.  Merge
            # both forms so a later analyze/remind/review call remains stable
            # even when the deployed table uses custom column names.
            top_level = {key: value for key, value in mapping.items() if key != "fields"}
            fields_map = {**fields_map, **_safe_fields(top_level)}
        return cls(
            record_id=_text(mapping.get("record_id") or mapping.get("id") or mapping.get("记录编号")),
            case_id=_text(_first(fields_map, *_field_candidates("case_id", field_names))),
            reporter_user_key=_person(_first(fields_map, *_field_candidates("reporter_user_key", field_names))),
            subject_user_key=_person(_first(fields_map, *_field_candidates("subject_user_key", field_names))),
            occurred_at=_date_text(_first(fields_map, *_field_candidates("occurred_at", field_names))),
            nature=_nature(_first(fields_map, *_field_candidates("nature", field_names))),
            category=_text(_first(fields_map, *_field_candidates("category", field_names))),
            fact_summary=_text(_first(fields_map, *_field_candidates("fact_summary", field_names))),
            evidence_sources=_strings(_first(fields_map, *_field_candidates("evidence_sources", field_names))),
            correct_behavior=_text(_first(fields_map, *_field_candidates("correct_behavior", field_names))),
            immediate_remedy=_text(_first(fields_map, *_field_candidates("immediate_remedy", field_names))),
            prevention=_text(_first(fields_map, *_field_candidates("prevention", field_names))),
            review_status=_text(_first(fields_map, *_field_candidates("review_status", field_names))),
            source_key=_text(_first(fields_map, *_field_candidates("source_key", field_names))),
            canonical_incident_id=_text(_first(fields_map, *_field_candidates("canonical_incident_id", field_names))),
            cross_source_fingerprint=_text(
                _first(fields_map, *_field_candidates("cross_source_fingerprint", field_names))
            ),
            record_link=_text(
                mapping.get("record_link") or _first(fields_map, *_field_candidates("record_link", field_names))
            ),
            fields=fields_map,
            observed_behavior=_text(_first(fields_map, *_field_candidates("observed_behavior", field_names))),
            context=_text(_first(fields_map, *_field_candidates("context", field_names))),
            impact=_text(_first(fields_map, *_field_candidates("impact", field_names))),
        )

    def to_mapping(self) -> dict[str, Any]:
        # Read/analyze/remind/review callers only need normalized business
        # values.  Returning the raw Feishu ``fields`` payload here can carry
        # verbose person objects (avatar URL, email, display metadata) for
        # every row, making a multi-page read large enough to be interrupted by
        # the upstream model.  Keep raw fields available internally on the
        # dataclass, but do not serialize them into tool output.
        return {
            "record_id": self.record_id,
            "case_id": self.case_id,
            "reporter_user_key": self.reporter_user_key,
            "subject_user_key": self.subject_user_key,
            "occurred_at": self.occurred_at,
            "nature": self.nature,
            "category": self.category,
            "fact_summary": self.fact_summary,
            "evidence_sources": list(self.evidence_sources),
            "correct_behavior": self.correct_behavior,
            "immediate_remedy": self.immediate_remedy,
            "prevention": self.prevention,
            "review_status": self.review_status,
            "source_key": self.source_key,
            "canonical_incident_id": self.canonical_incident_id,
            "cross_source_fingerprint": self.cross_source_fingerprint,
            "record_link": self.record_link,
            "observed_behavior": self.observed_behavior,
            "context": self.context,
            "impact": self.impact,
        }


@dataclass(frozen=True)
class CaseDraft:
    writer_user_key: str
    reporter_user_key: str
    subject_user_key: str
    occurred_at: str
    observed_behavior: str
    context: str
    impact: str
    evidence_sources: tuple[str, ...]
    nature: str
    category: str
    primary_rule_id: str | None
    secondary_rule_ids: tuple[str, ...]
    rule_version: str
    fact_summary: str
    agent_inference: str
    correct_behavior: str
    immediate_remedy: str
    prevention: str
    workflow: str = "draft"
    red_line_candidate: bool = False
    source_key: str = ""
    canonical_incident_id: str = ""
    cross_source_fingerprint: str = ""
    case_id: str = ""
    source_type: str = "feishu_private_chat"
    source_event_id: str = ""
    source_message_id: str = ""
    source_session_id: str = ""
    record_link: str = ""

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> CaseDraft:
        if not isinstance(mapping, Mapping):
            raise TypeError("case draft must be a mapping")
        unknown = set(mapping) - {field.name for field in fields(cls)}
        forbidden = {
            str(key).casefold() for key in unknown if any(token in str(key).casefold() for token in _FORBIDDEN_KEYS)
        }
        if forbidden:
            raise ValueError("forbidden performance fields")
        if unknown:
            raise ValueError(f"unknown case fields: {', '.join(sorted(map(str, unknown)))}")
        values = dict(mapping)
        for name in (
            "writer_user_key",
            "reporter_user_key",
            "subject_user_key",
            "occurred_at",
            "observed_behavior",
            "context",
            "impact",
            "nature",
            "category",
            "rule_version",
            "fact_summary",
            "agent_inference",
            "correct_behavior",
            "immediate_remedy",
            "prevention",
        ):
            if not isinstance(values.get(name), str):
                raise TypeError(f"{name} must be a string")
        for name in ("evidence_sources", "secondary_rule_ids"):
            raw = values.get(name, ())
            if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
                raise TypeError(f"{name} must be a string sequence")
            values[name] = tuple(raw)
        if values.get("primary_rule_id") is not None and not isinstance(values["primary_rule_id"], str):
            raise TypeError("primary_rule_id must be a string or null")
        if not isinstance(values.get("workflow", "draft"), str) or not isinstance(
            values.get("red_line_candidate", False), bool
        ):
            raise TypeError("invalid workflow fields")
        for name in ("source_key", "canonical_incident_id", "cross_source_fingerprint"):
            if not isinstance(values.get(name, ""), str):
                raise TypeError(f"{name} must be a string")
        values.setdefault("workflow", "draft")
        values.setdefault("red_line_candidate", False)
        return cls(**values)

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self) | {
            "evidence_sources": list(self.evidence_sources),
            "secondary_rule_ids": list(self.secondary_rule_ids),
        }


def allowed_evidence_sources() -> frozenset[str]:
    return _ALLOWED_EVIDENCE
