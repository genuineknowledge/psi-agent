"""Safely update Human-owned fields in the configured recruitment tables."""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NoReturn
from urllib.parse import parse_qsl, urlsplit

import anyio
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_feishu = importlib.import_module("_feishu_impl")
_paths = importlib.import_module("_runtime_paths")

_DEFAULTS_RELATIVE_PATH = os.path.join(
    "flows",
    "workflows",
    "resume-approval",
    "resume-approval.defaults.json",
)
_MAX_CONFIG_CHARS = 1_000_000
_MAX_TEXT_CHARS = 10_000
_MAX_NAME_CHARS = 100
_MAX_LINK_CHARS = 2_048
_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_SEARCH_PAGES = 40

_TARGET_TABLE_KEY = {
    "人才库": "talent_pool_table_id",
    "面试记录": "interview_table_id",
}
_TALENT_FIELDS = ("备注", "初审状态")
_INTERVIEW_FIELDS = (
    "面试纪要",
    "补充信息",
    "靠谱评分",
    "专业能力评分",
    "学习行动评分",
    "AI Native评分",
    "面试定级",
    "聪明人等级",
    "疑问待验证",
    "风险验证结果",
    "面试结论",
    "面试状态",
    "面试时间",
)
_FIELD_ORDER = {
    "人才库": _TALENT_FIELDS,
    "面试记录": _INTERVIEW_FIELDS,
}
_TEXT_FIELDS = frozenset(
    {"备注", "面试纪要", "补充信息", "疑问待验证", "风险验证结果", "面试结论"}
)
_SCORE_FIELDS = frozenset({"靠谱评分", "专业能力评分", "学习行动评分", "AI Native评分"})
_ENUMS = {
    "初审状态": frozenset({"待审批", "通过", "不通过"}),
    "面试定级": frozenset({"S", "A", "B", "C"}),
    "聪明人等级": frozenset({"T1", "T2", "T3", "T4", "T5"}),
    "面试状态": frozenset({"待安排", "待面试", "待补充", "已完成", "录用", "不录用", "待定"}),
}
_AWARE_ISO8601 = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_RECORD_ID = re.compile(r"\Arec[A-Za-z0-9_-]{1,124}\Z")
_CONFIG_ID = re.compile(r"\A[A-Za-z0-9_-]{3,256}\Z")


class _InvalidInputError(ValueError):
    """An input or local configuration failed the closed validation boundary."""


class _RemoteFailureError(RuntimeError):
    """A Feishu operation failed without exposing its response to the caller."""


def _reject_constant(_value: str) -> NoReturn:
    raise _InvalidInputError("JSON constants must be finite")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidInputError("JSON object keys must be unique")
        result[key] = value
    return result


