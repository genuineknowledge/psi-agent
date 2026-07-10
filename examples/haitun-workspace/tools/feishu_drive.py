"""Feishu/Lark drive comment tools — read and post comments on cloud documents.

Whole-document comments on a Feishu file (docx/doc/sheet/bitable). Use these to
review a doc's discussion, leave feedback, or reply in an existing thread.
Pair with ``feishu_doc_read`` (which reads the document body).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_drive_add_comment(file_token: str, file_type: str, content: str) -> str:
    """Add a top-level (whole-document) comment on a Feishu/Lark document or file.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        content: The comment text to post.
    """
    return _f.dumps_result(await _f.add_comment_impl(file_token, file_type, content))


async def feishu_drive_list_comments(file_token: str, file_type: str, page_size: int = 50, page_token: str = "") -> str:
    """List whole-document comments on a Feishu/Lark file.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        page_size: Max comments to return (default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.list_comments_impl(file_token, file_type, page_size, page_token))


async def feishu_drive_list_comment_replies(
    file_token: str, file_type: str, comment_id: str, page_size: int = 50, page_token: str = ""
) -> str:
    """List replies on a specific Feishu comment thread (whole-doc or local-selection).

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        comment_id: The comment thread's ID (from feishu_drive_list_comments).
        page_size: Max replies to return (default 50).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.list_comment_replies_impl(file_token, file_type, comment_id, page_size, page_token))


async def feishu_drive_reply_comment(
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str = ""
) -> str:
    """Post a reply on a Feishu comment thread, with an optional @-mention.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        comment_id: The comment thread's ID to reply under.
        content: The reply text.
        at_user_id: open_id/user_id to @-mention at the start of the reply (optional).
    """
    return _f.dumps_result(await _f.reply_comment_impl(file_token, file_type, comment_id, content, at_user_id))
