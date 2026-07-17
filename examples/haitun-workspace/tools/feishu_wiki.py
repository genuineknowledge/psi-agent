"""Feishu/Lark wiki tools — resolve a wiki node to its underlying document.

A Feishu wiki URL (``.../wiki/<node_token>``) is a shell: the real content lives
in an underlying docx / sheet / bitable / etc. This resolves the wiki node token
to its ``obj_token`` + ``obj_type`` so you can then read the body:
if ``obj_type`` is docx/doc/sheet, pass ``obj_token`` to ``feishu_doc_read``.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET`` and node read access.
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
