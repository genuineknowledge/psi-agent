"""Read positive-negative ledger records from a Feishu private-chat request."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Any, cast

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import reader, runtime

_SELF_MARKERS = {"我", "本人", "自己", "me", "myself"}
_PERSON_QUERY_KEYS = ("subject_user_key", "reporter_user_key")
_PERSON_ID_PREFIXES = ("ou_", "on_", "user_", "oc_")


def _normalize_person_filters(raw_query: dict[str, Any], user_key: str) -> dict[str, Any]:
    """Turn person-filter values into Feishu-compatible open_ids.

    Person columns only accept open_ids in a bitable filter.  ``我/本人/me``
    maps to the current session sender; open_id-prefixed values pass through;
    anything else (typically a Chinese name) fails fast with guidance instead
    of surfacing Feishu's raw filter error.
    """
    for key in _PERSON_QUERY_KEYS:
        value = raw_query.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value in _SELF_MARKERS:
            if not user_key:
                raise ValueError(
                    f"{key}: 值 '我/本人' 需要当前会话用户身份, 但 user_key 为空"
                )
            raw_query[key] = user_key
        elif value.startswith(_PERSON_ID_PREFIXES):
            raw_query[key] = value
        else:
            raise ValueError(
                f"{key}: 人员字段过滤只接受 open_id(ou_/user_ 等前缀) 或 特殊值 "
                f"'我/本人/me'(=当前会话发起人); 不支持中文姓名 '{value}' — "
                f"需要按姓名查询时先经 feishu_contact_find 解析成 open_id 再传, "
                f"或省略过滤做全量读取后自行筛选"
            )
    return raw_query


async def positive_negative_case_read(
    query_json: str = "",
    page_size: int = 100,
    page_token: str = "",
    user_key: str = "",
) -> str:
    """Read paginated positive-negative records from the configured Feishu table.

    台账调用规范 (务必遵守):
    - 本工具是正负面台账读取的唯一入口, 台账坐标由代码固定:
      base RNEvbLIJAaPPdksfv8YceTmjndg / 表 tblwXV7Xlwu0hVYH(正负清单总表-全员版) / 视图 veweChthHV。
    - 禁止为读台账自写 bash/python/urllib 脚本, 禁止经 feishu_api 列表后改读其它表;
      同库 tblbF6ZVQbNTNxxn(正负清单总表-战争版) 等不是本工具目标。
    - 工具报权限或表不存在错误时, 把错误原文反馈给用户并提示检查应用协作者权限,
      不要自行改坐标或换表重试。
    - 人员过滤 (subject_user_key=涉事人 / reporter_user_key=报告人): 值只接受
      open_id(ou_/user_ 等前缀) 或 特殊值 我/本人/me(=当前会话发起人, 自动填其 open_id);
      中文姓名会报错 — 需要按姓名查时先经 feishu_contact_find 解析成 open_id 再传,
      或省略过滤做全量读取后自行筛选。

    Args:
        query_json: JSON object containing optional filters and pagination fields.
        page_size: Number of rows to request, from 1 to 500.
        page_token: Cursor returned by a previous page.
        user_key: Trusted Feishu sender identity.

    Returns:
        JSON with readable Chinese record fields and a natural-language read
        status.  The internal pagination cursor is consumed by the tool and is
        not returned to the chat model.
    """
    try:
        raw_query = json.loads(query_json) if query_json.strip() else {}
        if not isinstance(raw_query, dict):
            raise ValueError("query_json must be a JSON object")
        _normalize_person_filters(raw_query, user_key)
        query_json = json.dumps(raw_query, ensure_ascii=False)
        adapter = runtime.configured_read_table_adapter()
        query = reader.parse_query(
            query_json,
            page_size=page_size,
            page_token=page_token,
            view_id=runtime.configured_read_view_id(),
        )
        result = await reader.read_records(cast(reader.FeishuLedgerClient, adapter._client), query, user_key)
        result = await reader.public_result_with_names(result)
    except (TypeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    except (OSError, RuntimeError) as exc:
        result = {"ok": False, "status": "table_read_failed", "error": f"{type(exc).__name__}: {exc}"}
    return _f.dumps_result(result)


__all__ = ["positive_negative_case_read"]
