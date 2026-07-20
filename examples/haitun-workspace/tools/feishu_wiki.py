"""Feishu/Lark wiki tools — create docs in a knowledge base + resolve nodes.

A Feishu wiki URL (``.../wiki/<node_token>``) is a shell: the real content lives
in an underlying docx / sheet / bitable / etc.

- ``feishu_wiki_list_spaces`` — list accessible knowledge bases (to get a space_id).
- ``feishu_wiki_create_doc`` — create a new docx document inside a knowledge base.
- ``feishu_wiki_get_node`` — resolve a wiki node token to its ``obj_token`` +
  ``obj_type`` so you can read the body (docx/doc/sheet → ``feishu_doc_read``).

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``; creating needs edit
permission on the target space / parent node.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_wiki_get_node(token: str) -> str:
    """Resolve a wiki node token to its underlying document.

    Given the token from a wiki URL (e.g. ``NFOnwDvrPiVjs5k0xXxchuwWnru`` in
    ``.../wiki/NFOnwDvrPiVjs5k0xXxchuwWnru``), returns ``obj_token``, ``obj_type``,
    and ``title``. Then read the content with ``feishu_doc_read(obj_type, obj_token)``
    when ``obj_type`` is docx/doc/sheet.

    Args:
        token: The wiki node token (the segment after ``/wiki/`` in the URL).
    """
    return _f.dumps_result(await _f.get_wiki_node_impl(token))


async def feishu_wiki_list_spaces(page_size: int = 20, page_token: str = "") -> str:
    """List the Feishu/Lark wiki (knowledge base) spaces the app/user can access.

    Returns each space's ``space_id`` and ``name``. You need a ``space_id`` before
    creating a document with ``feishu_wiki_create_doc``. Note: this does not return
    the personal "My Library". Paginate with the returned ``page_token`` while
    ``has_more`` is true.

    Args:
        page_size: Items per page (1-50, default 20).
        page_token: Token from a previous page; empty for the first page.
    """
    return _f.dumps_result(await _f.list_wiki_spaces_impl(page_size, page_token))


async def feishu_wiki_create_doc(space_id: str, title: str, parent_node_token: str = "") -> str:
    """Create a new document (docx node) inside a Feishu/Lark wiki knowledge base.

    Creates a wiki node backed by a new docx. Returns the ``node_token`` (the wiki
    entry), ``obj_token`` (the underlying docx's document_id) and the wiki URL. To
    write the body, pass ``obj_token`` to ``feishu_doc_append_content``. Full flow:
    ``feishu_wiki_list_spaces`` → ``feishu_wiki_create_doc`` → ``feishu_doc_append_content``.

    Args:
        space_id: The knowledge base space_id (from ``feishu_wiki_list_spaces``).
        title: The document title.
        parent_node_token: Optional parent node token; empty creates a top-level node.
    """
    return _f.dumps_result(await _f.create_wiki_node_impl(space_id, title, "docx", parent_node_token))
