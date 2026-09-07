"""Configuration-driven, fail-closed public table adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, ClassVar, Protocol
from zoneinfo import ZoneInfo

from _positive_negative_list.models import CaseDraft, LedgerQuery, LedgerRecord
from _positive_negative_list.preflight import TableSchema, TableSchemaValidation


class TableClient(Protocol):
    async def preflight(self, user_key: str) -> TableSchemaValidation: ...

    async def search(self, field_id: str, value: str, user_key: str) -> Sequence[Mapping[str, Any]]: ...

    async def create(self, fields: Mapping[str, Any], user_key: str) -> Mapping[str, Any]: ...

    async def list_records(self, query: LedgerQuery, user_key: str) -> Mapping[str, Any]: ...

    async def get_record(self, record_id: str, user_key: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class WriteResult:
    status: str
    record: Mapping[str, Any] | None = None
    candidates: tuple[Mapping[str, Any], ...] = ()
    error: str = ""


class TableAdapter:
    _dedupe_locks: ClassVar[dict[tuple[int, str], asyncio.Lock]] = {}

    def __init__(self, client: TableClient) -> None:
        self._client = client
        self._schema: TableSchema | None = None

    async def preflight(self, user_key: str) -> TableSchemaValidation:
        result = await self._client.preflight(user_key)
        self._schema = result.schema if result.ok else None
        return result

    async def list_records(self, query: LedgerQuery, user_key: str) -> Mapping[str, Any]:
        result = await self._client.list_records(query, user_key)
        rows = result.get("records", ()) if isinstance(result, Mapping) else ()
        field_names = getattr(self._client, "field_names", None)
        normalized = [
            LedgerRecord.from_mapping(row, field_names=field_names).to_mapping()
            for row in rows
            if isinstance(row, Mapping)
        ]
        return {
            **dict(result),
            "records": normalized,
            "count": len(normalized),
        }

    async def get_record(self, record_id: str, user_key: str) -> LedgerRecord | None:
        if not record_id.strip():
            return None
        direct_get = getattr(self._client, "get_record", None)
        if callable(direct_get):
            row = await direct_get(record_id.strip(), user_key)
            if row is None:
                return None
            record = LedgerRecord.from_mapping(row, field_names=getattr(self._client, "field_names", None))
            return record if record.record_id == record_id.strip() else None
        result = await self.list_records(LedgerQuery(record_id=record_id.strip(), page_size=1), user_key)
        rows = result.get("records", [])
        for row in rows:
            candidate = LedgerRecord.from_mapping(row, field_names=getattr(self._client, "field_names", None))
            if candidate.record_id == record_id.strip():
                return candidate
        return None

    async def find_by_source_key(self, source_key: str, user_key: str) -> Mapping[str, Any] | None:
        schema = self._require_schema()
        rows = await self._client.search(schema.deduplication_field_ids["source_key"], source_key, user_key)
        return rows[0] if rows else None

    async def find_by_canonical_id(self, canonical_id: str, user_key: str) -> Mapping[str, Any] | None:
        schema = self._require_schema()
        rows = await self._client.search(
            schema.deduplication_field_ids["canonical_incident_id"], canonical_id, user_key
        )
        return rows[0] if rows else None

    async def find_possible_duplicates(self, case: CaseDraft, user_key: str) -> WriteResult:
        schema = self._require_schema()
        if not case.cross_source_fingerprint:
            return WriteResult("none")
        rows = await self._client.search(
            schema.deduplication_field_ids["cross_source_fingerprint"], case.cross_source_fingerprint, user_key
        )
        if not rows:
            return WriteResult("none")
        return WriteResult("possible_duplicate", candidates=tuple(rows))

    async def create_public_record(self, case: CaseDraft, user_key: str) -> Mapping[str, Any] | WriteResult:
        loop = asyncio.get_running_loop()
        key = (id(loop), case.cross_source_fingerprint)
        lock = self._dedupe_locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._create_public_record_locked(case, user_key)

    async def _create_public_record_locked(self, case: CaseDraft, user_key: str) -> Mapping[str, Any] | WriteResult:
        schema = self._require_schema()
        if not case.source_key or not case.canonical_incident_id or not case.cross_source_fingerprint:
            raise ValueError("dedupe identifiers are required before public write")
        try:
            existing = await self.find_by_source_key(case.source_key, user_key)
            if existing is not None:
                return WriteResult("exact_duplicate", record=existing)
            existing = await self.find_by_canonical_id(case.canonical_incident_id, user_key)
            if existing is not None:
                return WriteResult("exact_duplicate", record=existing)
            possible = await self.find_possible_duplicates(case, user_key)
        except Exception as exc:
            return WriteResult("dedupe_failed", error=f"{type(exc).__name__}: {exc}")
        if possible.status == "possible_duplicate":
            return possible
        field_values = {
            "case_id": case.case_id,
            "source_key": case.source_key,
            "canonical_incident_id": case.canonical_incident_id,
            "cross_source_fingerprint": case.cross_source_fingerprint,
            "source_type": case.source_type,
            "source_event_id": case.source_event_id,
            "source_message_id": case.source_message_id,
            "source_session_id": case.source_session_id,
            "observed_behavior": case.observed_behavior,
            "context": case.context,
            "impact": case.impact,
            "evidence_sources": list(case.evidence_sources),
            "primary_rule_id": case.primary_rule_id or "",
            "secondary_rule_ids": list(case.secondary_rule_ids),
            "agent_inference": case.agent_inference,
            "nature": case.nature,
            "category": case.category,
            "fact_summary": case.fact_summary,
            "correct_behavior": case.correct_behavior,
            "immediate_remedy": case.immediate_remedy,
            "prevention": case.prevention,
            "rule_version": case.rule_version,
            "reporter_user_key": case.reporter_user_key,
            "subject_user_key": case.subject_user_key,
            "occurred_at": case.occurred_at,
        }
        existing_columns_builder = getattr(self._client, "build_existing_case_fields", None)
        if callable(existing_columns_builder):
            fields = existing_columns_builder(case, schema)
        else:
            field_values = {name: _encode_field_value(name, value, schema) for name, value in field_values.items()}
            fields = {
                schema.field_ids_by_semantic_name[name]: value
                for name, value in field_values.items()
                if name in schema.field_ids_by_semantic_name
            }
        created = await self._client.create(fields, user_key)
        if isinstance(created, Mapping):
            record_id = str(created.get("record_id") or created.get("id") or "").strip()
            if record_id and "record_link" not in created:
                return {
                    **dict(created),
                    "record_link": (
                        f"https://feishu.cn/base/{schema.app_token}?table={schema.table_id}&record={record_id}"
                    ),
                }
        return created

    def _require_schema(self) -> TableSchema:
        if self._schema is None:
            raise RuntimeError("table preflight is required before access")
        return self._schema


_FIELD_TYPE_NAMES = {
    "文本": 1,
    "数字": 2,
    "单选": 3,
    "日期": 5,
    "人员": 11,
    "超链接": 15,
}


def _field_type(schema: TableSchema, semantic_name: str) -> int | None:
    field_id = schema.field_ids_by_semantic_name.get(semantic_name)
    if not field_id:
        return None
    field = schema.fields_by_id.get(field_id, {})
    value = field.get("type") if isinstance(field, Mapping) else None
    if isinstance(value, str):
        value = _FIELD_TYPE_NAMES.get(value)
    return value if isinstance(value, int) else None


def _select_value(semantic_name: str, value: Any, schema: TableSchema, field_id: str) -> Any:
    """Use the deployed select option label while retaining internal values in tests."""
    if semantic_name != "nature" or not isinstance(value, str):
        return value
    options = schema.select_options_by_field_id.get(field_id, frozenset())
    candidates = {
        "positive": ("正面清单", "正面", "positive"),
        "negative": ("负面清单", "负面", "negative"),
        "neutral": ("中性", "neutral"),
        "insufficient_evidence": ("证据不足", "insufficient_evidence"),
    }.get(value.casefold(), (value,))
    if not options and candidates:
        # Some Feishu deployments omit select-option metadata from the fields
        # response.  The robot-owned test table is created with the formal
        # Chinese labels, so keep the persisted value aligned in that case.
        return candidates[0]
    for candidate in candidates:
        if candidate in options:
            return candidate
    raise ValueError(f"nature option {value!r} is not present in the deployed table")


def _person_value(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        identities = [part.strip() for part in value.replace("\uff0c", ",").split(",") if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        identities = [str(part).strip() for part in value if str(part).strip()]
    else:
        identities = []
    if not identities or any(not (item.startswith("ou_") or item.startswith("user_")) for item in identities):
        raise ValueError("person field requires Feishu user IDs (ou_*/user_*), not display names")
    return [{"id": identity} for identity in identities]


def _date_millis(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), time.min)
        except ValueError:
            return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(parsed.timestamp() * 1000)


def _encode_field_value(semantic_name: str, value: Any, schema: TableSchema) -> Any:
    field_id = schema.field_ids_by_semantic_name.get(semantic_name, "")
    encoded = _select_value(semantic_name, value, schema, field_id)
    field_type = _field_type(schema, semantic_name)
    if field_type == 11 and encoded not in (None, ""):
        return _person_value(encoded)
    if field_type == 5 and encoded not in (None, ""):
        return _date_millis(encoded)
    return encoded
