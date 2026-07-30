"""Feishu/Lark document tools — read, create, and write cloud documents.

- ``feishu_doc_read`` — read a doc's plain-text body (docx/doc/sheet).
- ``feishu_doc_create`` — create a new standalone docx cloud document.
- ``feishu_doc_append_content`` — append headings/paragraphs to a docx body
  (also works on the docx behind a wiki node via its ``obj_token``).
- ``feishu_doc_append_table`` — append a native Feishu table (rows/columns).
- ``feishu_doc_append_flowchart`` — append a flowchart (rendered as a table,
  since Feishu's API can't draw real diagrams).
- ``feishu_doc_append_swimlane`` — append a swimlane/cross-functional diagram
  (rendered as a lanes-by-stages table).
- ``feishu_doc_list_blocks`` — list the body's blocks with their ``block_id``s.
- ``feishu_doc_update_block`` — rewrite one block's text in place.
- ``feishu_doc_delete_blocks`` — delete blocks by ``block_id``.

The last three are the revise-in-place trio: the append tools can only add, so
fixing a wrong paragraph means listing blocks to find its ``block_id``, then
updating or deleting that block.

Pair with the feishu_wiki_* tools to create knowledge-base docs and the
feishu_drive_* tools to read or leave comments.
"""

from __future__ import annotations

# ruff: noqa: E402
# RUF002: these docstrings are read by the agent as prose, and the caption examples quote
# the exact characters it has to write, so the full-width CJK punctuation in them is
# correct typography here rather than an ASCII typo.
# ruff: noqa: RUF002
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


async def feishu_doc_create(title: str, folder_token: str = "", user_key: str = "", identity: str = "") -> str:
    """Create a new (empty) Feishu/Lark docx cloud document.

    Creates a standalone document in the cloud drive (not attached to a wiki/
    knowledge base — for that use ``feishu_wiki_create_doc``). Returns the new
    ``document_id`` and its URL. Fill in the body afterwards with
    ``feishu_doc_append_content(document_id, content)``.

    Args:
        title: The document title (plain text, 1-800 chars).
        folder_token: Optional target folder token; empty places it in the root.
        user_key: The sender's open_id (from ``<feishu_context>``), identifying whose
            authorization and remembered ownership choice apply.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.create_docx_impl(title, folder_token, user_key, identity))


async def feishu_doc_append_content(document_id: str, content: str, user_key: str = "", identity: str = "") -> str:
    """Append body content (headings + paragraphs) to a Feishu/Lark docx document.

    Writes into the document created by ``feishu_doc_create`` or the docx behind a
    wiki node (pass that node's ``obj_token`` as ``document_id``). ``content`` is
    plain text or light Markdown: a line starting with ``# ``..``###### `` becomes
    a heading (levels 1-6), every other non-blank line becomes a paragraph; blank
    lines are skipped. Blocks are appended to the end in batches of 50.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        content: The text/Markdown body to append.
        user_key: The sender's open_id (from ``<feishu_context>``). Writing into a
            user-owned wiki generally requires their identity, since the bot isn't a
            collaborator there.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.append_doc_content_impl(document_id, content, user_key, identity))