def _strict_json(raw: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise _InvalidInputError("Invalid JSON") from exc


def _safe_result(
    *,
    ok: bool,
    target: str,
    matched_by: str,
    fields: list[str],
    message: str,
) -> str:
    safe_target = target if isinstance(target, str) and target in _TARGET_TABLE_KEY else "未识别目标"
    payload = {
        "ok": ok,
        "target": safe_target,
        "matched_by": matched_by,
        "fields": fields,
        "message": message,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _ordered_fields(target: str, names: set[str]) -> list[str]:
    return [name for name in _FIELD_ORDER.get(target, ()) if name in names]


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _destination_config_path() -> str:
    workspace = os.path.realpath(_paths.workspace_dir())
    path = os.path.realpath(os.path.join(workspace, _DEFAULTS_RELATIVE_PATH))
    if not workspace or not _inside(path, workspace):
        raise _InvalidInputError("Invalid workspace configuration path")
    return path


async def _load_destination(target: str) -> dict[str, str]:
    config_path = anyio.Path(_destination_config_path())
    if not await config_path.is_file():
        raise _InvalidInputError("Recruitment configuration is unavailable")
    try:
        raw = await config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _InvalidInputError("Recruitment configuration is unreadable") from exc
    if len(raw) > _MAX_CONFIG_CHARS:
        raise _InvalidInputError("Recruitment configuration is too large")
    defaults = _strict_json(raw)
    if not isinstance(defaults, dict) or not isinstance(defaults.get("feishu_config"), dict):
        raise _InvalidInputError("Recruitment configuration is invalid")
    source = defaults["feishu_config"]
    table_key = _TARGET_TABLE_KEY[target]
    destination = {
        "app_token": source.get("app_token"),
        "table_id": source.get(table_key),
        "user_key": source.get("user_key"),
        "identity": source.get("identity"),
    }
    if not all(isinstance(value, str) and value.strip() for value in destination.values()):
        raise _InvalidInputError("Recruitment destination is incomplete")
    normalized = {key: value.strip() for key, value in destination.items()}
    if any(
        not _CONFIG_ID.fullmatch(normalized[key])
        for key in ("app_token", "table_id", "user_key")
    ):
        raise _InvalidInputError("Recruitment destination is invalid")
    if normalized["identity"] not in {"user", "bot"}:
        raise _InvalidInputError("Recruitment identity is invalid")
    return normalized


def _timestamp_milliseconds(value: Any) -> int:
    if not isinstance(value, str) or not _AWARE_ISO8601.fullmatch(value):
        raise _InvalidInputError("Interview time must be timezone-aware ISO 8601")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
        offset = parsed.utcoffset()
        if offset is None:
            raise ValueError
        milliseconds = int(parsed.timestamp() * 1_000)
    except (OverflowError, OSError, ValueError) as exc:
        raise _InvalidInputError("Interview time is invalid") from exc
    return milliseconds


def _validate_updates(target: str, updates_json: str) -> dict[str, Any]:
    parsed = _strict_json(updates_json)
    if not isinstance(parsed, dict) or not parsed:
        raise _InvalidInputError("Updates must be a non-empty object")
    allowed = frozenset(_FIELD_ORDER[target])
    if any(not isinstance(key, str) or key not in allowed for key in parsed):
        raise _InvalidInputError("Updates contain a field outside the target whitelist")
    normalized: dict[str, Any] = {}
    for field, value in parsed.items():
        if field in _TEXT_FIELDS:
            if not isinstance(value, str) or len(value) > _MAX_TEXT_CHARS:
                raise _InvalidInputError("Text field value is invalid")
            normalized[field] = value
        elif field in _SCORE_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise _InvalidInputError("Score field value is invalid")
            normalized[field] = value
        elif field in _ENUMS:
            if not isinstance(value, str) or value not in _ENUMS[field]:
                raise _InvalidInputError("Enum field value is invalid")
            normalized[field] = value
        elif field == "面试时间":
            normalized[field] = _timestamp_milliseconds(value)
        else:
            raise _InvalidInputError("Unsupported recruitment field")
    return normalized


def _valid_record_id(value: str) -> str:
    stripped = value.strip()
    if not _RECORD_ID.fullmatch(stripped):
        raise _InvalidInputError("Invalid record identifier")
    return stripped


def _record_id_from_link(link: str) -> str:
    if not isinstance(link, str) or not link.strip() or len(link) > _MAX_LINK_CHARS:
        raise _InvalidInputError("Invalid record link")
    parsed = urlsplit(link.strip())
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or (
            hostname not in {"feishu.cn", "larksuite.com"}
            and not hostname.endswith((".feishu.cn", ".larksuite.com"))
        )
    ):
        raise _InvalidInputError("Invalid record link")
    candidates = [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key in {"record", "record_id"}
    ]
    if len(candidates) != 1:
        raise _InvalidInputError("Record link must identify exactly one row")
    return _valid_record_id(candidates[0])


async def _timed(call: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        with anyio.fail_after(_REQUEST_TIMEOUT_SECONDS):
            result = await call()
    except TimeoutError as exc:
        raise _RemoteFailureError("Feishu operation timed out") from exc
    except Exception as exc:
        raise _RemoteFailureError("Feishu operation failed") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise _RemoteFailureError("Feishu operation failed")
    return result


async def _search_by_name(destination: dict[str, str], candidate_name: str) -> list[str]:
    name = candidate_name.strip()
    if not name or len(name) > _MAX_NAME_CHARS:
        raise _InvalidInputError("Candidate name is invalid")
    filter_json = json.dumps(
        {
            "conjunction": "and",
            "conditions": [{"field_name": "姓名", "operator": "is", "value": [name]}],
        },
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    page_token = ""
    seen_page_tokens: set[str] = set()
    record_ids: list[str] = []
    seen_record_ids: set[str] = set()
    for _page in range(_MAX_SEARCH_PAGES):

        async def search(search_page_token: str = page_token) -> dict[str, Any]:
            return await _feishu.search_bitable_records_impl(
                destination["app_token"],
                destination["table_id"],
                filter_json=filter_json,
                field_names='["姓名"]',
                page_size=500,
                page_token=search_page_token,
                user_key=destination["user_key"],
            )

        result = await _timed(search)
        records = result.get("records")
        if not isinstance(records, list):
            raise _RemoteFailureError("Feishu search response is invalid")
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("record_id"), str):
                raise _RemoteFailureError("Feishu search response is invalid")
            fields = record.get("fields")
            if (
                not isinstance(fields, dict)
                or not isinstance(fields.get("姓名"), str)
                or fields["姓名"] != name
            ):
                raise _RemoteFailureError("Feishu search response is invalid")
            try:
                found = _valid_record_id(record["record_id"])
            except _InvalidInputError as exc:
                raise _RemoteFailureError("Feishu search response is invalid") from exc
            if found in seen_record_ids:
                raise _RemoteFailureError("Feishu search response is ambiguous")
            seen_record_ids.add(found)
            record_ids.append(found)
        has_more = result.get("has_more")
        if not isinstance(has_more, bool):
            raise _RemoteFailureError("Feishu search pagination is invalid")
        if not has_more:
            return record_ids
        next_page = result.get("page_token")
        if not isinstance(next_page, str) or not next_page or next_page in seen_page_tokens:
            raise _RemoteFailureError("Feishu search pagination is invalid")
        seen_page_tokens.add(next_page)
        page_token = next_page
    raise _RemoteFailureError("Feishu search exceeded the supported table size")


def _build_get_record_request(destination: dict[str, str], record_id: str) -> BaseRequest:
    request = BaseRequest()
    request.http_method = HttpMethod.GET
    request.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"
    request.paths["app_token"] = destination["app_token"]
    request.paths["table_id"] = destination["table_id"]
    request.paths["record_id"] = record_id
    request.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return request


def _field_matches(field: str, expected: Any, actual: Any) -> bool:
    if field in _SCORE_FIELDS or field == "面试时间":
        return (
            not isinstance(actual, bool)
            and isinstance(actual, (int, float))
            and math.isfinite(actual)
            and actual == expected
        )
    return isinstance(actual, str) and actual == expected


async def _update_and_verify(destination: dict[str, str], record_id: str, updates: dict[str, Any]) -> None:
    fields_json = json.dumps(updates, ensure_ascii=False, sort_keys=True, allow_nan=False)

    async def update() -> dict[str, Any]:
        return await _feishu.update_bitable_record_impl(
            destination["app_token"],
            destination["table_id"],
            record_id,
            fields_json,
            destination["user_key"],
            destination["identity"],
            True,
        )

    updated = await _timed(update)
    if updated.get("dropped_fields"):
        raise _RemoteFailureError("Feishu omitted one or more fields")
    request = _build_get_record_request(destination, record_id)

    async def read_back() -> dict[str, Any]:
        return await _feishu._invoke(request, user_key=destination["user_key"])

    result = await _timed(read_back)
    data = result.get("data")
    record = data.get("record") if isinstance(data, dict) else None
    fields = record.get("fields") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or record.get("record_id") != record_id
        or not isinstance(fields, dict)
        or any(not _field_matches(field, value, fields.get(field)) for field, value in updates.items())
    ):
        raise _RemoteFailureError("Feishu readback did not match the requested update")


async def recruitment_update_record(
    target: Literal["人才库", "面试记录"],
    updates_json: str,
    record_id: str = "",
    candidate_name: str = "",
    record_link: str = "",
) -> str:
    """Update approved Human-owned fields in one configured recruitment row.

    Use this for conversational corrections or additions after a recruitment
    workflow, such as setting ``初审状态``, adding interview notes, entering the
    four explicit 1-5 scores, or recording an interview status/time. ``updates_json``
    must be a non-empty JSON object containing only the target's approved business
    fields. Give the exact ``record_id`` when known; otherwise give a Feishu row link,
    or an exact candidate name only when it identifies one row. The app and table are
    always loaded from the current workspace's fixed recruitment defaults and can
    never be selected through arguments or a link. Success is returned only after a
    same-row readback verifies every requested field.

    Args:
        target: ``人才库`` or ``面试记录``.
        updates_json: Business field/value object. Scores are integer 1-5; interview
            time is strict timezone-aware ISO 8601.
        record_id: Exact Feishu row ID. Takes precedence over every other locator.
        candidate_name: Exact ``姓名`` lookup used only when no row ID or link is given.
        record_link: Feishu row link used only to extract its row ID; link app/table
            coordinates never change the configured destination.
    """
    matched_by = "未匹配"
    safe_fields: list[str] = []
    try:
        if not isinstance(target, str) or target not in _TARGET_TABLE_KEY:
            raise _InvalidInputError("Unsupported recruitment target")
        if not all(isinstance(value, str) for value in (updates_json, record_id, candidate_name, record_link)):
            raise _InvalidInputError("Recruitment update arguments must be text")
        updates = _validate_updates(target, updates_json)
        safe_fields = _ordered_fields(target, set(updates))
        destination = await _load_destination(target)
        if record_id.strip():
            selected_record = _valid_record_id(record_id)
            matched_by = "明确记录"
        elif record_link.strip():
            selected_record = _record_id_from_link(record_link)
            matched_by = "行链接"
        else:
            matched_by = "姓名精确匹配"
            records = await _search_by_name(destination, candidate_name)
            if not records:
                return _safe_result(
                    ok=False,
                    target=target,
                    matched_by=matched_by,
                    fields=safe_fields,
                    message="未找到匹配记录, 未写入; 请检查姓名或提供行链接。",
                )
            if len(records) != 1:
                return _safe_result(
                    ok=False,
                    target=target,
                    matched_by=matched_by,
                    fields=safe_fields,
                    message=f"精确匹配到 {len(records)} 行, 未写入; 请提供行链接消歧。",
                )
            selected_record = records[0]
        await _update_and_verify(destination, selected_record, updates)
    except _InvalidInputError:
        return _safe_result(
            ok=False,
            target=target,
            matched_by=matched_by,
            fields=safe_fields,
            message="输入或本地招聘配置不符合安全约束, 未写入。",
        )
    except _RemoteFailureError:
        return _safe_result(
            ok=False,
            target=target,
            matched_by=matched_by,
            fields=safe_fields,
            message="招聘记录更新或读回核验失败, 结果未确认; 请稍后重试或人工核对。",
        )
    except Exception:
        return _safe_result(
            ok=False,
            target=target,
            matched_by=matched_by,
            fields=safe_fields,
            message="招聘记录更新或读回核验失败, 结果未确认; 请稍后重试或人工核对。",
        )
    return _safe_result(
        ok=True,
        target=target,
        matched_by=matched_by,
        fields=safe_fields,
        message=f"已更新并读回核验 {len(safe_fields)} 个业务字段。",
    )
