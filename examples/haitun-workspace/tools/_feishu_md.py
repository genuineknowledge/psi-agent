"""Markdown → native Feishu docx blocks, through Feishu's own converter.

``feishu_doc_append_content`` used to map each line to one of two blocks: a heading or
a paragraph. Anything else Markdown can express — a ``| a | b |`` table, a ``- `` list,
``**bold**``, a fenced code block — was written as the *literal source text*, so a doc
"with a table in it" arrived as rows of pipes and dashes: no grid, nothing draggable or
resizable, and no way to sort or edit a column.

Feishu converts Markdown itself: ``POST docx/v1/documents/blocks/convert`` takes
``{content_type: "markdown", content}`` and returns a flat ``blocks`` list plus the
``first_level_block_ids`` that attach at the insert point — exactly the shape the
``/descendant`` endpoint writes. So the conversion is not reimplemented here; this module
is the plumbing between the two endpoints, plus the three things the pair gets wrong:

1. **``merge_info`` must be stripped.** ``convert`` emits it on every table property;
   ``/descendant`` rejects the very payload its sibling produced with ``1770001 invalid
   param``. Measured by bisecting the payload — dropping it is the only change a table
   needs. The table's ``children`` must *stay* (dropping those gives ``1770041 open
   schema mismatch``).
2. **A converted table has no ``header_row``.** Every Markdown table has a header row by
   syntax, so it is set here; otherwise the first row renders as ordinary cells.
3. **``/descendant`` takes at most 1000 blocks per call** (measured: 1000 succeeds, 1001
   fails with ``99992402``). Since a table costs ``2 x rows x columns`` blocks, a 61x12
   table is 1465 — over the cap on its own, and a table cannot be split across calls. So
   an oversized table is created with as many rows as fit, then grown with
   ``insert_table_row`` and filled through ``blocks/batch_update``.

Plain prose (no Markdown beyond headings) is left on the old local path: it needs no
round-trip, and one-paragraph-per-line is how existing haitun docs are written.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f  # noqa: E402
from lark_channel.core.enum import AccessTokenType, HttpMethod  # noqa: E402
from lark_channel.core.model import BaseRequest  # noqa: E402

TABLE_BLOCK = 31
TABLE_CELL_BLOCK = 32
IMAGE_BLOCK = 27
TEXT_BLOCK = 2

# Measured, not documented: /descendant accepts 1000 descendants and refuses 1001 with
# 99992402 field validation failed. batch_update's 100 is the documented per-call cap.
DESCENDANT_MAX_BLOCKS = 1000
BATCH_UPDATE_MAX = 100

# A heading is deliberately *not* in this list: "# 标题" plus paragraphs is what the old
# local path already handled correctly, and routing those through the API would add a
# network round-trip to the common case of writing plain prose — and Feishu's converter
# joins single newlines into one paragraph, which would silently reflow prose written one
# line per paragraph.
_RICH_MARKDOWN_PATTERNS = (
    # A table row: at least two pipes with content between them.
    re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE),
    # Bullet / numbered / task list items.
    re.compile(r"^\s*([-*+]|\d+[.)])\s+\S", re.MULTILINE),
    # Blockquote.
    re.compile(r"^\s*>\s+\S", re.MULTILINE),
    # Fenced code block.
    re.compile(r"^\s*(```|~~~)", re.MULTILINE),
    # Horizontal rule.
    re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$", re.MULTILINE),
    # Inline emphasis / code / strikethrough / link / image.
    re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__|~~[^~\n]+~~|`[^`\n]+`|!?\[[^\]\n]*\]\([^)\n]*\)"),
)


def has_rich_markdown(content: str) -> bool:
    """True when ``content`` uses Markdown beyond headings and plain paragraphs.

    This is the routing decision: rich Markdown goes through Feishu's converter so it
    becomes real tables/lists/styled runs, while plain prose stays on the cheap local
    path. False negatives only cost fidelity on constructs nobody wrote; false positives
    only cost one API call, so the patterns are deliberately literal rather than a full
    Markdown grammar.
    """
    return any(p.search(content or "") for p in _RICH_MARKDOWN_PATTERNS)


def build_convert_request(content: str) -> BaseRequest:
    """``POST docx/v1/documents/blocks/convert`` — Markdown in, docx blocks out.

    Tenant-only: converting is a pure transformation that touches no document, so it
    needs no user identity even when the *write* that follows does.
    """
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents/blocks/convert"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"content_type": "markdown", "content": content}
    return req


def sanitize_converted(blocks: list[dict[str, Any]], *, header_row: bool = True) -> list[dict[str, Any]]:
    """Make ``convert``'s output acceptable to ``/descendant``.

    Two edits, both required (see the module docstring): drop the ``parent_id`` the
    converter echoes back (it names temporary ids that mean nothing at the insert point)
    and ``merge_info`` from every table property, and set ``header_row`` on tables since
    a Markdown table always has one and the converter never says so.
    """
    out: list[dict[str, Any]] = []
    for block in blocks:
        clean = {k: v for k, v in block.items() if k != "parent_id"}
        if clean.get("block_type") == TABLE_BLOCK and isinstance(clean.get("table"), dict):
            table = dict(clean["table"])
            prop = {k: v for k, v in (table.get("property") or {}).items() if k != "merge_info"}
            if header_row:
                prop["header_row"] = True
            table["property"] = prop
            clean["table"] = table
        out.append(clean)
    return out


def _children_of(block: dict[str, Any]) -> list[str]:
    """Every block id this block owns — its ``children`` plus a table's ``cells``.

    Both are followed because they are not the same list in the payload we build: a
    clipped table keeps ``cells`` and ``children`` in sync, but reading only one of them
    would drop cells from a subtree and produce ``1770041 open schema mismatch``.
    """
    ids = list(block.get("children") or [])
    table = block.get("table")
    if isinstance(table, dict):
        for cell in table.get("cells") or []:
            if cell not in ids:
                ids.append(cell)
    return ids


def subtree(root_id: str, by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """``root_id``'s block and everything under it, parents before children.

    Order is not required by the API (it resolves ids anywhere in the list) but keeps a
    batch readable when a request has to be inspected after a failure.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        bid = stack.pop(0)
        if bid in seen or bid not in by_id:
            continue
        seen.add(bid)
        block = by_id[bid]
        out.append(block)
        stack = _children_of(block) + stack
    return out


