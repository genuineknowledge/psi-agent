"""Runtime wiring for the configured public positive-negative ledger."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import _feishu_impl as _f
import anyio
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

from _positive_negative_list.preflight import TableSchemaValidation, validate_table_schema
from _positive_negative_list.reader import FeishuLedgerClient, _field_name_list, resolve_table_config
from _positive_negative_list.table import TableAdapter, TableClient, _encode_field_value

# The source ledger is the existing organization base.  These coordinates are
# intentionally kept in code: they are the user-provided source, not a new
# deployment configuration surface.  The write target is provisioned once in
# AppData and is never the public base.
_SOURCE_APP_TOKEN = "RNEvbLIJAaPPdksfv8YceTmjndg"
_SOURCE_TABLE_ID = "tblwXV7Xlwu0hVYH"
_SOURCE_VIEW_ID = "veweChthHV"
_TEST_APP_TOKEN = ""
_TEST_TABLE_ID = ""
_TEST_VIEW_ID = ""
_TEST_FIELD_NAMES = {
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
    # the production path deliberately has no positive-negative config file.
    return {}


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
    global _TEST_APP_TOKEN, _TEST_TABLE_ID, _TEST_VIEW_ID
    if not config:
        if target == "read":
            return _SOURCE_APP_TOKEN, _SOURCE_TABLE_ID, _SOURCE_VIEW_ID
        if not _TEST_APP_TOKEN or not _TEST_TABLE_ID:
            raise ValueError("测试表尚未初始化，请先确认一条候选记录")
        return _TEST_APP_TOKEN, _TEST_TABLE_ID, _TEST_VIEW_ID
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
        return dict(_SOURCE_FIELD_NAMES)
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
        view_id = (
            _TEST_VIEW_ID
            if self.app_token == _TEST_APP_TOKEN and self.table_id == _TEST_TABLE_ID
            else os.environ.get(view_env, "").strip()
        )
        if not view_purposes and view_id:
            view_purposes = {view_id: "public_ledger"}
        # The MVP write target is provisioned by the robot at runtime and has
        # no deployment config or user-selected view.  It is already isolated
        # by its own Base/table coordinates, so requiring an additional view
        # identifier here would make the first confirmed write impossible and
        # would contradict the no-extra-config requirement.  Keep the generic
        # validation contract for configured targets, while treating this
        # private robot-owned table as its own public-ledger purpose.
        if (
            not view_purposes
            and _write_mode(self._config) == "existing_columns"
            and self.app_token == _TEST_APP_TOKEN
            and self.table_id == _TEST_TABLE_ID
        ):
            view_purposes = {self.table_id: "public_ledger"}
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
        """Map the rich case into the six columns shared with the formal ledger.

        The test table intentionally has no extra columns.  Analysis and
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

    async def search(self, field_id: str, value: str, user_key: str):
        """Search one configured column using its deployed Feishu field name."""
        field_name = self._field_names_by_id.get(field_id)
        if not field_name:
            raise ValueError(f"unknown configured field ID: {field_id}")
        result = await _f.search_bitable_records_impl(
            app_token=self.app_token,
            table_id=self.table_id,
            filter_json=json.dumps(
                {"conjunction": "and", "conditions": [{"field_name": field_name, "operator": "is", "value": [value]}]},
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
    effective = config or {"write_target": {"mode": "existing_columns", "field_names": _TEST_FIELD_NAMES}}
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


def _create_base_request() -> BaseRequest:
    request = BaseRequest()
    request.http_method = HttpMethod.POST
    request.uri = "/open-apis/bitable/v1/apps"
    request.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    request.body = {"name": "HaiTun 正负面清单测试表"}
    return request


def _create_table_request(app_token: str) -> BaseRequest:
    request = BaseRequest()
    request.http_method = HttpMethod.POST
    request.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    request.paths["app_token"] = app_token
    request.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    request.body = {
        "table": {
            "name": "正负面清单",
            "fields": [
                {"field_name": "事件描述", "type": 1},
                {
                    "field_name": "正负面归属",
                    "type": 3,
                    "property": {
                        "options": [
                            {"name": "正面清单", "color": 0},
                            {"name": "负面清单", "color": 1},
                            {"name": "中性", "color": 2},
                            {"name": "证据不足", "color": 3},
                        ]
                    },
                },
                {"field_name": "员工姓名", "type": 11},
                {"field_name": "记录日期", "type": 5},
                {"field_name": "备注", "type": 1},
                {"field_name": "填写人", "type": 11},
            ],
        }
    }
    return request


async def _load_test_state() -> None:
    global _TEST_APP_TOKEN, _TEST_TABLE_ID, _TEST_VIEW_ID
    if _TEST_APP_TOKEN and _TEST_TABLE_ID:
        return
    try:
        from psi_agent._appdata import resolve_appdata_root  # noqa: PLC0415

        root = await resolve_appdata_root()
        path = Path(root) / "positive-negative-list" / "test-target.json"
        payload = json.loads(await anyio.Path(str(path)).read_text(encoding="utf-8"))
    except OSError, TypeError, ValueError, json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        app_token = str(payload.get("app_token") or "").strip()
        table_id = str(payload.get("table_id") or "").strip()
        view_id = str(payload.get("view_id") or "").strip()
        if app_token and table_id:
            _TEST_APP_TOKEN, _TEST_TABLE_ID, _TEST_VIEW_ID = app_token, table_id, view_id


async def _save_test_state(app_token: str, table_id: str, view_id: str) -> None:
    from psi_agent._appdata import resolve_appdata_root  # noqa: PLC0415

    root = await resolve_appdata_root()
    directory = Path(root) / "positive-negative-list"
    await anyio.Path(str(directory)).mkdir(parents=True, exist_ok=True)
    path = directory / "test-target.json"
    temporary = anyio.Path(str(path) + ".tmp")
    await temporary.write_text(
        json.dumps({"app_token": app_token, "table_id": table_id, "view_id": view_id}),
        encoding="utf-8",
    )
    await temporary.replace(anyio.Path(str(path)))


async def ensure_test_table(user_key: str) -> tuple[str, str]:
    """Load or create the robot-owned isolated test table.

    This is deliberately runtime state, not a new config field.  Creation is
    attempted only on the first confirmed write and is idempotent across
    process restarts through AppData.
    """
    global _TEST_APP_TOKEN, _TEST_TABLE_ID, _TEST_VIEW_ID
    await _load_test_state()
    if _TEST_APP_TOKEN and _TEST_TABLE_ID:
        return _TEST_APP_TOKEN, _TEST_TABLE_ID
    base = await _f._invoke(_create_base_request(), user_key=user_key, identity="bot")
    if not isinstance(base, dict) or not base.get("ok"):
        raise RuntimeError(str((base or {}).get("error") or "无法创建机器人测试表"))
    data_raw = base.get("data")
    data: dict[str, Any] = data_raw if isinstance(data_raw, dict) else {}
    app_raw = data.get("app")
    app: dict[str, Any] = app_raw if isinstance(app_raw, dict) else data
    app_token = str(app.get("app_token") or "").strip()
    if not app_token:
        raise RuntimeError("创建测试表成功但未返回 app_token")
    table = await _f._invoke(_create_table_request(app_token), user_key=user_key, identity="bot")
    if not isinstance(table, dict) or not table.get("ok"):
        raise RuntimeError(str((table or {}).get("error") or "无法创建测试表结构"))
    table_data_raw = table.get("data")
    table_data: dict[str, Any] = table_data_raw if isinstance(table_data_raw, dict) else {}
    table_id = str(table_data.get("table_id") or "").strip()
    if not table_id:
        raise RuntimeError("创建测试表成功但未返回 table_id")
    view_id = str(table_data.get("default_view_id") or "").strip()
    _TEST_APP_TOKEN, _TEST_TABLE_ID, _TEST_VIEW_ID = app_token, table_id, view_id
    await _save_test_state(app_token, table_id, view_id)
    return app_token, table_id


__all__ = [
    "ConfiguredTableClient",
    "configured_read_table_adapter",
    "configured_read_view_id",
    "configured_table_adapter",
    "ensure_test_table",
]
