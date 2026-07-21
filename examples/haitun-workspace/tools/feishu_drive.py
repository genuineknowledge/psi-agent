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


async def feishu_drive_add_comment(file_token: str, file_type: str, content: str, user_key: str = "") -> str:
    """Add a top-level (whole-document) comment on a Feishu/Lark document or file.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        content: The comment text to post.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to comment
            as that user when the file is user-owned; empty uses the bot's tenant token.
    """
    return _f.dumps_result(await _f.add_comment_impl(file_token, file_type, content, user_key))


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
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str = "", user_key: str = ""
) -> str:
    """Post a reply on a Feishu comment thread, with an optional @-mention.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        comment_id: The comment thread's ID to reply under.
        content: The reply text.
        at_user_id: open_id/user_id to @-mention at the start of the reply (optional).
        user_key: The sender's open_id; pass it to reply as that user (see add_comment).
    """
    return _f.dumps_result(
        await _f.reply_comment_impl(file_token, file_type, comment_id, content, at_user_id, user_key)
    )


async def feishu_file_download(
    source: str, save_path: str, is_url: bool = False, user_key: str = "", source_type: str = "auto"
) -> str:
    """Download a Feishu file/attachment to a local path.

    Two sources:
    - is_url=False (default): ``source`` is a file token. ``source_type`` picks the
      endpoint — "file" = a standalone cloud-space file (PDF / uploaded file, via
      /drive/v1/files), "media" = 素材 embedded inside a cloud doc (via /drive/v1/medias),
      "auto" (default) = try files first then media. **A PDF found via search / in a
      wiki or drive is a cloud file → auto (or "file") is correct; medias returns empty
      for it.**
    - is_url=True: ``source`` is a direct URL. Approval-form attachments are direct
      URLs valid only ~12 hours — pass them here and download promptly. If the link
      has expired, re-read the approval instance for a fresh URL.

    To read a PDF/attachment in the user's wiki or drive: resolve it (e.g.
    ``feishu_wiki_get_node(token, user_key)`` → obj_token), download here with ``user_key``
    so it's fetched as that user, then extract text with the ``ocr-and-documents`` skill.

    Args:
        source: A file token, or a direct URL when is_url=True.
        save_path: Local filesystem path to write the file to (parent dirs are created).
        is_url: True if source is a direct URL, False if it is a file token.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it (is_url=False)
            to download as that user — needed for files the bot can't see; empty falls
            back to the single authorized user, then tenant token. Ignored for URLs.
        source_type: "auto" (default) / "file" / "media" — see above. Use "auto" unless
            you specifically need a doc's embedded 素材 ("media").
    """
    return _f.dumps_result(await _f.download_file_impl(source, save_path, is_url, user_key, source_type))


async def feishu_drive_delete_file(file_token: str, file_type: str, user_key: str = "") -> str:
    """Delete a Feishu/Lark cloud file or document (moves it to the recycle bin).

    The delete is recoverable (goes to trash, not permanent). The caller must be the
    file's owner, or hold edit/full-access on its parent folder — so for a file the
    user owns, pass their ``user_key`` to delete as that user.

    To delete a document that lives inside a wiki knowledge base: first resolve it
    with ``feishu_wiki_get_node(token)`` to get ``obj_token`` + ``obj_type``, then
    call this with ``file_token=obj_token`` and ``file_type=obj_type`` (Feishu has no
    standalone "delete wiki node" API — deleting the underlying doc removes it).

    Args:
        file_token: The file/document token (from its URL), or a wiki node's obj_token.
        file_type: One of file, docx, doc, sheet, bitable, mindnote, slides, folder,
            shortcut. Deleting a folder is async and returns a task_id.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to delete as
            that user (needed for user-owned files/wikis); empty uses the bot's tenant token.
    """
    return _f.dumps_result(await _f.delete_file_impl(file_token, file_type, user_key))