def group_into_batches(
    first_level_ids: list[str],
    by_id: dict[str, dict[str, Any]],
    *,
    max_blocks: int | None = None,
) -> list[tuple[list[str], list[dict[str, Any]]]]:
    """Split the document into ``(children_id, descendants)`` calls of <= ``max_blocks``.

    Batching is by *whole first-level block*: a paragraph is one block but a table is
    ``1 + 2 x rows x columns`` of them, and half a table is not a writable payload. Blocks
    are kept in document order so appending batch after batch reproduces the document.

    A single subtree that exceeds the cap on its own is returned as its own oversized
    batch — the caller (``append_markdown``) handles that case by clipping and growing,
    which only tables support. Silently dropping it would lose the user's content.
    """
    cap = DESCENDANT_MAX_BLOCKS if max_blocks is None else max_blocks
    batches: list[tuple[list[str], list[dict[str, Any]]]] = []
    ids: list[str] = []
    blocks: list[dict[str, Any]] = []
    for fid in first_level_ids:
        chunk = subtree(fid, by_id)
        if not chunk:
            continue
        if ids and len(blocks) + len(chunk) > cap:
            batches.append((ids, blocks))
            ids, blocks = [], []
        ids.append(fid)
        blocks.extend(chunk)
    if ids:
        batches.append((ids, blocks))
    return batches


# ── Oversized tables: create what fits, then grow ───────────────────────────────
# A table costs 1 + 2 x rows x columns blocks (the table, one cell block each, one text
# block inside each cell), so /descendant's 1000-block ceiling is reached at ~41 rows of
# 12 columns. Feishu offers no "add rows to this payload" call, so the table is created
# with the rows that fit and the rest are appended one PATCH at a time
# (insert_table_row), then filled in batch_update batches of 100. Measured on a 61x12
# table: 20 inserts ~11s, 240 cells filled ~2s.


def cell_text(cell_id: str, by_id: dict[str, dict[str, Any]]) -> str:
    """The plain text inside one converted table cell (its text child's runs joined)."""
    cell = by_id.get(cell_id) or {}
    for child in cell.get("children") or []:
        block = by_id.get(child) or {}
        payload = block.get("text")
        if isinstance(payload, dict):
            return "".join(
                str(e.get("text_run", {}).get("content", ""))
                for e in payload.get("elements") or []
                if isinstance(e, dict)
            )
    return ""


