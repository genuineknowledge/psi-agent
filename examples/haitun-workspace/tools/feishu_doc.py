"""Feishu/Lark document reader — read the full text of a cloud document.

Given a file's type and token (both visible in its URL), return the document's
plain-text body. Supports new docs (docx), legacy docs (doc), and spreadsheets
(sheet). Pair with the feishu_drive_* tools to read or leave comments.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_doc_read(file_type: str, token: str, max_chars: int = 20000) -> str:
    """Read the full text content of a Feishu/Lark document (Docx, Doc, or Sheet).

    Given the document's file_type and token (both from its URL), fetch the body
    as plain text. For a sheet, every worksheet is read and tab-separated.

    Args:
        file_type: One of docx (new docs), doc (legacy docs), sheet (spreadsheets).
        token: The document/spreadsheet token from its URL.
        max_chars: Max characters to return (default 20000; guards the context window).
    """
    return _f.dumps_result(await _f.read_doc_impl(file_type, token, max_chars))
