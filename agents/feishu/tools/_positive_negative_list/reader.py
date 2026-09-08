"""Read-only positive-negative ledger access through the existing Feishu client."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import _feishu_impl as _f
from _assignment_display import readable_name, render_people_display, resolve_feishu_display_names

from _positive_negative_list.models import LedgerQuery, LedgerRecord

_QUERY_FIELDS = frozenset(
    {
        "record_id",
        "subject_user_key",
        "reporter_user_key",
        "nature",
        "category",
        "keyword",
        "occurred_from",
        "occurred_to",
        "view_id",
        "page_size",
        "page_token",
    }
)

_FIELD_NAMES = {
    "record_id": "record_id",
    "case_id": "案件ID",
    "subject_user_key": "涉事人",
    "reporter_user_key": "报告人",
    "nature": "行为性质",
    "category": "分类",
    "occurred_at": "发生时间",
    "observed_behavior": "观察到的行为",
    "context": "场合/背景",
    "impact": "影响",
    "fact_summary": "行为事实",
    "evidence_sources": "证据来源",
    "correct_behavior": "正确做法",
    "immediate_remedy": "立即补救",
    "prevention": "预防措施",
    "review_status": "复盘状态",
    "source_key": "来源键",
    "canonical_incident_id": "事件键",
    "cross_source_fingerprint": "跨源指纹",
    "primary_rule_id": "主规则ID",
    "secondary_rule_ids": "辅助规则ID",
    "agent_inference": "Agent判断",
    "rule_version": "规则版本",
    "source_type": "来源类型",
    "source_event_id": "来源事件ID",
    "source_message_id": "来源消息ID",
    "source_session_id": "来源会话ID",
}

_FIELD_ALIASES = {
    "case_id": ("案件ID", "记录ID", "编号", "序号"),
    "reporter_user_key": ("报告人", "记录人", "登记人"),
    "subject_user_key": ("涉事人", "涉事人员", "当事人", "员工姓名", "員工姓名"),
    "occurred_at": ("发生时间", "日期", "发生日期", "记录日期"),
    "nature": ("行为性质", "正负面归属", "性质", "类型"),
    "category": ("分类", "行为分类", "行为类别"),
    "fact_summary": ("行为事实", "事实摘要", "事件描述", "描述", "行为描述", "事项"),
    "evidence_sources": ("证据来源", "证据", "来源"),
    "context": ("场合/背景", "场合", "背景"),
}


def _field_name_list(
    field_names: Mapping[str, str],
    configured_semantics: frozenset[str] | set[str] = frozenset(),
    *,
    strict: bool = False,
) -> list[str]:
    """Return the Feishu column names to request.

    The shared semantic map contains aliases for the richer, upgraded ledger
    schema.  The existing public table deliberately has only the original
    business columns, however, and Feishu silently returns no rows when a
    search request contains a column that is not present in that table.  A
    strict read therefore requests only the explicitly mapped columns.  This
    is used by the public-source adapter; the non-strict mode remains useful
    for configured/new tables where aliases are intentional.
    """
    if strict:
        # Keep a stable, human-facing order.  ``record_id`` is returned by the
        # API as metadata rather than a field and must not be requested.
        order = (
            "subject_user_key",
            "reporter_user_key",
            "nature",
            "occurred_at",
            "fact_summary",
            "context",
            "impact",
            "evidence_sources",
            "category",
            "observed_behavior",
            "note",
        )
        semantics = [semantic for semantic in order if semantic in field_names]
        semantics.extend(
            semantic
            for semantic in field_names
            if semantic not in semantics and semantic not in {"record_id", "case_id"}
        )
        names: list[str] = []
        for semantic in semantics:
            name = str(field_names.get(semantic) or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    names: list[str] = []
    for semantic, field_name in field_names.items():
        if semantic == "record_id":
            continue
        aliases = () if semantic in configured_semantics else _FIELD_ALIASES.get(semantic, ())
        for name in (field_name, *aliases):
            if name and name not in names:
                names.append(name)
    return names


def _config_value(name: str, explicit: str) -> str:
    return explicit.strip() or os.environ.get(name, "").strip()


def parse_query(query_json: str, *, page_size: int = 100, page_token: str = "", view_id: str = "") -> LedgerQuery:
    try:
        raw = json.loads(query_json) if query_json.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"query_json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("query_json must be a JSON object")
    unknown = set(raw) - _QUERY_FIELDS
    if unknown:
        raise ValueError(f"unknown query fields: {', '.join(sorted(map(str, unknown)))}")
    values = dict(raw)
    if "page_size" not in values:
        values["page_size"] = page_size
    if "page_token" not in values:
        values["page_token"] = page_token
    if "view_id" not in values:
        values["view_id"] = view_id
    if (
        not isinstance(values["page_size"], int)
        or isinstance(values["page_size"], bool)
        or not 1 <= values["page_size"] <= 500
    ):
        raise ValueError("page_size must be an integer from 1 to 500")
    for key, value in values.items():
        if key != "page_size" and not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
    return LedgerQuery(**values)


def _condition(field_name: str, operator: str, value: str) -> dict[str, Any]:
    return {"field_name": field_name, "operator": operator, "value": [value]}


def build_filter(query: LedgerQuery, field_names: Mapping[str, str] | None = None) -> str:
    names = {**_FIELD_NAMES, **dict(field_names or {})}
    conditions: list[dict[str, Any]] = []
    for key, field_name in (
        ("record_id", names["record_id"]),
        ("subject_user_key", names["subject_user_key"]),
        ("reporter_user_key", names["reporter_user_key"]),
        ("nature", names["nature"]),
        ("category", names["category"]),
    ):
        value = getattr(query, key)
        if value:
            conditions.append(_condition(field_name, "is", value))
    if query.keyword:
        conditions.append(_condition(names["fact_summary"], "contains", query.keyword))
    if query.occurred_from:
        conditions.append(_condition(names["occurred_at"], "isGreaterEqual", query.occurred_from))
    if query.occurred_to:
        conditions.append(_condition(names["occurred_at"], "isLessEqual", query.occurred_to))
    return json.dumps({"conjunction": "and", "conditions": conditions}, ensure_ascii=False) if conditions else ""


class FeishuLedgerClient:
    def __init__(
        self,
        app_token: str,
        table_id: str,
        field_names: Mapping[str, str] | None = None,
        *,
        strict_field_names: bool = False,
    ) -> None:
        self.app_token = app_token.strip()
        self.table_id = table_id.strip()
        self._requested_field_names = dict(field_names or {})
        self._configured_semantics = frozenset(self._requested_field_names)
        self._strict_field_names = strict_field_names
        self.field_names = {**_FIELD_NAMES, **self._requested_field_names}

    async def list_records(self, query: LedgerQuery, user_key: str) -> dict[str, Any]:
        if query.record_id:
            try:
                record = await self.get_record(query.record_id, user_key)
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "status": "record_lookup_failed",
                    "error": str(exc),
                    "records": [],
                    "has_more": False,
                    "page_token": "",
                }
            return {
                "ok": True,
                "records": [record] if record is not None else [],
                "count": 1 if record is not None else 0,
                "has_more": False,
                "page_token": "",
            }
        result = await _f.search_bitable_records_impl(
            app_token=self.app_token,
            table_id=self.table_id,
            filter_json=build_filter(query, self.field_names),
            sort_json="",
            field_names=json.dumps(
                _field_name_list(
                    self._requested_field_names if self._strict_field_names else self.field_names,
                    self._configured_semantics,
                    strict=self._strict_field_names,
                ),
                ensure_ascii=False,
            ),
            view_id=query.view_id,
            page_size=query.page_size,
            page_token=query.page_token,
            automatic_fields=True,
            user_key=user_key,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return result if isinstance(result, dict) else {"ok": False, "error": "table read failed"}
        return result

    async def get_record(self, record_id: str, user_key: str) -> Mapping[str, Any] | None:
        """Fetch a row by Feishu record ID instead of treating it as a table column."""
        result = await _f.get_bitable_record_impl(
            app_token=self.app_token,
            table_id=self.table_id,
            record_id=record_id,
            field_names=json.dumps(
                _field_name_list(
                    self._requested_field_names if self._strict_field_names else self.field_names,
                    self._configured_semantics,
                    strict=self._strict_field_names,
                ),
                ensure_ascii=False,
            ),
            user_key=user_key,
        )
        if not isinstance(result, dict):
            raise RuntimeError("table record read failed")
        if not result.get("ok"):
            message = result.get("error") or result.get("message") or "table record read failed"
            raise RuntimeError(str(message))
        record = result.get("record")
        if not isinstance(record, Mapping):
            return None
        if str(record.get("record_id") or "") != record_id:
            return None
        return record


def resolve_table_config(
    app_token: str = "",
    table_id: str = "",
    *,
    app_token_env: str = "HAITUN_PNL_APP_TOKEN",
    table_id_env: str = "HAITUN_PNL_TABLE_ID",
) -> tuple[str, str]:
    resolved_app = _config_value(app_token_env, app_token)
    resolved_table = _config_value(table_id_env, table_id)
    if not resolved_app:
        raise ValueError(f"app_token is required (set {app_token_env} or pass app_token)")
    if not resolved_table:
        raise ValueError(f"table_id is required (set {table_id_env} or pass table_id)")
    return resolved_app, resolved_table


async def read_records(client: FeishuLedgerClient, query: LedgerQuery, user_key: str) -> dict[str, Any]:
    result = await client.list_records(query, user_key)
    if not result.get("ok", True):
        return result
    raw_records = result.get("records", [])
    field_names = getattr(client, "field_names", None)
    records = [
        LedgerRecord.from_mapping(row, field_names=field_names).to_mapping()
        for row in raw_records
        if isinstance(row, Mapping)
    ]
    has_more = bool(result.get("has_more"))
    next_token = str(result.get("page_token") or "")
    if has_more and (not next_token or next_token == query.page_token):
        return {"ok": False, "error": "table pagination cursor did not advance"}
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "has_more": has_more,
        "page_token": next_token,
        "query": query.__dict__,
    }


def _public_result(
    result: Mapping[str, Any],
    display_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project an internal page into a readable response for the chat model."""
    if not result.get("ok", True):
        return {
            "ok": False,
            "状态": "读取失败",
            "说明": "暂时无法读取正负面清单，请稍后重试。",
        }
    records: list[dict[str, Any]] = []
    for raw in result.get("records", ()):
        if not isinstance(raw, Mapping):
            continue
        record = LedgerRecord.from_mapping(raw)
        nature = {
            "positive": "正面清单",
            "negative": "负面清单",
            "neutral": "未发现正负面行为",
            "insufficient_evidence": "证据不足",
        }.get(record.nature, record.nature)
        names = display_names or {}
        reporter = render_people_display(record.reporter_user_key, dict(names))
        subject = render_people_display(record.subject_user_key, dict(names))
        records.append(
            {
                "记录编号": record.record_id,
                "报告人": reporter,
                "涉事人": subject,
                "发生时间": record.occurred_at,
                "行为性质": nature,
                "分类": record.category,
                "行为事实": record.fact_summary,
                "场合/背景": record.context,
                "影响": record.impact,
                "证据状态": "已提供" if record.evidence_sources else "未填写",
                "复盘状态": (
                    "已完成" if record.review_status else ("待复盘" if record.nature == "negative" else "不适用")
                ),
                "记录链接": record.record_link,
            }
        )
    return {
        "ok": True,
        "记录": records,
        "本页记录数": len(records),
        "读取状态": "本页已读完，请继续读取下一页" if result.get("has_more") else "已读完全部记录",
    }


async def public_result_with_names(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project a page with all person fields rendered as display names."""
    if not result.get("ok", True):
        return _public_result(result)
    identities: set[str] = set()
    normalized: list[LedgerRecord] = []
    for raw in result.get("records", ()):
        if not isinstance(raw, Mapping):
            continue
        record = LedgerRecord.from_mapping(raw)
        normalized.append(record)
        identities.update(
            part.strip()
            for value in (record.reporter_user_key, record.subject_user_key)
            for part in value.replace("，", ",").split(",")
            if readable_name(part.strip()) is None
        )
    names = await resolve_feishu_display_names(identities, _f.get_users_batch_impl)
    return _public_result({**dict(result), "records": [record.to_mapping() for record in normalized]}, names)


def public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Backward-compatible projection for internal callers.

    User-facing entry points should use :func:`public_result_with_names`.
    """
    return _public_result(result)