def rows_that_fit(columns: int, *, max_blocks: int | None = None) -> int:
    """How many rows of ``columns`` columns fit in one /descendant call (>= 1).

    ``1`` for the table block itself, then two blocks per cell. At least one row is
    always attempted: a table too wide for even a single row is Feishu's error to give,
    not ours to guess at.
    """
    cap = DESCENDANT_MAX_BLOCKS if max_blocks is None else max_blocks
    per_row = max(1, 2 * columns)
    return max(1, (cap - 1) // per_row)


def clip_table(
    table: dict[str, Any], keep_rows: int, by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """Split an oversized converted table into a creatable head and the text of its tail.

    Returns ``(descendants, tail_rows)``: the blocks for a ``keep_rows``-row table
    (row-major, so the header survives) and the remaining rows as plain cell strings, to
    be re-applied after the table has been grown. Text is carried rather than block ids
    because the grown rows are new blocks with ids only Feishu knows.
    """
    prop = dict((table.get("table") or {}).get("property") or {})
    columns = int(prop.get("column_size") or 0)
    cells = list((table.get("table") or {}).get("cells") or [])
    kept = cells[: keep_rows * columns] if columns else cells
    head = {
        **{k: v for k, v in table.items() if k not in ("table", "children", "parent_id")},
        "children": list(kept),
        "table": {"cells": list(kept), "property": {**prop, "row_size": keep_rows}},
    }
    descendants: list[dict[str, Any]] = [head]
    for cell_id in kept:
        cell = by_id.get(cell_id)
        if cell is None:
            continue
        descendants.append({k: v for k, v in cell.items() if k != "parent_id"})
        for child in cell.get("children") or []:
            block = by_id.get(child)
            if block is not None:
                descendants.append({k: v for k, v in block.items() if k != "parent_id"})
    tail: list[list[str]] = []
    if columns:
        rest = cells[keep_rows * columns :]
        for start in range(0, len(rest), columns):
            tail.append([cell_text(cid, by_id) for cid in rest[start : start + columns]])
    return descendants, tail


def build_insert_row_request(document_id: str, block_id: str, row_index: int = -1) -> BaseRequest:
    """``PATCH …/blocks/:block_id`` with ``insert_table_row`` — one more row on a table.

    ``row_index`` -1 appends. Feishu creates the row's cells *and* an empty text block in
    each, which is what makes the fill step a text update rather than a block creation.
    """
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"insert_table_row": {"row_index": row_index}}
    return req


def build_batch_update_request(document_id: str, requests: list[dict[str, Any]]) -> BaseRequest:
    """``PATCH …/blocks/batch_update`` — up to 100 block edits in one call."""
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/batch_update"
    req.paths["document_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"requests": requests}
    return req


def fill_requests(cell_ids: list[str], texts: list[str], children: dict[str, list[str]]) -> list[dict[str, Any]]:
    """``update_text_elements`` edits that write ``texts`` into ``cell_ids``' text blocks.

    A cell block holds no text itself; the run lives in the paragraph Feishu created
    inside it, so each edit targets that child. Cells whose child is unknown, or whose
    text is empty, are skipped — a grown row is already blank, so there is nothing to say.
    """
    out: list[dict[str, Any]] = []
    for cell_id, text in zip(cell_ids, texts, strict=False):
        if not text:
            continue
        kids = children.get(cell_id) or []
        if not kids:
            continue
        out.append({"block_id": kids[0], "update_text_elements": {"elements": [{"text_run": {"content": text}}]}})
    return out


async def convert_markdown(content: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """Markdown → ``{blocks, first_level_ids}`` via Feishu's converter, ready to write.

    ``prefer="tenant"``: the conversion creates nothing and owns nothing, so it must not
    trigger an ownership question — only the write that follows does.
    """
    res = await _f.invoke_request(build_convert_request(content), user_key=user_key, prefer="tenant", identity=identity)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    blocks = data.get("blocks")
    first = data.get("first_level_block_ids")
    if not isinstance(blocks, list) or not isinstance(first, list) or not first:
        return _f.error_result("Feishu converted the Markdown but returned no blocks.")
    return {"ok": True, "blocks": sanitize_converted(blocks), "first_level_ids": [str(i) for i in first]}


async def _grow_table(
    document_id: str,
    real_table_id: str,
    tail_rows: list[list[str]],
    keep_rows: int,
    columns: int,
    user_key: str,
    identity: str,
) -> dict[str, Any]:
    """Append ``tail_rows`` to an already-created table, then write their text.

    Rows are inserted one call at a time (Feishu has no batch row-insert: batch_update
    with several ``insert_table_row`` entries is refused with 1770001), and the fill is
    read back from the document rather than predicted, because the ids of the new cells
    and of the paragraph inside each are assigned by Feishu.
    """
    for _ in tail_rows:
        res = await _f.invoke_request(
            build_insert_row_request(document_id, real_table_id),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "rows_grown": False}
    listed = await _f.list_all_blocks(document_id, user_key=user_key, identity=identity)
    if not listed["ok"]:
        return {**listed, "rows_grown": True, "tail_filled": False}
    by_id = {str(b.get("block_id", "")): b for b in listed["blocks"] if isinstance(b, dict)}
    table = by_id.get(real_table_id) or {}
    cells = list((table.get("table") or {}).get("cells") or [])
    children = {bid: list(b.get("children") or []) for bid, b in by_id.items()}
    tail_cells = cells[keep_rows * columns :] if columns else []
    flat = [text for row in tail_rows for text in row]
    requests = fill_requests(tail_cells, flat, children)
    for start in range(0, len(requests), BATCH_UPDATE_MAX):
        res = await _f.invoke_request(
            build_batch_update_request(document_id, requests[start : start + BATCH_UPDATE_MAX]),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "rows_grown": True, "tail_filled": False}
    return {"ok": True, "rows_grown": True, "tail_filled": True, "rows_added": len(tail_rows)}


async def _write_oversized_table(
    document_id: str,
    table: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    user_key: str,
    identity: str,
) -> dict[str, Any]:
    """Write one table that alone exceeds the /descendant cap: create what fits, grow the rest."""
    prop = (table.get("table") or {}).get("property") or {}
    columns = int(prop.get("column_size") or 0)
    total_rows = int(prop.get("row_size") or 0)
    if not columns:
        return _f.error_result("a converted table reported no columns — cannot write it.")
    keep = min(total_rows, rows_that_fit(columns))
    descendants, tail = clip_table(table, keep, by_id)
    created = await _f.invoke_request(
        _f.build_descendant_request(document_id, [str(table.get("block_id", ""))], descendants, -1),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not created["ok"]:
        return created
    real_id = _f.real_block_id(created, str(table.get("block_id", "")))
    if not real_id:
        return {
            "ok": False,
            "message": (
                f"wrote the first {keep} of {total_rows} table rows, but Feishu returned no id for the "
                "table, so the remaining rows could not be added. The rows written are in the document."
            ),
            "rows_written": keep,
            "rows_missing": total_rows - keep,
        }
    grown = await _grow_table(document_id, real_id, tail, keep, columns, user_key, identity)
    if not grown["ok"]:
        return {
            **grown,
            "message": (f"wrote {keep} of {total_rows} table rows; growing it failed: {grown.get('message', '')}"),
            "rows_written": keep,
            "rows_missing": total_rows - keep,
        }
    return {"ok": True, "table_rows": total_rows, "table_grown_rows": len(tail)}


async def append_markdown(
    document_id: str,
    content: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Append Markdown to a docx as native blocks — real tables, lists, styled runs.

    Converts through Feishu, then writes in batches under the 1000-block cap. Partial
    progress is reported rather than hidden: a failure on batch three leaves batches one
    and two in the document, and ``blocks_written`` says so, because the caller needs to
    know whether to retry the whole thing or the remainder.
    """
    doc = document_id.strip()
    if not doc:
        return _f.error_result("document_id is required.")
    converted = await convert_markdown(content, user_key, identity)
    if not converted["ok"]:
        return converted
    blocks: list[dict[str, Any]] = converted["blocks"]
    by_id = {str(b.get("block_id", "")): b for b in blocks}
    batches = group_into_batches(converted["first_level_ids"], by_id)

    written = 0
    extra: dict[str, Any] = {}
    for ids, payload in batches:
        if len(payload) > DESCENDANT_MAX_BLOCKS:
            # One first-level block too big for a single call. Only a table can be grown
            # after the fact; anything else has no split that the API accepts.
            root = by_id.get(ids[0]) or {}
            if root.get("block_type") != TABLE_BLOCK:
                return {
                    "ok": False,
                    "message": (
                        f"one Markdown block converts to {len(payload)} Feishu blocks, over the "
                        f"{DESCENDANT_MAX_BLOCKS}-block limit for a single write, and only tables can be "
                        "split. Shorten it or write it in pieces."
                    ),
                    "blocks_written": written,
                }
            res = await _write_oversized_table(doc, root, by_id, user_key, identity)
            if not res["ok"]:
                return {**res, "blocks_written": written}
            extra.update({k: v for k, v in res.items() if k != "ok"})
            written += len(payload)
            continue
        res = await _f.invoke_request(
            _f.build_descendant_request(doc, ids, payload, -1),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "blocks_written": written}
        written += len(payload)
    return {
        "ok": True,
        "document_id": doc,
        "added": len(converted["first_level_ids"]),
        "blocks_written": written,
        "native_markdown": True,
        **extra,
    }
