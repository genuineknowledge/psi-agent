"""Feishu/Lark document tools — read, create, and write cloud documents.

- ``feishu_doc_read`` — read a doc's plain-text body (docx/doc/sheet).
- ``feishu_doc_create`` — create a new standalone docx cloud document.
- ``feishu_doc_append_content`` — append headings/paragraphs to a docx body
  (also works on the docx behind a wiki node via its ``obj_token``).

Pair with the feishu_wiki_* tools to create knowledge-base docs and the
feishu_drive_* tools to read or leave comments.
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


async def feishu_doc_create(title: str, folder_token: str = "") -> str:
    """Create a new (empty) Feishu/Lark docx cloud document.

    Creates a standalone document in the cloud drive (not attached to a wiki/
    knowledge base — for that use ``feishu_wiki_create_doc``). Returns the new
    ``document_id`` and its URL. Fill in the body afterwards with
    ``feishu_doc_append_content(document_id, content)``.

    Args:
        title: The document title (plain text, 1-800 chars).
        folder_token: Optional target folder token; empty places it in the root.
    """
    return _f.dumps_result(await _f.create_docx_impl(title, folder_token))


async def feishu_doc_append_content(document_id: str, content: str) -> str:
    """Append body content (headings + paragraphs) to a Feishu/Lark docx document.

    Writes into the document created by ``feishu_doc_create`` or the docx behind a
    wiki node (pass that node's ``obj_token`` as ``document_id``). ``content`` is
    plain text or light Markdown: a line starting with ``# ``..``###### `` becomes
    a heading (levels 1-6), every other non-blank line becomes a paragraph; blank
    lines are skipped. Blocks are appended to the end in batches of 50.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        content: The text/Markdown body to append.
    """
    return _f.dumps_result(await _f.append_doc_content_impl(document_id, content))
