"""Feishu docx/doc/wiki — blocks, tables, flowcharts, embedded sheets & bitables, images.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import io
import json
from typing import Any

import _feishu_impl as _core
import anyio
from lark_channel.api.drive import comment as _comment
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest


async def add_comment_impl(
    file_token: str,
    file_type: str,
    content: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    req = _comment.build_comment_create_request(file_token=file_token, file_type=file_type, content=content)
    return await _core._invoke(req, user_key=user_key, prefer="user", identity=identity)


def _build_reply_create_request(
    *, file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies"
    req.paths["file_token"] = file_token
    req.paths["comment_id"] = comment_id
    req.add_query("file_type", file_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    elements: list[dict[str, Any]] = []
    if at_user_id:
        elements.append({"type": "person", "person": {"user_id": at_user_id}})
    elements.append({"type": "text_run", "text_run": {"text": content}})
    req.body = {"content": {"elements": elements}}
    return req


async def reply_comment_impl(
    file_token: str,
    file_type: str,
    comment_id: str,
    content: str,
    at_user_id: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    req = _build_reply_create_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        content=content,
        at_user_id=at_user_id,
    )
    return await _core._invoke(req, user_key=user_key, prefer="user", identity=identity)


def _raw_get(uri: str, path_name: str, path_value: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = uri
    req.paths[path_name] = path_value
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_docx_raw_request(document_id: str) -> BaseRequest:
    return _raw_get("/open-apis/docx/v1/documents/:document_id/raw_content", "document_id", document_id)


def _build_doc_raw_request(doc_token: str) -> BaseRequest:
    return _raw_get("/open-apis/doc/v2/:doc_token/raw_content", "doc_token", doc_token)


async def read_doc_impl(file_type: str, token: str, max_chars: int) -> dict[str, Any]:
    ft = file_type.strip().lower()
    if ft == "docx":
        res = await _core._invoke(_build_docx_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "doc":
        res = await _core._invoke(_build_doc_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "sheet":
        res = await _core._read_sheet(token)
        content = res.get("content", "") if res["ok"] else ""
    else:
        return _core._error(f"Unsupported file_type {file_type!r}. Use one of: docx, doc, sheet.")

    if not res["ok"]:
        return res

    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return {
        "ok": True,
        "file_type": ft,
        "token": token,
        "content": content,
        "truncated": truncated,
    }


def _build_doc_search_request(search_key: str, count: int, offset: int, docs_types: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/suite/docs-api/search/object"
    req.token_types = {AccessTokenType.USER}
    body: dict[str, Any] = {"search_key": search_key, "count": count, "offset": offset}
    if docs_types:
        body["docs_types"] = docs_types
    req.body = body
    return req


async def search_docs_impl(
    search_key: str, count: int, offset: int, docs_types: str, user_key: str = ""
) -> dict[str, Any]:
    """Search cloud docs by keyword (needs a user_access_token). Returns matched docs."""
    client = _core._get_uat_client()
    if client is None:
        return _core._error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _core._get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _core._error(_core._AUTH_PROMPT, need_auth=True, need_capabilities=["docs_read"])

    types_list = [t.strip() for t in docs_types.split(",") if t.strip()]
    req = _build_doc_search_request(search_key, count, offset, types_list)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:
        return _core._error(f"Feishu search failed: {type(exc).__name__}: {exc}")

    body = _core._parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    docs = [
        {
            "title": e.get("title", ""),
            "token": e.get("docs_token", ""),
            "obj_type": e.get("docs_type", ""),
            "owner_id": e.get("owner_id", ""),
        }
        for e in (data.get("docs_entities", []) if isinstance(data.get("docs_entities"), list) else [])
    ]
    return {
        "ok": True,
        "docs": docs,
        "count": len(docs),
        "has_more": bool(data.get("has_more")),
        "total": data.get("total", 0),
    }


def _build_wiki_space_create_request(name: str, description: str, open_sharing: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/wiki/v2/spaces"
    req.token_types = {AccessTokenType.USER}
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if description:
        body["description"] = description
    if open_sharing:
        body["open_sharing"] = open_sharing
    req.body = body
    return req


async def create_wiki_space_impl(
    name: str, description: str = "", open_sharing: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Create a new Feishu wiki space (knowledge base). Needs a user_access_token.

    Feishu's create-space API only accepts a UAT (not the bot's tenant token); the
    new space is owned by the authorizing user. Returns the new space_id + name.
    """
    client = _core._get_uat_client()
    if client is None:
        return _core._error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _core._get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _core._error(_core._AUTH_PROMPT, need_auth=True, need_capabilities=["wiki_write"])

    sharing = open_sharing.strip()
    if sharing and sharing not in ("open", "closed"):
        return _core._error("open_sharing must be 'open' or 'closed' (or empty).")
    req = _build_wiki_space_create_request(name.strip(), description.strip(), sharing)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:
        return _core._error(f"Feishu create wiki space failed: {type(exc).__name__}: {exc}")

    body = _core._parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    space = data.get("space", {}) if isinstance(data.get("space"), dict) else {}
    space_id = space.get("space_id", "")
    return {
        "ok": True,
        "space_id": space_id,
        "name": space.get("name", name),
        "description": space.get("description", description),
        "url": f"{_DOC_BASE_URL}/wiki/settings/{space_id}" if space_id else "",
    }


# ── Create documents: standalone docx + wiki (knowledge base) nodes ───────────
#
# Read tools above only *fetch* content; these create new documents. A wiki doc
# is a two-layer thing: the wiki *node* (the entry in a knowledge space) wraps an
# underlying docx whose token is `obj_token` — that token is the docx document_id
# you pass to `append_doc_content_impl` to fill in the body. So the full flow is
# list_wiki_spaces → create_wiki_node → append_doc_content.

_DOC_BASE_URL = "https://feishu.cn"


