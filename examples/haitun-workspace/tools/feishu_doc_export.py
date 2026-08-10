"""Feishu/Lark cloud-doc export — build a pdf/docx/xlsx/csv and save it locally.

Not an endpoint-table row, for two reasons at once. Exporting is three calls where the
middle one has to be *repeated* — Feishu builds the file asynchronously and only reports
progress through ``job_status`` — and the product is a file on disk rather than a JSON
response. A ``rules`` block can express neither polling nor writing bytes.

The three steps cannot be split across separate calls either: Feishu deletes the built
file **10 minutes** after the task finishes, so a ticket handed back to the caller would
usually be worthless by the time it came back. Hence one tool that runs the whole chain.

For downloading something that already exists as a file (an uploaded PDF, a document's
attachment) use ``feishu_file_download`` instead — export is only for turning Feishu's
own online documents into a portable format.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_doc_export(
    token: str,
    file_type: str,
    file_extension: str,
    save_path: str,
    sub_id: str = "",
    user_key: str = "",
) -> str:
    """Export a Feishu/Lark cloud document to a local pdf / docx / xlsx / csv file.

    Runs the whole export chain in one call: create the task, poll until Feishu has
    built the file, then download it to ``save_path``. Returns the path and byte count.

    Formats are paired to the source type, and a mismatch is refused locally rather
    than sent:

    - ``docx`` (new document) → ``pdf`` / ``docx``
    - ``doc`` (legacy document) → ``pdf`` / ``docx``
    - ``sheet`` (spreadsheet) → ``xlsx`` / ``csv``
    - ``bitable`` (base) → ``xlsx`` / ``csv``

    Exporting to ``csv`` needs ``sub_id``, because one csv can only hold one worksheet
    or one table out of the several a spreadsheet/base contains.

    A failure that will not improve with retrying says so: a document too large to
    export (``job_status`` 107) or with too many images (6000) is reported as such
    rather than polled again.

    Args:
        token: The document's own token, from its URL (``feishu.cn/docx/<token>``). For
            a doc in a wiki, resolve the node first (``feishu_api`` on
            ``GET /open-apis/wiki/v2/spaces/get_node``) and pass its ``obj_token``.
        file_type: What the source is — ``docx``, ``doc``, ``sheet`` or ``bitable``.
        file_extension: What to produce — ``pdf``, ``docx``, ``xlsx`` or ``csv``.
        save_path: Local filesystem path to write the exported file to (parent dirs are
            created).
        sub_id: Required only for a csv export — the spreadsheet's ``sheet_id`` or the
            base's ``table_id``. List a spreadsheet's sheet_ids with ``feishu_api`` on
            ``GET /open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query``.
        user_key: The sender's open_id (from ``<feishu_context>``). Pass it to export as
            that user — needed for documents the bot cannot see; empty uses the bot's
            tenant token.
    """
    return _f.dumps_result(await _f.export_doc_impl(token, file_type, file_extension, save_path, sub_id, user_key))