async def feishu_doc_append_table(
    document_id: str,
    rows_json: str,
    header_row: bool = True,
    column_width_json: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> str:
    """Append a native, editable Feishu table to a docx document.

    Use this whenever the user wants a real table in a Feishu doc — plain
    ``feishu_doc_append_content`` can only write headings/paragraphs, so a table
    typed as text there would NOT render as a table. This creates a true table
    block (rows x columns) via the docx descendant API.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        rows_json: A JSON 2-D array of cell values, one inner array per row, e.g.
            '[["姓名","部门","评分"],["张三","研发","4"],["李四","市场","5"]]'.
            Rows are padded to the widest row; numbers/bools become text.
        header_row: Style the first row as a header (default true).
        column_width_json: Optional JSON array of per-column pixel widths, e.g. '[120,200,80]'.
        user_key: The sender's open_id (from ``<feishu_context>``). Writing into a
            user-owned wiki generally requires their identity, since the bot isn't a
            collaborator there.
        identity: Who owns the result: ``"user"`` (this person — needs their
            authorization) or ``"bot"`` (the bot). Omit to use the choice remembered
            for this ``user_key``; if they have never been asked, the tool does
            nothing and returns ``need_identity_choice`` so you can ask them.
        caption: Optional table title, written as a numbered "表 N：…" line **above** the
            table (that's where a table's title belongs; figures caption below). Write
            the text only — "客户明细", not "表2：客户明细" — the number is added
            automatically, continuing the document's own 表 sequence.
        auto_number: Number the caption from the document's existing 表 captions
            (default true). Set false only when the caller manages numbering itself.
    """
    return _f.dumps_result(
        await _f.append_doc_table_impl(
            document_id,
            rows_json,
            header_row,
            column_width_json,
            user_key,
            identity,
            caption=caption,
            auto_number=auto_number,
        )
    )


async def feishu_doc_append_flowchart(
    document_id: str,
    steps_json: str,
    title: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> str:
    """Append a flowchart to a docx — rendered as a single-column table of steps.

    Feishu's open API can NOT draw a real flowchart/diagram block (block_type 21 is
    an empty canvas the API can't populate), so a genuine editable representation is
    a top-to-bottom table where each step is a row joined by ↓ arrows. Use this when
    the user asks for a 流程图/flowchart inside a Feishu doc.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        steps_json: A JSON array of step labels in order, e.g.
            '["提交申请","主管审批","财务复核","归档"]'.
        title: Optional heading cell shown at the top of the flowchart.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see append_content).
        caption: Optional numbered "表 N：…" line above it — text only, no "表N：" prefix.
        auto_number: Number the caption from the document's existing 表 captions (default true).
    """
    return _f.dumps_result(
        await _f.append_doc_flowchart_impl(
            document_id, steps_json, title, user_key, identity, caption=caption, auto_number=auto_number
        )
    )


async def feishu_doc_append_swimlane(
    document_id: str,
    lanes_json: str,
    stages_json: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> str:
    """Append a swimlane / cross-functional diagram to a docx — rendered as a table.

    Feishu's open API can't draw a real swimlane diagram, so this renders one as a
    table whose columns are the lanes (角色/部门) and rows are the stages/activities —
    a faithful, editable equivalent. Use this for 泳道图/swimlane requests.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        lanes_json: EITHER a JSON object mapping each lane to its ordered activities,
            e.g. '{"客户":["下单","付款"],"客服":["接单"],"仓库":["发货"]}' (auto-gridded),
            OR a JSON array of lane (column) names, e.g. '["客户","客服","仓库"]' — in which
            case pass the body rows in ``stages_json``.
        stages_json: Only when ``lanes_json`` is an array: a JSON 2-D array of body rows
            (each row aligns to the lane columns), e.g. '[["下单","接单","发货"]]'.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result (see append_content).
        caption: Optional numbered "表 N：…" line above it — text only, no "表N：" prefix.
        auto_number: Number the caption from the document's existing 表 captions (default true).
    """
    return _f.dumps_result(
        await _f.append_doc_swimlane_impl(
            document_id, lanes_json, stages_json, user_key, identity, caption=caption, auto_number=auto_number
        )
    )


async def feishu_doc_list_blocks(document_id: str, max_blocks: int = 200, user_key: str = "") -> str:
    """List the blocks of a Feishu/Lark docx, with each one's ``block_id`` and text.

    Call this before editing anything: ``feishu_doc_update_block`` and
    ``feishu_doc_delete_blocks`` address content by ``block_id``, and this is the only
    way to learn those ids. Each entry is ``{block_id, block_type, type_name, parent_id,
    text, editable_text}`` — ``text`` is a preview (trimmed at 200 chars), and
    ``editable_text`` says whether ``update_block`` can rewrite it (false for
    image/table/divider blocks). To read the whole body as prose instead, use
    ``feishu_doc_read``.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        max_blocks: Max blocks to return (default 200, cap 2000); ``truncated`` in the
            result says whether the document has more.
        user_key: The sender's open_id (from ``<feishu_context>``), so a doc only that
            person can see is read with their authorization.
    """
    return _f.dumps_result(await _f.list_doc_blocks_impl(document_id, max_blocks, user_key))


async def feishu_doc_update_block(
    document_id: str, block_id: str, text: str, user_key: str = "", identity: str = ""
) -> str:
    """Rewrite the text of one block in a Feishu/Lark docx, in place.

    This is how a doc gets *corrected* rather than appended to: the block keeps its
    id and its type (a heading stays a heading, a bullet stays a bullet), only the
    text is replaced. Get ``block_id`` from ``feishu_doc_list_blocks``. Blocks with no
    text runs (image, table, divider) can't be updated this way — replace them by
    deleting and re-appending. Note the text is *replaced*, not merged: pass the full
    new text of that block.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        block_id: The block to rewrite (from ``feishu_doc_list_blocks``).
        text: The block's complete new text.
        user_key: The sender's open_id (from ``<feishu_context>``). Editing a doc in a
            user-owned wiki generally needs their identity.
        identity: Who acts: ``"user"`` (this person — needs their authorization) or
            ``"bot"``. Omit to use the choice remembered for this ``user_key``; if they
            have never been asked, the tool does nothing and returns
            ``need_identity_choice`` so you can ask them.
    """
    return _f.dumps_result(await _f.update_doc_block_impl(document_id, block_id, text, user_key, identity))


async def feishu_doc_delete_blocks(
    document_id: str,
    block_ids_json: str,
    parent_block_id: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Delete one or more blocks from a Feishu/Lark docx, by ``block_id``.

    Removes whole blocks (paragraph, heading, table, image, …) — the way to drop a
    section that shouldn't be there. Get the ids from ``feishu_doc_list_blocks``. The
    ids are resolved to their current positions at delete time and removed
    bottom-up, so a batch delete doesn't shift itself off target. Deleting is not
    undoable through the API, so confirm the target text with ``list_blocks`` first.

    Args:
        document_id: The docx document_id (or a wiki node's obj_token).
        block_ids_json: JSON array of block_ids to delete, e.g. '["doxcnAAA","doxcnBBB"]'.
        parent_block_id: The blocks' parent, when they are *nested* (e.g. inside a
            table cell or callout — see ``parent_id`` in the list result). Empty means
            the document root, which is where top-level paragraphs live.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who acts (see ``feishu_doc_update_block``).
    """
    return _f.dumps_result(
        await _f.delete_doc_blocks_impl(document_id, block_ids_json, parent_block_id, user_key, identity)
    )
