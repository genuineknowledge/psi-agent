"""Feishu/Lark drive tools — post comments, download files, upload files.

What is left here is what the ``feishu-drive`` skill's endpoint table cannot express.
Reading comments and deleting a file are plain requests and moved into that table; these
four are not requests-with-arguments:

- The two comment *writes* carry a nested body — a comment's text sits inside
  ``reply_list.replies[].content.elements[]``, and a reply's @-mention is a ``person``
  element that must come *before* the text run. Validation looks fields up by top-level
  key and cannot index into an array, so a tabled version would leave the comment text
  unchecked, and Feishu answers ``code: 0`` for a malformed ``elements`` — the comment
  lands empty and the call reports success.
- ``feishu_file_download`` produces a *file on disk*. It never goes through ``_invoke``:
  it reads bytes off the raw response and writes them, falling back from the bot's token
  to the user's authorization for files the bot cannot see. Its ``source_type`` picks
  between the two download endpoints, which serve disjoint things — ``medias`` what is
  inside a document, ``files`` a standalone resource file in Drive.
- ``feishu_drive_upload`` needs a real file handle in the body, which a JSON argument
  cannot carry.

Exporting a document (``feishu_doc_export``) and attaching a local image/file into one
(``feishu_doc_attach``) are the two other flows this domain cannot table, and live in
their own modules because both are multi-call chains rather than one request.

Pair the comment tools with ``feishu_doc_read`` (which reads the document body).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_drive_add_comment(
    file_token: str, file_type: str, content: str, user_key: str = "", identity: str = ""
) -> str:
    """Add a top-level (whole-document) comment on a Feishu/Lark document or file.

    To *read* comments instead, use ``feishu_api`` with
    ``GET /open-apis/drive/v1/files/:file_token/comments``.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        content: The comment text to post.
        user_key: The sender's open_id (from ``<feishu_context>``), identifying whose
            authorization and remembered ownership choice apply.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.add_comment_impl(file_token, file_type, content, user_key, identity))


async def feishu_drive_reply_comment(
    file_token: str,
    file_type: str,
    comment_id: str,
    content: str,
    at_user_id: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Post a reply on a Feishu comment thread, with an optional @-mention.

    Args:
        file_token: The file's token (from its URL).
        file_type: File type — one of docx, doc, sheet, bitable, file.
        comment_id: The comment thread's ID to reply under (get it from ``feishu_api``
            ``GET /open-apis/drive/v1/files/:file_token/comments``).
        content: The reply text.
        at_user_id: open_id/user_id to @-mention at the start of the reply (optional).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see add_comment).
    """
    return _f.dumps_result(
        await _f.reply_comment_impl(file_token, file_type, comment_id, content, at_user_id, user_key, identity)
    )


async def feishu_file_download(
    source: str,
    save_path: str,
    is_url: bool = False,
    user_key: str = "",
    source_type: str = "media",
) -> str:
    """Download a Feishu file/attachment to a local path.

    Three kinds of source, chosen with ``source_type``:

    - ``"media"`` (default): ``source`` is the file_token of something that lives
      **inside a document** — an image or an attachment block. Goes through
      ``drive/v1/medias/:file_token/download``.
    - ``"file"``: ``source`` is the file_token of a **standalone resource file in
      Drive**, e.g. a PDF someone uploaded. Goes through
      ``drive/v1/files/:file_token/download``.
    - ``"url"``: ``source`` is a direct URL. Approval-form attachments are direct URLs
      valid only ~12 hours — pass them here and download promptly. If the link has
      expired, re-read the approval instance for a fresh URL. (``is_url=True`` is the
      older spelling of this and still works.)

    ``media`` and ``file`` are **not** interchangeable — the wrong one is a 404 rather
    than a redirect. Neither serves Feishu's own online documents (docx / sheet /
    bitable): to get one of those as a local file use ``feishu_doc_export``.

    To read a PDF/attachment that lives in the user's wiki or drive: resolve it with
    ``feishu_api`` on ``GET /open-apis/wiki/v2/spaces/get_node`` → obj_token, download
    here with ``user_key`` so it's fetched as that user, then extract text with the
    ``ocr-and-documents`` skill (PyMuPDF).

    Args:
        source: A file_token, or a direct URL when source_type="url" / is_url=True.
        save_path: Local filesystem path to write the file to (parent dirs are created).
        is_url: Deprecated spelling of source_type="url"; True still forces URL mode.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it (for the
            token modes) to download as that user — needed for files the bot can't see;
            empty uses the bot's tenant token. Ignored for direct-URL downloads.
        source_type: ``"media"`` (inside a document, default), ``"file"`` (a resource
            file in Drive), or ``"url"`` (a direct link).
    """
    return _f.dumps_result(await _f.download_file_impl(source, save_path, is_url, user_key, source_type))


async def feishu_drive_upload(
    file_path: str,
    parent_node: str,
    parent_type: str = "explorer",
    file_name: str = "",
    extra_json: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Upload a local file (e.g. a learning video or a signed-proof image) to Feishu Drive.

    Handles files up to 20MB in one request and returns the new ``file_token`` (which you
    can then share by adding a permission member — see the ``feishu-permission`` skill —
    or reference as learning evidence).
    Larger files need Feishu's chunked upload flow, which this tool does not implement — it
    returns an error telling you the size.

    Args:
        file_path: Local path of the file to upload.
        parent_node: Target container token — for parent_type=explorer, the destination
            folder token (the segment in a feishu.cn/drive/folder/<token> URL).
        parent_type: Where it goes — "explorer" (a Drive folder, default) or a doc-attach
            type like "docx_image" / "docx_file" when attaching into a document.
        file_name: Name to store it as (defaults to the local file's name).
        extra_json: Optional JSON string for the endpoint's ``extra`` field (e.g. the target
            drive_route_token when attaching into a doc). Empty for a plain folder upload.
        user_key: The sender's open_id (from ``<feishu_context>``). A user-owned target
            folder generally needs that user's identity.
        identity: ``"user"`` / ``"bot"`` — who owns the result (see add_comment).
    """
    return _f.dumps_result(
        await _f.upload_media_impl(file_path, parent_type, parent_node, file_name, extra_json, user_key, identity)
    )
