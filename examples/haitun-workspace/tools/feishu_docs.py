"""Feishu/Lark document search — find cloud docs by keyword/name.

Searches the docs the authorizing user can access and returns candidates
(title, token, obj_type). Feed a result's ``obj_type`` + ``token`` into
``feishu_doc_read`` to read the full body.

Search needs a user_access_token, so authorize first via ``feishu_auth_start`` /
``feishu_auth_complete`` (a not-authorized result says so with ``need_auth``).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_docs_search(search_key: str, count: int = 20, offset: int = 0, docs_types: str = "") -> str:
    """Search Feishu/Lark cloud documents by keyword and return matching docs.

    Returns candidates as ``{title, token, obj_type, owner_id}``. Read a result's
    full text with ``feishu_doc_read(obj_type, token)`` (for docx/doc/sheet).
    Requires prior user authorization (see ``feishu_auth_start``).

    Args:
        search_key: Keyword to search document titles/content for.
        count: Max results to return (0-50, default 20).
        offset: Pagination offset (offset + count < 200).
        docs_types: Comma-separated type filter — doc, sheet, slides, bitable,
            mindnote, file. Empty = all types.
    """
    return _f.dumps_result(await _f.search_docs_impl(search_key, count, offset, docs_types))