def _build_docx_create_request(title: str, folder_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {}
    if title:
        body["title"] = title
    if folder_token:
        body["folder_token"] = folder_token
    req.body = body
    return req


async def create_docx_impl(
    title: str,
    folder_token: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Create a new standalone docx cloud document. Returns its document_id + URL.

    Pass ``user_key`` to create as that user (doc owned by them); empty uses tenant token.
    """
    res = await _core._invoke(
        _build_docx_create_request(title.strip(), folder_token.strip()),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    doc = data.get("document", {}) if isinstance(data.get("document"), dict) else {}
    document_id = doc.get("document_id", "")
    return {
        "ok": True,
        "document_id": document_id,
        "title": doc.get("title", title),
        "revision_id": doc.get("revision_id"),
        "url": f"{_DOC_BASE_URL}/docx/{document_id}" if document_id else "",
    }


def _build_wiki_node_create_request(
    *, space_id: str, obj_type: str, node_type: str, parent_node_token: str, title: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/wiki/v2/spaces/:space_id/nodes"
    req.paths["space_id"] = space_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"obj_type": obj_type, "node_type": node_type}
    if parent_node_token:
        body["parent_node_token"] = parent_node_token
    if title:
        body["title"] = title
    req.body = body
    return req


async def create_wiki_node_impl(
    space_id: str,
    title: str,
    obj_type: str = "docx",
    parent_node_token: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Create a node (default: a docx doc) in a wiki space. Returns node_token + obj_token(=document_id).

    Pass ``user_key`` to act as that user (needed when the wiki space is owned by the
    user, so the bot isn't a collaborator); empty uses the bot's tenant token.
    """
    if not space_id.strip():
        return _core._error("space_id is required. Use feishu_wiki_list_spaces to find it.")
    # Feishu deprecated `doc`; the API rejects it with error 131010.
    obj_type = (obj_type or "docx").strip()
    if obj_type == "doc":
        obj_type = "docx"
    res = await _core._invoke(
        _build_wiki_node_create_request(
            space_id=space_id.strip(),
            obj_type=obj_type,
            node_type="origin",
            parent_node_token=parent_node_token.strip(),
            title=title.strip(),
        ),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    node = data.get("node", {}) if isinstance(data.get("node"), dict) else {}
    obj_token = node.get("obj_token", "")
    return {
        "ok": True,
        "node_token": node.get("node_token", ""),
        "obj_token": obj_token,
        "obj_type": node.get("obj_type", obj_type),
        "space_id": node.get("space_id", space_id),
        "title": node.get("title", title),
        # For a docx node, obj_token is the document_id — write the body with
        # feishu_doc_append_content(document_id=obj_token, ...).
        "url": f"{_DOC_BASE_URL}/wiki/{node.get('node_token', '')}",
    }


async def create_wiki_doc_with_content_impl(
    space_id: str, title: str, content: str, parent_node_token: str = "", user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create a wiki docx node AND write its body in one call (atomic-ish).

    Avoids the "empty node" failure of doing create + append as two separate LLM
    tool calls: creates the node, then appends the body. If the body write fails,
    the node_token/obj_token are returned alongside the error (so the half-created
    node can be found or retried), rather than leaving a silent empty page.
    """
    node = await _core.create_wiki_node_impl(space_id, title, "docx", parent_node_token, user_key, identity)
    if not node["ok"]:
        return node
    obj_token = node.get("obj_token", "")
    # No body requested (or only blank lines): return the node as-is, not an error.
    if not _content_to_blocks(content or ""):
        return {**node, "added": 0, "note": "no body content — created an empty doc"}
    if not obj_token:
        return {**node, "ok": False, "message": "node created but obj_token missing — cannot write body"}
    written = await _core.append_doc_content_impl(obj_token, content, user_key, identity)
    if not written["ok"]:
        # Surface the node so the caller knows a doc exists and can retry the body.
        return {
            **node,
            "ok": False,
            "body_written": False,
            "added": written.get("added", 0),
            "message": f"Node created but writing body failed: {written.get('message', '')}",
            **({"need_auth": True} if written.get("need_auth") else {}),
        }
    return {**node, "body_written": True, "added": written.get("added", 0)}


def _build_list_spaces_request(page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    return req


async def list_wiki_spaces_impl(page_size: int = 20, page_token: str = "", user_key: str = "") -> dict[str, Any]:
    """List the wiki (knowledge base) spaces the app/user can access. Returns space_id + name.

    Pass ``user_key`` to list the spaces THAT USER can see (the bot's own tenant token
    only sees spaces the bot was added to — usually none); empty uses the bot token.
    """
    page_size = max(1, min(int(page_size or 20), 50))
    res = await _core._invoke_wiki_read(
        _build_list_spaces_request(page_size, page_token.strip()),
        user_key,
        lambda r: not (r.get("data", {}) or {}).get("items"),
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    spaces = [
        {"space_id": it.get("space_id", ""), "name": it.get("name", ""), "space_type": it.get("space_type", "")}
        for it in items
        if isinstance(it, dict)
    ]
    return {
        "ok": True,
        "spaces": spaces,
        "page_token": data.get("page_token", ""),
        "has_more": bool(data.get("has_more")),
    }


def _build_list_wiki_nodes_request(
    space_id: str, page_size: int, page_token: str, parent_node_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces/:space_id/nodes"
    req.paths["space_id"] = space_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    if parent_node_token:
        req.add_query("parent_node_token", parent_node_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_wiki_nodes_impl(
    space_id: str, page_size: int = 50, page_token: str = "", parent_node_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """List the child nodes (documents/pages) of a wiki space (or under a parent node).

    Pass ``user_key`` to browse as that user (the bot's tenant token only sees spaces
    it was added to); empty uses the bot token. ``parent_node_token`` empty lists the
    space's top level; set it to drill into a node's children.
    """
    if not space_id.strip():
        return _core._error("space_id is required. Use feishu_wiki_list_spaces to find it.")
    page_size = max(1, min(int(page_size or 50), 50))
    res = await _core._invoke_wiki_read(
        _build_list_wiki_nodes_request(space_id.strip(), page_size, page_token.strip(), parent_node_token.strip()),
        user_key,
        lambda r: not (r.get("data", {}) or {}).get("items"),
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    nodes = [
        {
            "node_token": it.get("node_token", ""),
            "obj_token": it.get("obj_token", ""),
            "obj_type": it.get("obj_type", ""),
            "title": it.get("title", ""),
            "has_child": bool(it.get("has_child")),
        }
        for it in items
        if isinstance(it, dict)
    ]
    return {
        "ok": True,
        "nodes": nodes,
        "page_token": data.get("page_token", ""),
        "has_more": bool(data.get("has_more")),
    }


# ── Write body content into a docx ────────────────────────────────────────────
#
# The docx block API is rich (tables/images/code/…). We map plain text / light
# Markdown to the two blocks that cover "write a knowledge-base doc": headings
# (`# ` → h1 … up to `###### ` → h6, block_type 3..8) and paragraphs (block_type
# 2). Blank lines are skipped. Children are appended to the document root
# (block_id == document_id) in batches of <=50 (the API cap).

_HEADING_KEYS = {3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4", 7: "heading5", 8: "heading6"}
_BLOCKS_BATCH = 50


def _line_to_block(line: str) -> dict[str, Any] | None:
    text = line.rstrip()
    if not text.strip():
        return None
    stripped = text.lstrip()
    level = 0
    while level < len(stripped) and stripped[level] == "#":
        level += 1
    # "# " .. "###### " → heading blocks (block_type 3..8)
    if 1 <= level <= 6 and level < len(stripped) and stripped[level] == " ":
        block_type = 2 + level
        content = stripped[level + 1 :].strip()
        key = _HEADING_KEYS[block_type]
        return {"block_type": block_type, key: {"elements": [{"text_run": {"content": content}}]}}
    # Everything else → a plain text paragraph (block_type 2)
    return {"block_type": 2, "text": {"elements": [{"text_run": {"content": text.strip()}}]}}


def _content_to_blocks(content: str) -> list[dict[str, Any]]:
    blocks = [b for b in (_line_to_block(ln) for ln in content.splitlines()) if b is not None]
    return blocks


def _build_blocks_append_request(document_id: str, children: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    # Root block: the document_id doubles as the root block_id.
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"children": children}
    return req


# ── Tables (block_type 31) + flowcharts/swimlanes rendered AS tables ────────────
# Feishu docx has no API to *draw* a flowchart/mindnote/board: block_type 21
# (diagram) and 44 (board) are empty canvases the open API can't populate with
# nodes/edges, so a "生成流程图/泳道图" request can't produce a real editable diagram
# via the API. The faithful, fully-supported alternative is a native Feishu table:
# a flowchart becomes a single-column "步骤 → 步骤" ladder, a swimlane becomes a
# grid whose columns are the lanes (角色/部门) and rows are the stages. Both render
# as real, editable tables in the doc — not an image, not a broken embed.
#
# A table can't be created with the plain /children endpoint: the table block, its
# cell blocks (block_type 32) and each cell's text block must all be sent together
# to the /descendant endpoint, which takes a flat `descendants` list plus the
# `children_id` of the blocks that attach at the insert point (here: the table).
_TABLE_BLOCK_TYPE = 31
_TABLE_CELL_BLOCK_TYPE = 32


def _text_block(block_id: str, text: str, *, bold: bool = False) -> dict[str, Any]:
    """A paragraph (block_type 2) carrying one text run, for use inside a table cell."""
    run: dict[str, Any] = {"content": text}
    if bold:
        run["text_style"] = {"bold": True}
    return {"block_id": block_id, "block_type": 2, "text": {"elements": [{"text_run": run}]}}


def _table_descendants(
    rows: list[list[str]],
    *,
    table_id: str = "tbl",
    header_row: bool = True,
    column_width: list[int] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build the (table_block_id, descendants) for a 2-D grid of cell strings.

    ``rows`` is a list of rows, each a list of cell texts; every row is padded to
    the widest row so the grid is rectangular (Feishu requires it). Returns the
    table block_id to put in ``children_id`` and the flat descendants list (table,
    then each cell, then each cell's text block) the /descendant endpoint wants.
    """
    row_size = len(rows)
    column_size = max((len(r) for r in rows), default=0)
    cell_ids: list[str] = []
    descendants: list[dict[str, Any]] = []
    cell_blocks: list[dict[str, Any]] = []
    text_blocks: list[dict[str, Any]] = []
    for r, row in enumerate(rows):
        for c in range(column_size):
            text = row[c] if c < len(row) else ""
            cid = f"{table_id}_c{r}_{c}"
            tid = f"{cid}_t"
            cell_ids.append(cid)
            cell_blocks.append({"block_id": cid, "block_type": _TABLE_CELL_BLOCK_TYPE, "children": [tid]})
            text_blocks.append(_text_block(tid, text, bold=header_row and r == 0))
    table_prop: dict[str, Any] = {"row_size": row_size, "column_size": column_size, "header_row": header_row}
    if column_width:
        table_prop["column_width"] = column_width
    table_block = {
        "block_id": table_id,
        "block_type": _TABLE_BLOCK_TYPE,
        "table": {"cells": cell_ids, "property": table_prop},
    }
    # Order: table first, then all cells, then all cell-text blocks. The API only
    # requires every referenced block_id to be present somewhere in descendants.
    descendants.append(table_block)
    descendants.extend(cell_blocks)
    descendants.extend(text_blocks)
    return table_id, descendants


def _build_descendant_request(
    document_id: str, children_id: list[str], descendants: list[dict[str, Any]], index: int
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    # Root block: the document_id doubles as the root block_id (append at doc root).
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/descendant"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"children_id": children_id, "descendants": descendants}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


# Public aliases for the sibling helper modules (``_feishu_md``, ``_chart_*``): they need
# the same request plumbing, and re-deriving it there would let the two copies drift on
# things like which token types a docx write accepts.
build_descendant_request = _build_descendant_request


async def invoke_request(
    request: Any,
    user_key: str | None = None,
    prefer: str = "tenant",
    identity: str = "",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Public ``_invoke`` for sibling helper modules.

    A delegating function rather than an alias so that replacing ``_invoke`` (tests, or
    any future wrapper) is seen through this door too — an alias would capture the
    original at import time and quietly bypass it.
    """
    return await _core._invoke(request, user_key=user_key, prefer=prefer, identity=identity, capabilities=capabilities)


def real_block_id(response: dict[str, Any], temporary_id: str) -> str:
    """The permanent ``block_id`` Feishu assigned to a temporary id we sent.

    ``/descendant`` answers with ``block_id_relations`` mapping each ``temporary_block_id``
    to the real one. Any follow-up edit (growing a table, filling a cell) has to address
    the real id: the temporary one is ours, meaningful only inside that one request.
    """
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    for rel in (data or {}).get("block_id_relations") or []:
        if isinstance(rel, dict) and str(rel.get("temporary_block_id", "")) == temporary_id:
            return str(rel.get("block_id", ""))
    return ""


async def list_all_blocks(document_id: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """Every block of a docx, raw and unpaged — for code that needs the block *graph*.

    ``list_doc_blocks_impl`` is the agent-facing reader: it trims text to a preview and
    caps the count, both wrong here, where a table's cells have to be matched to the
    paragraphs inside them. Raw payloads, all pages, no cap.
    """
    doc = document_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _core._invoke(
            _build_document_blocks_list_request(doc, _BLOCKS_LIST_PAGE_MAX, page_token),
            user_key=user_key,
            prefer="tenant",
            identity=identity,
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        items.extend(b for b in (data.get("items") or []) if isinstance(b, dict))
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return {"ok": True, "document_id": doc, "blocks": items}


def _parse_rows(rows_json: str) -> list[list[str]] | dict[str, Any]:
    """Parse a JSON 2-D array of cell values into list[list[str]] (or an error dict).

    Accepts a JSON array of arrays; each cell is str()-coerced (numbers/bools become
    text). Rejects anything that isn't a non-empty list of lists.
    """
    try:
        data = json.loads(rows_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return _core._error(f'rows must be a JSON 2-D array, e.g. [["a","b"],["1","2"]]. Parse error: {exc}')
    if not isinstance(data, list) or not data:
        return _core._error("rows must be a non-empty JSON array of rows.")
    parsed: list[list[str]] = []
    for i, row in enumerate(data):
        if not isinstance(row, list):
            return _core._error(f"row {i} must be an array of cell values.")
        parsed.append(["" if c is None else str(c) for c in row])
    return parsed


async def _append_table_descendants(
    document_id: str,
    rows: list[list[str]],
    *,
    header_row: bool,
    column_width: list[int] | None,
    user_key: str,
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Send one table (built from ``rows``) to the /descendant endpoint. Shared by
    the table / flowchart / swimlane tools.

    A caption is written *before* the table, not after: the academic convention places a
    table's title above it and a figure's below, and these three tools produce tables.
    Its "表 N" is numbered off the document's existing captions, so tables and figures
    keep independent, gap-free sequences.
    """
    if not document_id.strip():
        return _core._error("document_id is required.")
    if not rows:
        return _core._error("no rows to write — the table would be empty.")
    doc = document_id.strip()
    result_extra: dict[str, Any] = {}
    if caption.strip():
        # Before the table, and before the table's own request, so a caption that fails
        # doesn't leave a numbered heading pointing at nothing.
        text, fields = await _core._resolve_table_caption(doc, caption, auto_number, user_key, identity)
        result_extra.update(fields)
        note = await _core.append_doc_content_impl(doc, text, user_key, identity)
        result_extra["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result_extra["caption_error"] = note.get("message", "")
    table_id, descendants = _table_descendants(rows, header_row=header_row, column_width=column_width)
    req = _build_descendant_request(doc, [table_id], descendants, index=-1)
    res = await _core._invoke(req, user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    return {
        "ok": True,
        "document_id": doc,
        "rows": len(rows),
        "columns": max((len(r) for r in rows), default=0),
        **result_extra,
    }


async def _resolve_table_caption(
    document_id: str, caption: str, auto_number: bool, user_key: str, identity: str
) -> tuple[str, dict[str, Any]]:
    """A caption body becomes a numbered 表 caption, counted off the document's existing ones.

    Imported lazily because ``_chart_caption`` imports this module: at module scope the
    two would form an import cycle.
    """
    import _chart_caption as _cap  # noqa: PLC0415

    body = _cap.strip_own_number(caption, _cap.TABLE)
    if not auto_number:
        return _cap.format_caption(_cap.TABLE, 0, body), {}
    numbered = await _cap.next_number(document_id, _cap.TABLE, user_key, identity)
    if not numbered.get("ok"):
        return (
            _cap.format_caption(_cap.TABLE, 0, body),
            {"caption_number_skipped": numbered.get("reason", "could not read the document")},
        )
    number = int(numbered["number"])
    return _cap.format_caption(_cap.TABLE, number, body), {"caption_number": number}


async def append_doc_table_impl(
    document_id: str,
    rows_json: str,
    header_row: bool = True,
    column_width_json: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append a native Feishu table (block_type 31) to a docx body.

    ``rows_json`` is a JSON 2-D array; the first row is styled as a header when
    ``header_row`` is true. ``column_width_json`` optionally sets per-column pixel
    widths (JSON array of ints). ``caption`` writes a numbered 表 caption line above it.
    """
    rows = _parse_rows(rows_json)
    if isinstance(rows, dict):  # parse error
        return rows
    column_width: list[int] | None = None
    if column_width_json.strip():
        try:
            cw = json.loads(column_width_json)
            if isinstance(cw, list) and all(isinstance(x, int) for x in cw):
                column_width = cw
        except json.JSONDecodeError, TypeError:
            column_width = None
    return await _append_table_descendants(
        document_id,
        rows,
        header_row=header_row,
        column_width=column_width,
        user_key=user_key,
        identity=identity,
        caption=caption,
        auto_number=auto_number,
    )


async def append_doc_flowchart_impl(
    document_id: str,
    steps_json: str,
    title: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append a flowchart rendered as a single-column Feishu table (each step a row,
    joined by ↓ arrows). Feishu's API can't draw a real diagram, so this is the
    faithful, editable alternative. ``steps_json`` is a JSON array of step labels."""
    try:
        steps = json.loads(steps_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return _core._error(f"steps must be a JSON array of strings. Parse error: {exc}")
    if not isinstance(steps, list) or not steps:
        return _core._error('steps must be a non-empty JSON array, e.g. ["开始","审批","结束"].')
    labels = ["" if s is None else str(s) for s in steps]
    # Interleave arrow rows so the ladder reads top-to-bottom like a flowchart.
    rows: list[list[str]] = [[title or "流程图"]]
    for i, label in enumerate(labels):
        rows.append([label])
        if i < len(labels) - 1:
            rows.append(["↓"])
    return await _append_table_descendants(
        document_id,
        rows,
        header_row=bool(title) or True,
        column_width=None,
        user_key=user_key,
        identity=identity,
        caption=caption,
        auto_number=auto_number,
    )


async def append_doc_swimlane_impl(
    document_id: str,
    lanes_json: str,
    stages_json: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append a swimlane diagram rendered as a Feishu table: columns = lanes
    (角色/部门), rows = stages. Feishu's API can't draw a real swimlane diagram, so
    this grid is the faithful, editable alternative.

    ``lanes_json`` — either a JSON array of lane names (then ``stages_json`` gives
    the per-stage cells) OR a JSON object mapping lane→[activities] (auto-gridded).
    """
    try:
        lanes = json.loads(lanes_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return _core._error(f"lanes must be JSON. Parse error: {exc}")
    rows: list[list[str]]
    if isinstance(lanes, dict):
        # {lane: [activity, ...]} — columns are lanes, each column filled top-down.
        if not lanes:
            return _core._error("lanes object is empty.")
        lane_names = [str(name) for name in lanes]
        columns = [[str(a) for a in (lanes[name] or [])] for name in lane_names]
        depth = max((len(col) for col in columns), default=0)
        rows = [lane_names]
        for r in range(depth):
            rows.append([col[r] if r < len(col) else "" for col in columns])
    elif isinstance(lanes, list) and lanes:
        # lanes = header (column) names; stages_json = 2-D array of body rows.
        lane_names = [str(x) for x in lanes]
        rows = [lane_names]
        if stages_json.strip():
            body = _parse_rows(stages_json)
            if isinstance(body, dict):  # parse error
                return body
            rows.extend(body)
    else:
        return _core._error("lanes must be a non-empty JSON array of lane names or an object {lane:[activities]}.")
    return await _append_table_descendants(
        document_id,
        rows,
        header_row=True,
        column_width=None,
        user_key=user_key,
        identity=identity,
        caption=caption,
        auto_number=auto_number,
    )


# ── Embedded spreadsheets (block_type 30) and bitables (18) inside a doc ────────
#
# A native table block (31, above) is part of the document: it holds text, and nothing
# more. What people mean by "在文档里放一个可编辑的飞书表格" is usually the other thing —
# an embedded *spreadsheet*, with a formula bar, cell formats and filters, editable in
# place and openable as its own sheet. That is block_type 30, and Feishu provisions the
# backing spreadsheet itself: creating the block with a `row_size`/`column_size` returns
# `sheet.token` of the form "<spreadsheetToken>_<sheetId>" (verified live — an empty
# `sheet: {}` is rejected with 1770001 invalid param). Block 18 is the same story for a
# 多维表格, whose token is "<appToken>_<tableId>" and which needs a `view_type`.
#
# The point of splitting that token is that no new write path is needed: the two halves
# are exactly the (spreadsheet_token, sheet_id) pair the sheets/v2 values API already
# takes, so the existing write/append/format helpers fill an in-document sheet as-is.
# Writing past the declared size is fine — the worksheet grows to fit (measured: an 8-row
# write into a 5-row block left the block reporting 8 rows).
_SHEET_BLOCK_TYPE = 30
_BITABLE_BLOCK_TYPE = 18

# Largest row_size/column_size the *create block* call accepts. Measured against the live
# API: 9 passes, 10 is refused with 99992402 "field validation failed" whatever the other
# dimension is. Nothing in the docs mentions it, and the error names no field, so the
# number is empirical — a bigger grid is reached by writing into the sheet afterwards,
# which does grow it (30x4 written into a 9x4 block leaves the worksheet at 30x4).
_SHEET_BLOCK_CREATE_MAX = 9

# view_type 1 = grid (表格视图), the default a person sees when opening a new 多维表格.
_BITABLE_DEFAULT_VIEW = 1


def _column_letter(count: int) -> str:
    """Spreadsheet column label for the ``count``-th column (1 → A, 27 → AA)."""
    if count < 1:
        return "A"
    label = ""
    while count:
        count, rem = divmod(count - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def split_embedded_sheet_token(block_token: str) -> tuple[str, str]:
    """Split an embedded block's token into its ``(container_token, child_id)`` halves.

    A sheet block's token is ``"<spreadsheetToken>_<sheetId>"`` and a bitable block's is
    ``"<appToken>_<tableId>"``. Only the *first* underscore separates them: Feishu tokens
    are alphanumeric, but splitting from the right would break the moment one contains an
    underscore, so partition from the left. Returns ``("", "")`` when there is no
    separator, letting callers report a clear error instead of writing to a half-token.
    """
    head, sep, tail = (block_token or "").strip().partition("_")
    if not sep or not head or not tail:
        return "", ""
    return head, tail


def _embedded_block_token(block: dict[str, Any], key: str) -> str:
    """The ``token`` of an embedded block's payload (``"sheet"`` / ``"bitable"``), or ``""``."""
    payload = block.get(key)
    return str(payload.get("token", "")) if isinstance(payload, dict) else ""


def _embedded_sheet_result(document_id: str, child: dict[str, Any], *, rows: int, columns: int) -> dict[str, Any]:
    """Shape a created sheet block into the tool result, including its write coordinates.

    ``spreadsheet_token`` + ``sheet_id`` are returned because they are the whole point:
    they are what ``feishu_sheet_write`` needs to fill the embedded grid, and an agent
    that only got the ``block_id`` back would have no way to write into it.
    """
    token = _embedded_block_token(child, "sheet")
    spreadsheet, sheet_id = split_embedded_sheet_token(token)
    return {
        "ok": True,
        "document_id": document_id,
        "block_id": child.get("block_id", ""),
        "block_token": token,
        "spreadsheet_token": spreadsheet,
        "sheet_id": sheet_id,
        "range": f"{sheet_id}!A1" if sheet_id else "",
        "rows": rows,
        "columns": columns,
        "url": f"{_DOC_BASE_URL}/sheets/{spreadsheet}" if spreadsheet else "",
    }


def _first_child(res: dict[str, Any], block_type: int) -> dict[str, Any] | None:
    """Pick the created block of the wanted type out of a /children or /descendant reply."""
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list):
        return None
    for child in children:
        if isinstance(child, dict) and child.get("block_type") == block_type:
            return child
    return None


async def append_doc_sheet_impl(
    document_id: str,
    rows: int = 10,
    columns: int = 5,
    values_json: str = "",
    header_row: bool = True,
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append an embedded, editable Feishu spreadsheet (block_type 30) to a docx body.

    When ``values_json`` is given, the grid is written into the new sheet, so one call
    produces a filled in-document spreadsheet. The write goes through the ordinary
    sheets/v2 path, which means ``=``-prefixed cells become live formulas — the reason to
    embed a sheet rather than use a plain table block.

    The block is created at most 9x9 (the API's undocumented creation cap) and grown to
    the requested/data size by the write that follows, including for an empty sheet, whose
    area is written as blank cells. So the size asked for is the size that appears.

    A failed *write* still returns the block's coordinates with ``ok: False``: the sheet
    exists in the document at that point, and silently dropping its token would leave an
    empty embed nobody can fill.
    """
    if not document_id.strip():
        return _core._error("document_id is required.")
    doc = document_id.strip()

    values: list[list[Any]] | None = None
    if values_json.strip():
        values, err = _core._parse_values_json(values_json)
        if err or values is None:
            return _core._error(err or "values_json produced no rows.")
    if rows < 1 or columns < 1:
        return _core._error("rows and columns must both be at least 1.")
    if rows > _core._SHEET_MAX_ROWS or columns > _core._SHEET_MAX_COLS:
        return _core._error(
            f"an embedded sheet is capped at {_core._SHEET_MAX_ROWS} rows x {_core._SHEET_MAX_COLS} columns."
        )

    # The wanted final size, which is usually *larger* than the block can be created at.
    # With data, the data decides: padding a 4-column table out to the default 5 would add
    # a stray empty column the caller never asked for.
    want_rows, want_columns = rows, columns
    if values:
        want_rows = len(values)
        want_columns = max((len(r) for r in values), default=0)
    # Creating the block is capped at 9x9 (measured: row_size or column_size of 10 is
    # refused with 99992402 field validation failed, 9 is accepted). The cap only applies
    # to *creation*: a subsequent ranged write grows the worksheet, so a big table starts
    # from a clamped block and is expanded by its own write.
    create_rows = min(want_rows, _SHEET_BLOCK_CREATE_MAX)
    create_columns = min(want_columns, _SHEET_BLOCK_CREATE_MAX)
    rows, columns = create_rows, create_columns

    result_extra: dict[str, Any] = {}
    if caption.strip():
        # Same convention as the table tools: a 表 caption goes above what it labels, and
        # it is written first so a failed caption never numbers a sheet that isn't there.
        text, fields = await _core._resolve_table_caption(doc, caption, auto_number, user_key, identity)
        result_extra.update(fields)
        note = await _core.append_doc_content_impl(doc, text, user_key, identity)
        result_extra["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result_extra["caption_error"] = note.get("message", "")

    block = {"block_type": _SHEET_BLOCK_TYPE, "sheet": {"row_size": rows, "column_size": columns}}
    res = await _core._invoke(
        _build_blocks_append_request(doc, [block]), user_key=user_key, prefer="user", identity=identity
    )
    if not res["ok"]:
        return res
    child = _first_child(res, _SHEET_BLOCK_TYPE)
    if child is None:
        return _core._error("Feishu created the block but returned no sheet block to write into.")
    out = {**_embedded_sheet_result(doc, child, rows=want_rows, columns=want_columns), **result_extra}
    needs_growing = values is None and (want_rows > create_rows or want_columns > create_columns)
    if values is None and not needs_growing:
        return out
    # Split again into plain strings rather than reading them back out of ``out``, whose
    # value type is the union of everything in the result dict.
    block_token = _embedded_block_token(child, "sheet")
    spreadsheet, sheet_id = split_embedded_sheet_token(block_token)
    if not spreadsheet or not sheet_id:
        return {
            **out,
            "ok": False,
            "message": (
                f"embedded sheet created but its token {block_token!r} could not be split into "
                "spreadsheet_token/sheet_id — write the values with feishu_sheet_write once you have them."
            ),
        }

    # An empty sheet asked to be bigger than the creation cap is grown by writing blank
    # cells over the wanted area — the same ranged write, just with nothing in it, so the
    # person gets the 20 empty rows they asked to type into rather than a silent 9.
    payload = values if values is not None else [[None] * want_columns for _ in range(want_rows)]
    # The range must span the grid. A bare "<sheetId>!A1" is accepted by Feishu and comes
    # back ok=True with an empty updatedRange having written *nothing* — data silently
    # lost — so the end cell is always spelled out.
    end = f"{_column_letter(want_columns)}{want_rows}"
    wrote = await _core.write_sheet_impl(
        spreadsheet,
        f"{sheet_id}!A1:{end}",
        json.dumps(payload, ensure_ascii=False),
        user_key,
        identity,
    )
    if not wrote["ok"]:
        return {
            **out,
            "ok": False,
            "values_written": False,
            "message": f"Embedded sheet created but writing its values failed: {wrote.get('message', '')}",
            **({"need_auth": True} if wrote.get("need_auth") else {}),
        }
    if values is not None:
        out["values_written"] = True
    out["updated_cells"] = wrote.get("updated_cells")
    if header_row and values:
        # Bold header, matching what feishu_doc_append_table's header row looks like. A
        # style failure is reported but doesn't fail the call: the data is already there.
        styled = await _core.format_sheet_impl(
            spreadsheet,
            f"{sheet_id}!A1:{_column_letter(len(values[0]))}1",
            json.dumps({"font": {"bold": True}}),
            user_key,
            identity,
        )
        out["header_styled"] = bool(styled.get("ok"))
    return out


async def append_doc_bitable_impl(
    document_id: str,
    view_type: int = _BITABLE_DEFAULT_VIEW,
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append an embedded 多维表格 (bitable, block_type 18) to a docx body.

    Returns the new bitable's ``app_token`` and ``table_id`` — split out of the block's
    ``"<appToken>_<tableId>"`` token — so the caller can add fields and records to it.
    Feishu creates the bitable itself; it starts with default fields, which
    ``feishu_api POST /open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields`` can extend.
    """
    if not document_id.strip():
        return _core._error("document_id is required.")
    doc = document_id.strip()
    result_extra: dict[str, Any] = {}
    if caption.strip():
        text, fields = await _core._resolve_table_caption(doc, caption, auto_number, user_key, identity)
        result_extra.update(fields)
        note = await _core.append_doc_content_impl(doc, text, user_key, identity)
        result_extra["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result_extra["caption_error"] = note.get("message", "")

    block = {"block_type": _BITABLE_BLOCK_TYPE, "bitable": {"view_type": int(view_type or _BITABLE_DEFAULT_VIEW)}}
    res = await _core._invoke(
        _build_blocks_append_request(doc, [block]), user_key=user_key, prefer="user", identity=identity
    )
    if not res["ok"]:
        return res
    child = _first_child(res, _BITABLE_BLOCK_TYPE)
    if child is None:
        return _core._error("Feishu created the block but returned no bitable block.")
    token = _embedded_block_token(child, "bitable")
    app_token, table_id = split_embedded_sheet_token(token)
    return {
        "ok": True,
        "document_id": doc,
        "block_id": child.get("block_id", ""),
        "block_token": token,
        "app_token": app_token,
        "table_id": table_id,
        "url": f"{_DOC_BASE_URL}/base/{app_token}" if app_token else "",
        **result_extra,
    }


async def append_doc_content_impl(
    document_id: str,
    content: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Append text/heading blocks (from plain text or light Markdown) to a docx body.

    Content that uses Markdown beyond headings — a ``|``-delimited table, a ``- `` list,
    ``**bold**``, a fenced code block — is routed through Feishu's own Markdown converter
    so it lands as *native blocks*: a real table you can drag, sort and edit, not the
    literal pipes and dashes this tool used to write. Plain prose keeps the local
    heading/paragraph mapping, which needs no round-trip.

    Pass ``user_key`` to write as that user (e.g. into a doc inside a user-owned wiki);
    empty uses the bot's tenant token.
    """
    if not document_id.strip():
        return _core._error("document_id is required.")
    if not (content or "").strip():
        return _core._error("content is empty — nothing to write.")
    # Imported lazily: _feishu_md imports this module, so a module-scope import would cycle.
    import _feishu_md as _md  # noqa: PLC0415

    if _md.has_rich_markdown(content):
        return await _md.append_markdown(document_id.strip(), content, user_key, identity)
    blocks = _content_to_blocks(content or "")
    if not blocks:
        return _core._error("content is empty — nothing to write.")
    added = 0
    for start in range(0, len(blocks), _BLOCKS_BATCH):
        batch = blocks[start : start + _BLOCKS_BATCH]
        res = await _core._invoke(
            _build_blocks_append_request(document_id.strip(), batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            res["added"] = added
            return res
        added += len(batch)
    return {"ok": True, "document_id": document_id.strip(), "added": added}


# ── Drive media upload — put a learning video / signed proof into Feishu Drive ─────────
# upload_all handles files up to 20MB in one shot (multipart). Larger files need the
# chunked upload_prepare/upload_part/upload_finish flow (not implemented here).
_UPLOAD_ALL_MAX_BYTES = 20 * 1024 * 1024


class _NamedBytes(io.BytesIO):
    """An in-memory file that carries a filename.

    The SDK decides "this is multipart" by finding ``io.IOBase`` values in the request
    *body* — plain ``bytes`` is not enough — and httpx reads ``.name`` to fill in the
    multipart ``filename=``. A bare ``BytesIO`` would upload as "upload" with no
    extension, which Feishu rejects for images.
    """

    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _build_media_upload_all_request(
    file_name: str, parent_type: str, parent_node: str, size: int, data: bytes, extra: dict[str, Any] | None
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/medias/upload_all"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {
        "file_name": file_name,
        "parent_type": parent_type,
        "parent_node": parent_node,
        "size": str(size),
    }
    if extra:
        body["extra"] = json.dumps(extra, ensure_ascii=False)
    # The binary goes in the BODY, not in req.files: Client.arequest overwrites
    # req.files with Files.extract_files(req.body) right before sending, so anything
    # assigned here is discarded — the request then goes out as application/json and
    # Feishu answers "boundary not found".
    body["file"] = _NamedBytes(data, file_name)
    req.body = body
    return req


async def upload_media_impl(
    file_path: str,
    parent_type: str = "explorer",
    parent_node: str = "",
    file_name: str = "",
    extra_json: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Upload a local file (e.g. a learning video) to Feishu Drive; returns its file_token."""
    p = anyio.Path(file_path)
    if not await p.is_file():
        return _core._error(f"file not found: {file_path}")
    if not parent_node.strip():
        return _core._error("parent_node is required (the target folder token for parent_type=explorer).")
    extra: dict[str, Any] | None = None
    if extra_json.strip():
        try:
            extra = json.loads(extra_json)
        except ValueError as exc:
            return _core._error(f"extra_json is not valid JSON: {exc}")
    name = file_name.strip() or p.name
    data = await p.read_bytes()
    size = len(data)
    if size > _core._UPLOAD_ALL_MAX_BYTES:
        return _core._error(
            f"file is {size} bytes (> 20MB). upload_all supports files up to 20MB; "
            "use the chunked upload flow for larger files.",
            size=size,
        )
    # A factory, not a request: an upload may be attempted under both identities and
    # the SDK consumes the file entry on the first send.
    res = await _core._invoke(
        lambda: _build_media_upload_all_request(name, parent_type, parent_node.strip(), size, data, extra),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "file_token": rdata.get("file_token", ""), "file_name": name, "size": size}


# ── Data charts as docx image blocks (block_type 27) ───────────────────────────
# The one way to land a real data chart (pie/line/bar/…) in a Feishu doc: docx has
# no chart block and the Sheets API can't create charts, but it does have an *image*
# block, and drive medias/upload_all can target that block directly. The dance is
# three calls and the order matters:
#
#   1. POST .../blocks/:block_id/children with an empty ``image`` block → returns the
#      new block's block_id. The block exists but renders as a placeholder.
#   2. POST drive/v1/medias/upload_all with parent_type="docx_image" and
#      parent_node=<that block_id> → uploads the PNG *into* the block.
#   3. PATCH .../blocks/:block_id with replace_image=<file_token> → binds the token so
#      the block renders the picture.
#
# Step 3 is required: without it the upload is attached but the block keeps showing
# a placeholder. An empty image block left behind by a failed step 2/3 is worse than
# no chart, so failures try to clean it up.
_IMAGE_BLOCK_TYPE = 27


def _build_image_block_create_request(document_id: str, block_id: str, index: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # An image block is created empty: width/height are the display box, and the
    # token is filled in later by the replace_image patch.
    body: dict[str, Any] = {"children": [{"block_type": _IMAGE_BLOCK_TYPE, "image": {"token": ""}}]}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


def _build_image_block_patch_request(document_id: str, block_id: str, file_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"replace_image": {"token": file_token}}
    return req


def _build_block_delete_request(document_id: str, block_id: str, index: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children/batch_delete"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"start_index": index, "end_index": index + 1}
    return req


async def _upload_into_image_block(image_path: str, block_id: str, user_key: str, identity: str = "") -> dict[str, Any]:
    """Upload a local PNG into an existing docx image block; returns its file_token."""
    path = anyio.Path(image_path)
    if not await path.is_file():
        return _core._error(f"chart image not found: {image_path}")
    data = await path.read_bytes()
    size = len(data)
    if size > _core._UPLOAD_ALL_MAX_BYTES:
        return _core._error(f"chart image is {size} bytes (> 20MB) — too large to upload.", size=size)
    res = await _core._invoke(
        lambda: _build_media_upload_all_request(path.name, "docx_image", block_id, size, data, None),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    token = rdata.get("file_token", "")
    if not token:
        return _core._error("upload succeeded but returned no file_token.")
    return {"ok": True, "file_token": token, "size": size}


async def read_doc_for_captions(document_id: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """A docx's plain text, for counting the 图/表 captions already in it.

    Separate from ``read_doc_impl`` because that one is a user-facing reader with no
    identity plumbing (tenant token only): a chart being written into someone's own doc
    has to be counted with the same credentials that will do the writing, or the read
    fails on exactly the user-owned docs where captions matter most. ``prefer="tenant"``
    since this is a read — a doc the bot can see needs no user authorization.
    """
    doc = document_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    res = await _core._invoke(
        lambda: _build_docx_raw_request(doc),
        user_key=user_key,
        prefer="tenant",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "content": data.get("content", "")}


async def append_doc_image_impl(
    document_id: str, image_path: str, caption: str = "", user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Append a local image to a docx as a real image block, with an optional caption.

    Shared by every chart tool: they render a PNG, then hand it here. The caption is
    written as a separate paragraph below the image (docx image blocks carry no
    caption field of their own) — a chart without a "图N: what this shows" line makes
    the reader guess at what they're looking at.
    """
    doc = document_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    created = await _core._invoke(
        lambda: _build_image_block_create_request(doc, doc, -1), user_key=user_key, prefer="user", identity=identity
    )
    if not created["ok"]:
        return created
    cdata = created["data"] if isinstance(created["data"], dict) else {}
    children = cdata.get("children") or []
    block_id = children[0].get("block_id", "") if children and isinstance(children[0], dict) else ""
    if not block_id:
        return _core._error("created the image block but the response carried no block_id.")
    # Where the placeholder landed, so a later failure can remove exactly that block.
    index = cdata.get("index")

    uploaded = await _upload_into_image_block(image_path, block_id, user_key, identity)
    if not uploaded["ok"]:
        await _discard_image_block(doc, block_id, index, user_key, identity)
        return uploaded
    patched = await _core._invoke(
        lambda: _build_image_block_patch_request(doc, block_id, uploaded["file_token"]),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not patched["ok"]:
        await _discard_image_block(doc, block_id, index, user_key, identity)
        return patched
    result: dict[str, Any] = {
        "ok": True,
        "document_id": doc,
        "block_id": block_id,
        "file_token": uploaded["file_token"],
        "bytes": uploaded["size"],
    }
    if caption.strip():
        # A failed caption doesn't invalidate the chart itself, so it's reported
        # rather than treated as a failure of the whole append.
        note = await _core.append_doc_content_impl(doc, caption.strip(), user_key, identity)
        result["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result["caption_error"] = note.get("message", "")
    return result


# ── Attachments as docx file blocks (block_type 23) ────────────────────────────
# The same three-step dance as the image block above, with three constants swapped:
# block_type 27 → 23, parent_type "docx_image" → "docx_file", and the patch field
# ``replace_image`` → ``replace_file`` (whose only member is ``token``). Keeping it
# beside the image path rather than folding both into one parameterized helper would
# save a few lines but hide which of the three constants belong together — a mismatched
# pair (file block bound with replace_image) is accepted with code 0 and renders as a
# broken placeholder.
_FILE_BLOCK_TYPE = 23


def _build_file_block_create_request(document_id: str, block_id: str, index: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"children": [{"block_type": _FILE_BLOCK_TYPE, "file": {"token": ""}}]}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


def _build_file_block_patch_request(document_id: str, block_id: str, file_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"replace_file": {"token": file_token}}
    return req


async def _upload_into_file_block(file_path: str, block_id: str, user_key: str, identity: str = "") -> dict[str, Any]:
    """Upload a local file into an existing docx file block; returns its file_token."""
    path = anyio.Path(file_path)
    if not await path.is_file():
        return _core._error(f"file not found: {file_path}")
    data = await path.read_bytes()
    size = len(data)
    if size > _core._UPLOAD_ALL_MAX_BYTES:
        return _core._error(
            f"file is {size} bytes (> 20MB). Attaching into a document uses upload_all, "
            "which caps at 20MB; larger files need Feishu's chunked upload flow.",
            size=size,
        )
    res = await _core._invoke(
        lambda: _build_media_upload_all_request(path.name, "docx_file", block_id, size, data, None),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    token = rdata.get("file_token", "")
    if not token:
        return _core._error("upload succeeded but returned no file_token.")
    return {"ok": True, "file_token": token, "size": size}


async def append_doc_file_impl(
    document_id: str, file_path: str, caption: str = "", user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Attach a local file to a docx as a real file block, with an optional caption.

    Mirrors :func:`append_doc_image_impl` step for step, including the cleanup: an empty
    file block left behind by a failed upload or patch renders as a broken placeholder,
    which is worse than no attachment at all.
    """
    doc = document_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    created = await _core._invoke(
        lambda: _build_file_block_create_request(doc, doc, -1), user_key=user_key, prefer="user", identity=identity
    )
    if not created["ok"]:
        return created
    cdata = created["data"] if isinstance(created["data"], dict) else {}
    children = cdata.get("children") or []
    block_id = children[0].get("block_id", "") if children and isinstance(children[0], dict) else ""
    if not block_id:
        return _core._error("created the file block but the response carried no block_id.")
    index = cdata.get("index")

    uploaded = await _upload_into_file_block(file_path, block_id, user_key, identity)
    if not uploaded["ok"]:
        await _discard_image_block(doc, block_id, index, user_key, identity)
        return uploaded
    patched = await _core._invoke(
        lambda: _build_file_block_patch_request(doc, block_id, uploaded["file_token"]),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not patched["ok"]:
        await _discard_image_block(doc, block_id, index, user_key, identity)
        return patched
    result: dict[str, Any] = {
        "ok": True,
        "document_id": doc,
        "block_id": block_id,
        "file_token": uploaded["file_token"],
        "bytes": uploaded["size"],
    }
    if caption.strip():
        note = await _core.append_doc_content_impl(doc, caption.strip(), user_key, identity)
        result["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result["caption_error"] = note.get("message", "")
    return result


def _build_block_children_list_request(document_id: str, block_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", 500)
    return req


async def _discard_image_block(document_id: str, block_id: str, index: Any, user_key: str, identity: str = "") -> None:
    """Best-effort removal of a placeholder image block after a failed upload/patch.

    The create response carries no ``index``, so the delete range is found by locating
    ``block_id`` among the document's children — deleting by a guessed range could take
    the user's real content with it. If the block can't be located the empty placeholder
    is left in place: an orphan is unfortunate, deleting the wrong block is not
    recoverable.
    """
    if not isinstance(index, int) or index < 0:
        index = await _locate_child_index(document_id, block_id, user_key, identity)
    if not isinstance(index, int) or index < 0:
        return
    with contextlib.suppress(Exception):
        await _core._invoke(
            lambda: _build_block_delete_request(document_id, document_id, index),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )


async def _locate_child_index(document_id: str, block_id: str, user_key: str, identity: str = "") -> int:
    """Position of ``block_id`` among the doc root's children, or -1 if not found."""
    with contextlib.suppress(Exception):
        res = await _core._invoke(
            lambda: _build_block_children_list_request(document_id, document_id),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if res.get("ok"):
            data = res.get("data")
            items = data.get("items") or [] if isinstance(data, dict) else []
            for position, item in enumerate(items):
                if isinstance(item, dict) and item.get("block_id") == block_id:
                    return position
    return -1


# ── Block-level editing — revise a doc in place instead of only appending ────────
# Everything above only ever *adds* to a document, so fixing one wrong sentence meant
# rewriting the whole doc. These three close that loop: list the blocks to learn their
# block_ids and current text, rewrite one block's text, or delete blocks outright.
#
# The block_id is the unit of address, never a line number: Feishu's delete endpoint
# takes a *child index range* under a parent, and indexes shift as soon as anything is
# added or removed. So delete resolves block_id → current index right before deleting,
# and refuses rather than guessing when the block can't be found — a wrong index here
# deletes someone else's paragraph, which no retry can undo.
_BLOCK_TYPE_NAMES = {
    1: "page",
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
    18: "bitable",
    19: "callout",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    27: "image",
    28: "iframe",
    30: "sheet",
    31: "table",
    32: "table_cell",
    34: "quote_container",
    999: "unsupported",
}

# The typed payload key holding a block's text elements, per block_type. Text lives
# under the block's own kind name (a heading2's runs are in "heading2"), which is why
# reading a block's text needs the type→key mapping rather than one fixed field.
_TEXTUAL_BLOCK_KEYS = {
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
}


def _build_document_blocks_list_request(document_id: str, page_size: int, page_token: str) -> BaseRequest:
    """GET every block of a document (flat, with parent_id/children), not just one level."""
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks"
    req.paths["document_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    return req


def _build_block_text_patch_request(document_id: str, block_id: str, elements: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"update_text_elements": {"elements": elements}}
    return req


def _build_blocks_batch_delete_request(document_id: str, block_id: str, start: int, end: int) -> BaseRequest:
    """Delete children [start, end) of ``block_id`` — the range is half-open."""
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children/batch_delete"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"start_index": start, "end_index": end}
    return req


def _block_plain_text(block: dict[str, Any]) -> str:
    """The block's visible text, joined from its text_run/equation/mention elements."""
    key = _TEXTUAL_BLOCK_KEYS.get(block.get("block_type") or 0, "")
    payload = block.get(key) if key else None
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for element in payload.get("elements") or []:
        if not isinstance(element, dict):
            continue
        run = element.get("text_run")
        if isinstance(run, dict):
            parts.append(str(run.get("content", "")))
            continue
        equation = element.get("equation")
        if isinstance(equation, dict):
            parts.append(str(equation.get("content", "")))
            continue
        mention = element.get("mention_doc")
        if isinstance(mention, dict):
            parts.append(str(mention.get("title", "")))
            continue
        mention_user = element.get("mention_user")
        if isinstance(mention_user, dict):
            parts.append("@" + str(mention_user.get("user_id", "")))
    return "".join(parts)


_BLOCKS_LIST_PAGE_MAX = 500


def _embedded_block_coordinates(raw: dict[str, Any], block_type: int) -> dict[str, Any]:
    """Write coordinates for an embedded sheet/bitable block, or ``{}`` for anything else.

    Keyed by what the caller does next: a sheet block yields the
    ``spreadsheet_token``/``sheet_id``/``range`` that ``feishu_sheet_*`` takes, a bitable
    block the ``app_token``/``table_id`` that ``feishu_bitable_*`` takes.
    """
    if block_type == _SHEET_BLOCK_TYPE:
        token = _embedded_block_token(raw, "sheet")
        spreadsheet, sheet_id = split_embedded_sheet_token(token)
        return {
            "block_token": token,
            "spreadsheet_token": spreadsheet,
            "sheet_id": sheet_id,
            "range": f"{sheet_id}!A1" if sheet_id else "",
        }
    if block_type == _BITABLE_BLOCK_TYPE:
        token = _embedded_block_token(raw, "bitable")
        app_token, table_id = split_embedded_sheet_token(token)
        return {"block_token": token, "app_token": app_token, "table_id": table_id}
    return {}


async def list_doc_blocks_impl(
    document_id: str,
    max_blocks: int = 200,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """List a docx's blocks as ``{block_id, block_type, type_name, text, parent_id}``.

    The prerequisite for editing anything: ``update_doc_block`` / ``delete_doc_blocks``
    address content by ``block_id``, and this is the only way to learn those ids. Text
    is trimmed to a preview so listing a long document doesn't flood the context; read
    the full body with ``read_doc_impl`` when that's what's wanted.

    ``prefer="tenant"`` because this is a read — a doc the bot can already see needs no
    user authorization — with the user's identity used when one is available.
    """
    doc = document_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    limit = max(1, min(int(max_blocks or 200), 2000))
    items: list[dict[str, Any]] = []
    page_token = ""
    truncated = False
    while True:
        remaining = limit - len(items)
        page_size = min(_BLOCKS_LIST_PAGE_MAX, max(remaining, 1))
        res = await _core._invoke(
            _build_document_blocks_list_request(doc, page_size, page_token),
            user_key=user_key,
            prefer="tenant",
            identity=identity,
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            if len(items) >= limit:
                truncated = True
                break
            block_type = raw.get("block_type") or 0
            text = _block_plain_text(raw)
            entry = {
                "block_id": raw.get("block_id", ""),
                "block_type": block_type,
                "type_name": _BLOCK_TYPE_NAMES.get(block_type, str(block_type)),
                "parent_id": raw.get("parent_id", ""),
                "text": text if len(text) <= 200 else text[:200] + "…",
                "editable_text": block_type in _TEXTUAL_BLOCK_KEYS,
            }
            # An embedded sheet/bitable holds no text, so the fields above say nothing
            # about it: its content lives in a separate spreadsheet addressed by the
            # block's token. Surfacing the split token here is what makes an *existing*
            # in-document table editable — otherwise finding one and updating a cell
            # would be impossible, since only the create call ever returned its token.
            entry.update(_embedded_block_coordinates(raw, block_type))
            items.append(entry)
        page_token = str(data.get("page_token") or "")
        if truncated or not page_token or len(items) >= limit:
            truncated = truncated or bool(page_token)
            break
    return {"ok": True, "document_id": doc, "count": len(items), "truncated": truncated, "blocks": items}


async def update_doc_block_impl(
    document_id: str,
    block_id: str,
    text: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Replace the text of one docx block, keeping the block itself (and its type).

    Rewriting a heading leaves it a heading, a bullet stays a bullet: only the text runs
    are replaced. Structural blocks (image/table/divider/page) carry no text runs to
    replace and are rejected up front with the reason, rather than sent off to fail as
    an opaque Feishu error.
    """
    doc = document_id.strip()
    block = block_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    if not block:
        return _core._error("block_id is required — get it from feishu_doc_list_blocks.")
    if block == doc:
        return _core._error(
            "block_id is the document's root block, which holds no text. "
            "Pass the block_id of a paragraph/heading from feishu_doc_list_blocks."
        )
    if text == "":
        return _core._error("text is required — to remove a block entirely use feishu_doc_delete_blocks.")
    res = await _core._invoke(
        _build_block_text_patch_request(doc, block, [{"text_run": {"content": text}}]),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    return {"ok": True, "document_id": doc, "block_id": block, "text": text}


async def delete_doc_blocks_impl(
    document_id: str,
    block_ids_json: str,
    parent_block_id: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Delete one or more blocks, addressed by block_id, from a docx.

    Feishu deletes by child-index range under a parent, so each id is resolved to its
    current index first. Deletions run highest-index-first: removing a block shifts
    every later sibling down, so deleting low-to-high would make each subsequent index
    point one block too far. Ids that can't be located are reported as ``not_found``
    instead of being guessed at.
    """
    doc = document_id.strip()
    if not doc:
        return _core._error("document_id is required.")
    try:
        raw_ids = json.loads(block_ids_json or "[]")
    except json.JSONDecodeError as exc:
        return _core._error(f"block_ids_json is not valid JSON: {exc}")
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list) or not raw_ids:
        return _core._error("block_ids_json must be a non-empty JSON array of block_ids, e.g. '[\"abc123\"]'.")
    wanted = [str(item).strip() for item in raw_ids if str(item).strip()]
    if not wanted:
        return _core._error("block_ids_json contained no usable block_id.")
    parent = parent_block_id.strip() or doc
    if doc in wanted:
        return _core._error(
            "refusing to delete the document's root block — delete the file with feishu_api "
            "DELETE /open-apis/drive/v1/files/:file_token."
        )
    listed = await _core._invoke(
        _build_block_children_list_request(doc, parent),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not listed["ok"]:
        return listed
    ldata = listed["data"] if isinstance(listed["data"], dict) else {}
    positions: dict[str, int] = {}
    for position, item in enumerate(ldata.get("items") or []):
        if isinstance(item, dict) and item.get("block_id"):
            positions[str(item["block_id"])] = position
    targets = sorted(((positions[bid], bid) for bid in wanted if bid in positions), reverse=True)
    not_found = [bid for bid in wanted if bid not in positions]
    deleted: list[str] = []
    for index, bid in targets:
        res = await _core._invoke(
            _build_blocks_batch_delete_request(doc, parent, index, index + 1),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            res["deleted"] = deleted
            res["not_found"] = not_found
            return res
        deleted.append(bid)
    if not deleted:
        return _core._error(
            f"none of those block_ids are children of {parent} — "
            "re-check them with feishu_doc_list_blocks (nested blocks need their own parent_block_id).",
            not_found=not_found,
        )
    return {
        "ok": True,
        "document_id": doc,
        "parent_block_id": parent,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "not_found": not_found,
    }
