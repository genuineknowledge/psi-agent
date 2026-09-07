"""Runtime wiring for the configured public positive-negative ledger."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import _feishu_impl as _f
import _runtime_paths as _paths
import yaml
from loguru import logger

from _positive_negative_list.preflight import TableSchemaValidation, validate_table_schema
from _positive_negative_list.reader import FeishuLedgerClient, _field_name_list, resolve_table_config
from _positive_negative_list.table import TableAdapter, TableClient, _encode_field_value

# The ledger is the existing organization base and the single production
# target for both reads and confirmed writes.  Coordinates and write-column
# labels are editable through ``config/positive-negative-list.yaml`` (todo-sop
# style: values editable, structure is a contract); the values below are the
# built-in defaults used when that file is missing, unreadable, or malformed.
# There is no robot-provisioned test table and no AppData target file; the
# write path reuses the public ledger's own six columns after a fail-closed
# preflight.
_SOURCE_APP_TOKEN = "RNEvbLIJAaPPdksfv8YceTmjndg"
_SOURCE_TABLE_ID = "tblwXV7Xlwu0hVYH"
_SOURCE_VIEW_ID = "veweChthHV"
# Tenant web domain for employee-visible record links (kept in sync with
# ``table._DEFAULT_WEB_HOST``); ``ledger.host`` in the editable config
# overrides it per deployment.
_SOURCE_WEB_HOST = "genuineknowledge.feishu.cn"
_LEDGER_FIELD_NAMES = {
    "nature": "正负面归属",
    "subject_user_key": "员工姓名",
    "fact_summary": "事件描述",
    "occurred_at": "记录日期",
    "note": "备注",
    "reporter_user_key": "填写人",
}
_SOURCE_FIELD_NAMES = {
    "case_id": "记录ID",
    "nature": "正负面归属",
    "subject_user_key": "员工姓名",
    "reporter_user_key": "填写人",
    "occurred_at": "记录日期",
    "fact_summary": "事件描述",
    "observed_behavior": "事件描述",
}
_TYPE_NAMES = {
    "文本": 1,
    "数字": 2,
    "单选": 3,
    "日期": 5,
    "人员": 11,
    "超链接": 15,
}

_REQUIRED_LEDGER_FIELDS = (
    "case_id",
    "source_key",
    "canonical_incident_id",
    "cross_source_fingerprint",
    "observed_behavior",
    "context",
    "impact",
    "evidence_sources",
    "primary_rule_id",
    "secondary_rule_ids",
    "agent_inference",
    "nature",
    "category",
    "fact_summary",
    "correct_behavior",
    "immediate_remedy",
    "prevention",
    "rule_version",
    "reporter_user_key",
    "subject_user_key",
    "occurred_at",
)


def _load_config() -> dict[str, Any]:
    # Kept as a tiny injection seam for unit tests and downstream deployments;
    # production reads the editable ``config/positive-negative-list.yaml``.
    return {}


_CONFIG_FILE_REL = "config/positive-negative-list.yaml"


def _ledger_file_config() -> dict[str, Any]:
    """Optional editable PNL config (``config/positive-negative-list.yaml``).

    Mirrors the todo-sop pattern: coordinates and column labels are editable
    values while the structure is a contract.  A missing, unreadable, or
    malformed file falls back to the built-in defaults below instead of
    changing ledger behaviour silently.
    """
    try:
        root = _paths.resolve_agent()
    except Exception:
        return {}
    path = Path(str(root)) / _CONFIG_FILE_REL
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
    except Exception as exc:
        logger.warning(
            f"positive-negative-list config unreadable ({type(exc).__name__}: {exc}); fall back to built-ins"
        )
        return {}
    ledger = loaded.get("ledger") if isinstance(loaded, dict) else None
    if not isinstance(ledger, dict):
        return {}
    app_token = str(ledger.get("app_token") or "").strip()
    table_id = str(ledger.get("table_id") or "").strip()
    if not app_token or not table_id:
        logger.warning("positive-negative-list config lacks ledger coordinates; fall back to built-ins")
        return {}
    result: dict[str, Any] = {"app_token": app_token, "table_id": table_id}
    view_id = str(ledger.get("view_id") or "").strip()
    if view_id:
        result["view_id"] = view_id
    host = str(ledger.get("host") or "").strip()
    if host:
        result["host"] = host
    columns = ledger.get("columns")
    if isinstance(columns, dict):
        names = {str(semantic): str(field_name) for semantic, field_name in columns.items() if str(field_name).strip()}
        if names:
            result["columns"] = names
    return result


def _default_ledger_coordinates() -> tuple[str, str, str]:
    file_config = _ledger_file_config()
    if file_config:
        return (
            file_config["app_token"],
            file_config["table_id"],
            file_config.get("view_id") or _SOURCE_VIEW_ID,
        )
    return _SOURCE_APP_TOKEN, _SOURCE_TABLE_ID, _SOURCE_VIEW_ID


def _default_ledger_field_names() -> dict[str, str]:
    file_config = _ledger_file_config()
    columns = file_config.get("columns") if file_config else None
    return dict(columns) if columns else dict(_LEDGER_FIELD_NAMES)


def _default_ledger_web_host() -> str:
    file_config = _ledger_file_config()
    host = file_config.get("host") if file_config else None
    return str(host).strip() if host else _SOURCE_WEB_HOST


def _field_config(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    configured = raw.get("field_ids", {})
    if not isinstance(configured, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for semantic, value in configured.items():
        if isinstance(value, str) and value:
            result[str(semantic)] = {"field_id": value, "field_name": str(semantic), "type": 1}
        elif isinstance(value, dict):
            result[str(semantic)] = dict(value)
    return result


def _target_section(config: dict[str, Any], target: str) -> dict[str, Any]:
    configured = config.get(f"{target}_target")
    return configured if isinstance(configured, dict) else config


def _target_env(config: dict[str, Any], target: str, key: str, default: str) -> str:
    section = _target_section(config, target)
    value = section.get(key)
    return str(value).strip() if isinstance(value, str) and value.strip() else default


def _target_coordinates(config: dict[str, Any], target: str) -> tuple[str, str, str]:
    if not config:
        # Reads and confirmed writes both target the same public ledger; there
        # is no robot-provisioned test table left to initialize.
        return _default_ledger_coordinates()
    app_env = _target_env(config, target, "app_token_env", "HAITUN_PNL_APP_TOKEN")
    table_env = _target_env(config, target, "table_id_env", "HAITUN_PNL_TABLE_ID")
    view_env = _target_env(config, target, "view_id_env", "HAITUN_PNL_VIEW_ID")
    app_token, table_id = resolve_table_config(
        os.environ.get(app_env, ""),
        os.environ.get(table_env, ""),
        app_token_env=app_env,
        table_id_env=table_env,
    )
    return app_token, table_id, os.environ.get(view_env, "").strip()


def _read_field_names(config: dict[str, Any]) -> dict[str, str]:
    if not config:
        names = dict(_SOURCE_FIELD_NAMES)
        file_config = _ledger_file_config()
        columns = file_config.get("columns") if file_config else None
        if columns:
            names.update(columns)
        return names
    section = _target_section(config, "read")
    raw = section.get("field_names", {})
    if not isinstance(raw, dict) or not raw:
        raw = {
            semantic: value.get("field_name")
            for semantic, value in _field_config(config).items()
            if isinstance(value, dict) and value.get("field_name")
        }
    return {
        str(semantic): str(field_name)
        for semantic, field_name in raw.items()
        if isinstance(semantic, str) and isinstance(field_name, str) and field_name.strip()
    }


def _write_field_names(config: dict[str, Any]) -> dict[str, str]:
    section = _target_section(config, "write")
    raw = section.get("field_names", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(semantic): str(field_name)
        for semantic, field_name in raw.items()
        if isinstance(semantic, str) and isinstance(field_name, str) and field_name.strip()
    }


def _write_mode(config: dict[str, Any]) -> str:
    section = _target_section(config, "write")
    return str(section.get("mode") or "").strip().casefold()


def _enum_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = config.get("enum_requirements", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for semantic, requirement in raw.items():
        if not isinstance(requirement, dict):
            continue
        normalized = dict(requirement)
        options = normalized.get("options")
        if isinstance(options, Sequence) and not isinstance(options, (str, bytes)):
            normalized["options"] = frozenset(str(item) for item in options if str(item))
        result[str(semantic)] = normalized
    return result


class ConfiguredTableClient(FeishuLedgerClient):
    def __init__(self, app_token: str, table_id: str, config: dict[str, Any]) -> None:
        fields = _field_config(config)
        if _write_mode(config) == "existing_columns":
            write_names = _write_field_names(config)
            for semantic, field_name in write_names.items():
                fields.setdefault(semantic, {"field_name": field_name, "type": 1})
            note_name = write_names.get("note", "备注")
            for semantic in ("source_key", "canonical_incident_id", "cross_source_fingerprint"):
                fields.setdefault(semantic, {"field_name": note_name, "type": 1})
        names = {
            semantic: str(value.get("field_name") or semantic)
            for semantic, value in fields.items()
            if isinstance(value, dict)
        }
        super().__init__(app_token, table_id, names)
        self._config = config
        self.web_host = str(config.get("web_host") or "").strip()
        self._fields = fields
        self._field_names_by_id = {
            str(value.get("field_id")): str(value.get("field_name") or semantic)
            for semantic, value in fields.items()
            if isinstance(value, dict) and value.get("field_id")
        }

    async def preflight(self, user_key: str):
        if not self._fields:
            return validate_table_schema(
                (),
                {"case_id": {}},
                {},
                app_token=self.app_token,
                target_table_id=self.table_id,
                candidate_table_ids=(self.table_id,),
                view_purposes={},
                can_create_records=False,
                notification_user_key=user_key,
                notification_identity_provenance="trusted_feishu_context" if user_key.startswith("ou_") else "",
            )
        listed = await _f.list_bitable_fields_impl(self.app_token, self.table_id)
        if not listed.get("ok"):
            return TableSchemaValidation(False, ("table.fields.unreadable",), None)
        fields = []
        for item in listed.get("fields", []):
            if not isinstance(item, dict):
                continue
            field_id = item.get("field_id")
            field_name = item.get("name")
            field_type = item.get("type")
            if isinstance(field_type, str):
                field_type = _TYPE_NAMES.get(field_type, field_type)
            field = {
                "field_id": field_id,
                "field_name": field_name,
                "type": field_type,
                "property": item.get("property", {}),
            }
            fields.append(field)
            if isinstance(field_id, str) and isinstance(field_name, str) and field_id and field_name:
                self._field_names_by_id[field_id] = field_name
        if _write_mode(self._config) == "existing_columns":
            return self._preflight_existing_columns(fields, user_key)
        # Every value emitted by ``TableAdapter.create_public_record`` must
        # have an explicit configured destination. Otherwise a row could be
        # created while silently dropping behavior or identity evidence.
        required = {semantic: self._fields.get(semantic, {}) for semantic in _REQUIRED_LEDGER_FIELDS}
        enum_requirements = _enum_config(self._config)
        write_section = _target_section(self._config, "write")
        view_purposes = write_section.get("view_purposes", self._config.get("view_purposes", {}))
        if not isinstance(view_purposes, dict):
            view_purposes = {}
        if not view_purposes:
            purpose = str(self._config.get("target_view_purpose") or "").strip()
            view_env = str(self._config.get("view_id_env") or "HAITUN_PNL_VIEW_ID").strip()
            view_id = os.environ.get(view_env, "").strip() if view_env else ""
            if purpose and view_id:
                view_purposes = {view_id: purpose}
        return validate_table_schema(
            fields,
            required,
            enum_requirements,
            app_token=self.app_token,
            target_table_id=self.table_id,
            candidate_table_ids=(self.table_id,),
            view_purposes=view_purposes,
            can_create_records=bool(user_key.startswith("ou_")),
            notification_user_key=user_key,
            notification_identity_provenance="trusted_feishu_context" if user_key.startswith("ou_") else "",
        )

    def _preflight_existing_columns(self, fields: list[dict[str, Any]], user_key: str) -> TableSchemaValidation:
        names = _write_field_names(self._config)
        required_names = {
            "nature": names.get("nature", ""),
            "subject_user_key": names.get("subject_user_key", ""),
            "fact_summary": names.get("fact_summary", ""),
            "occurred_at": names.get("occurred_at", ""),
            "note": names.get("note", ""),
            "reporter_user_key": names.get("reporter_user_key", ""),
        }
        fields_by_name = {
            str(field.get("field_name")): field
            for field in fields
            if isinstance(field.get("field_name"), str) and field.get("field_name")
        }
        errors: list[str] = []
        type_requirements = {
            "nature": 3,
            "subject_user_key": 11,
            "fact_summary": 1,
            "occurred_at": 5,
            "note": 1,
            "reporter_user_key": 11,
        }
        field_ids: dict[str, str] = {}
        for semantic, field_name in required_names.items():
            field = fields_by_name.get(field_name)
            if field is None:
                errors.append(f"{semantic}.field")
                continue
            field_id = str(field.get("field_id") or "")
            if not field_id:
                errors.append(f"{semantic}.field_id")
                continue
            field_ids[semantic] = field_id
            actual_type = field.get("type")
            expected_type = type_requirements[semantic]
            if actual_type != expected_type:
                errors.append(f"{semantic}.type")
        allowed_names = set(required_names.values()) | {"记录ID"}
        unexpected = sorted(name for name in fields_by_name if name not in allowed_names)
        if unexpected:
            errors.append("unexpected_fields:" + ",".join(unexpected))
        view_purposes = self._config.get("view_purposes", {})
        if not isinstance(view_purposes, dict):
            view_purposes = {}
        view_env = _target_env(self._config, "write", "view_id_env", "HAITUN_PNL_VIEW_ID")
        view_id = os.environ.get(view_env, "").strip() if view_env else ""
        if not view_purposes and view_id:
            view_purposes = {view_id: "public_ledger"}
        # The production write target is the existing public ledger itself (or
        # its editable replacement from ``config/positive-negative-list.yaml``):
        # there is no robot-provisioned table or AppData target file.  Declare
        # the ledger's public view as the write-path view purpose so the first
        # confirmed write never requires extra deployment configuration.
        if not view_purposes:
            resolved_app, resolved_table, resolved_view = _default_ledger_coordinates()
            if self.app_token == resolved_app and self.table_id == resolved_table:
                view_purposes = {resolved_view or _SOURCE_VIEW_ID: "public_ledger"}
        required = {
            "nature": {
                "field_id": field_ids.get("nature", ""),
                "field_name": required_names["nature"],
                "type": type_requirements["nature"],
            },
            "subject_user_key": {
                "field_id": field_ids.get("subject_user_key", ""),
                "field_name": required_names["subject_user_key"],
                "type": type_requirements["subject_user_key"],
            },
            "fact_summary": {
                "field_id": field_ids.get("fact_summary", ""),
                "field_name": required_names["fact_summary"],
                "type": type_requirements["fact_summary"],
            },
            "occurred_at": {
                "field_id": field_ids.get("occurred_at", ""),
                "field_name": required_names["occurred_at"],
                "type": type_requirements["occurred_at"],
            },
            "reporter_user_key": {
                "field_id": field_ids.get("reporter_user_key", ""),
                "field_name": required_names["reporter_user_key"],
                "type": type_requirements["reporter_user_key"],
            },
            "source_key": {
                "field_id": field_ids.get("note", ""),
                "field_name": required_names["note"],
                "type": type_requirements["note"],
            },
            "canonical_incident_id": {
                "field_id": field_ids.get("note", ""),
                "field_name": required_names["note"],
                "type": type_requirements["note"],
            },
            "cross_source_fingerprint": {
                "field_id": field_ids.get("note", ""),
                "field_name": required_names["note"],
                "type": type_requirements["note"],
            },
        }
        enum = _enum_config(self._config)
        if "nature" in enum:
            enum["nature"] = {**enum["nature"], "field_id": field_ids.get("nature", "")}
        result = validate_table_schema(
            fields,
            required,
            enum,
            app_token=self.app_token,
            target_table_id=self.table_id,
            candidate_table_ids=(self.table_id,),
            view_purposes=view_purposes,
            can_create_records=bool(user_key.startswith("ou_")),
            notification_user_key=user_key,
            notification_identity_provenance="trusted_feishu_context" if user_key.startswith("ou_") else "",
            allow_deduplication_aliases=True,
        )
        if errors:
            return TableSchemaValidation(False, tuple(dict.fromkeys((*result.errors, *errors))), None)
        return result

    def build_existing_case_fields(self, case, schema):
        """Map the rich case into the six columns of the public ledger.

        The public ledger intentionally has no extra columns.  Analysis and
        deduplication metadata therefore lives together in the existing
        ``备注`` column instead of allowing repeated semantic aliases to
        overwrite each other during generic field translation.
        """
        ids = schema.field_ids_by_semantic_name
        values = {
            ids["fact_summary"]: _encode_field_value("fact_summary", case.fact_summary, schema),
            ids["nature"]: _encode_field_value("nature", case.nature, schema),
            ids["subject_user_key"]: _encode_field_value("subject_user_key", case.subject_user_key, schema),
            ids["occurred_at"]: _encode_field_value("occurred_at", case.occurred_at, schema),
            ids["reporter_user_key"]: _encode_field_value("reporter_user_key", case.reporter_user_key, schema),
        }
        note_lines = [
            f"分类：{case.category}",
            f"场合/背景：{case.context}",
            f"影响：{case.impact}",
            f"证据来源：{'、'.join(case.evidence_sources)}",
            f"Agent判断：{case.agent_inference}",
            f"来源标识：{case.source_key}",
            f"事件标识：{case.canonical_incident_id}",
            f"跨源去重标识：{case.cross_source_fingerprint}",
        ]
        if case.nature == "negative":
            note_lines.extend(
                (
                    f"正确做法：{case.correct_behavior}",
                    f"立即补救：{case.immediate_remedy}",
                    f"预防措施：{case.prevention}",
                )
            )
        values[ids["source_key"]] = "\n".join(note_lines)
        return values

    async def search(self, field_id: str, value: str, user_key: str, operator: str = "is"):
        """Search one configured column using its deployed Feishu field name.

        ``operator`` is a Feishu filter operator.  The six-column ledger
        aliases every deduplication identifier into the ``备注`` text column,
        where an exact ``is`` match can never hit a multi-line cell; callers
        resolving aliased columns pass ``contains`` instead.
        """
        field_name = self._field_names_by_id.get(field_id)
        if not field_name:
            raise ValueError(f"unknown configured field ID: {field_id}")
        result = await _f.search_bitable_records_impl(
            app_token=self.app_token,
            table_id=self.table_id,
            filter_json=json.dumps(
                {
                    "conjunction": "and",
                    "conditions": [{"field_name": field_name, "operator": operator, "value": [value]}],
                },
                ensure_ascii=False,
            ),
            field_names=json.dumps(_field_name_list(self.field_names, self._configured_semantics), ensure_ascii=False),
            page_size=100,
            user_key=user_key,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            message = result.get("error") if isinstance(result, dict) else "table search failed"
            raise RuntimeError(str(message or "table search failed"))
        return result.get("records", [])

    async def create(self, fields: Mapping[str, Any], user_key: str):
        """Create one row after translating semantic field IDs to Feishu names."""
        translated: dict[str, Any] = {}
        for field_id, value in fields.items():
            field_name = self._field_names_by_id.get(field_id)
            if not field_name:
                raise ValueError(f"unknown configured field ID: {field_id}")
            translated[field_name] = value
        result = await _f.create_bitable_records_impl(
            self.app_token,
            self.table_id,
            json.dumps([{"fields": translated}], ensure_ascii=False),
            user_key=user_key,
            identity=str(self._config.get("write_identity") or "bot"),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return result if isinstance(result, dict) else {"ok": False, "error": "table create failed"}
        created = result.get("created") if isinstance(result.get("created"), list) else []
        return {"record_id": str(created[0]) if created else "", "fields": translated, **dict(result)}


def configured_table_adapter() -> TableAdapter:
    config = _load_config()
    app_token, table_id, _ = _target_coordinates(config, "write")
    effective = config or {
        "write_target": {"mode": "existing_columns", "field_names": _default_ledger_field_names()},
        "web_host": _default_ledger_web_host(),
    }
    return TableAdapter(ConfiguredTableClient(app_token, table_id, effective))


def configured_read_table_adapter() -> TableAdapter:
    config = _load_config()
    app_token, table_id, _ = _target_coordinates(config, "read")
    return TableAdapter(
        cast(
            TableClient,
            FeishuLedgerClient(
                app_token,
                table_id,
                _read_field_names(config),
                strict_field_names=True,
            ),
        )
    )


def configured_read_view_id() -> str:
    config = _load_config()
    return _target_coordinates(config, "read")[2]


def read_target_coordinates() -> tuple[str, str]:
    """Public-ledger coordinates used by the read-side capability guard."""
    config = _load_config()
    app_token, table_id, _ = _target_coordinates(config, "read")
    return app_token, table_id


__all__ = [
    "ConfiguredTableClient",
    "configured_read_table_adapter",
    "configured_read_view_id",
    "configured_table_adapter",
    "read_target_coordinates",
]
