"""Read positive-negative ledger records from a Feishu private-chat request."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from _positive_negative_list import reader, runtime


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
