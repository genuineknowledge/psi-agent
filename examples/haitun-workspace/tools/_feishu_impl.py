"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from lark_channel.api.drive import comment as _comment
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

_client: Any = None


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _config() -> tuple[str, str] | None:
    app_id = os.environ.get("PSI_FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("PSI_FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None
    return app_id, app_secret


def _reset_client() -> None:
    global _client
    _client = None


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client  # noqa: PLC0415

    app_id, app_secret = creds
    _client = Client.builder().app_id(app_id).app_secret(app_secret).build()
    return _client


async def _invoke(request: Any) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    try:
        resp = await client.arequest(request)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")

    code = getattr(resp, "code", None)
    msg = getattr(resp, "msg", "") or ""
    data: dict[str, Any] = {}
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        try:
            body = json.loads(bytes(content).decode("utf-8"))
            if isinstance(body, dict):
                data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
                if code is None:
                    code = body.get("code")
                if not msg:
                    msg = body.get("msg", "") or ""
        except ValueError, UnicodeDecodeError:
            pass

    ok = code == 0
    if not ok:
        return {
            "ok": False,
            "code": code,
            "msg": msg,
            "data": data,
            "message": f"Feishu API error {code}: {msg}",
        }
    return {"ok": True, "code": 0, "msg": msg, "data": data}


async def add_comment_impl(file_token: str, file_type: str, content: str) -> dict[str, Any]:
    req = _comment.build_comment_create_request(file_token=file_token, file_type=file_type, content=content)
    return await _invoke(req)


async def list_comments_impl(file_token: str, file_type: str, page_size: int, page_token: str) -> dict[str, Any]:
    req = _comment.build_comment_list_request(
        file_token=file_token,
        file_type=file_type,
        page_size=page_size,
        page_token=page_token or None,
        is_whole="true",
    )
    return await _invoke(req)


async def list_comment_replies_impl(
    file_token: str, file_type: str, comment_id: str, page_size: int, page_token: str
) -> dict[str, Any]:
    req = _comment.build_comment_reply_list_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        page_size=page_size,
        page_token=page_token or None,
    )
    return await _invoke(req)


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
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> dict[str, Any]:
    req = _build_reply_create_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        content=content,
        at_user_id=at_user_id,
    )
    return await _invoke(req)


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


def _build_sheet_meta_request(spreadsheet_token: str) -> BaseRequest:
    return _raw_get(
        "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "spreadsheet_token",
        spreadsheet_token,
    )


def _build_sheet_values_request(spreadsheet_token: str, range_: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.paths["range"] = range_
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _sheet_values_to_text(data: dict[str, Any]) -> str:
    grid = data.get("valueRange", {}).get("values", []) if isinstance(data, dict) else []
    lines: list[str] = []
    for row in grid if isinstance(grid, list) else []:
        cells = [("" if c is None else str(c)) for c in (row if isinstance(row, list) else [])]
        lines.append("\t".join(cells))
    return "\n".join(lines)


async def _read_sheet(token: str) -> dict[str, Any]:
    meta = await _invoke(_build_sheet_meta_request(token))
    if not meta["ok"]:
        return meta
    sheets = meta["data"].get("sheets", [])
    parts: list[str] = []
    for sh in sheets if isinstance(sheets, list) else []:
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        title = sh.get("title", "")
        if not sheet_id:
            continue
        values = await _invoke(_build_sheet_values_request(token, str(sheet_id)))
        if not values["ok"]:
            return values
        parts.append(f"# {title}\n{_sheet_values_to_text(values['data'])}")
    return {"ok": True, "content": "\n\n".join(parts)}


async def read_doc_impl(file_type: str, token: str, max_chars: int) -> dict[str, Any]:
    ft = file_type.strip().lower()
    if ft == "docx":
        res = await _invoke(_build_docx_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "doc":
        res = await _invoke(_build_doc_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "sheet":
        res = await _read_sheet(token)
        content = res.get("content", "") if res["ok"] else ""
    else:
        return _error(f"Unsupported file_type {file_type!r}. Use one of: docx, doc, sheet.")

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
