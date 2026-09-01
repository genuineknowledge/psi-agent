"""Feishu Drive media — download files and message resources.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from typing import Any

import _feishu_impl as _core
import anyio
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

# ── Drive — download a file/attachment to disk ────────────────────────────────
#
# Two sources: a drive media file_token (goes through the medias endpoint), or a
# direct URL (approval-form attachments are direct URLs valid only ~12h — download
# them straight, NOT via medias).


#: The two download endpoints, by ``source_type``. They are not interchangeable and
#: picking the wrong one is a 404 rather than a redirect: ``medias`` serves what lives
#: *inside* a document (images, attachments) and ``files`` serves standalone resource
#: files in Drive (a PDF someone uploaded). Feishu's own online documents — docx, sheet,
#: bitable — are in neither; those have to be exported (``export_doc_impl``).
_DOWNLOAD_URIS = {
    "media": "/open-apis/drive/v1/medias/:file_token/download",
    "file": "/open-apis/drive/v1/files/:file_token/download",
}


def _build_media_download_request(file_token: str, source_type: str = "media") -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = _DOWNLOAD_URIS.get(source_type, _DOWNLOAD_URIS["media"])
    req.paths["file_token"] = file_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _download_url_bytes(url: str) -> tuple[bytes | None, str]:
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:  # transport failure
        return None, f"{type(exc).__name__}: {exc}"
    if resp.status_code in (403, 404):
        return None, (
            f"HTTP {resp.status_code} — the attachment link may have expired "
            "(approval-form URLs are valid ~12h). Re-read the instance detail for a fresh URL."
        )
    if resp.status_code >= 400:
        return None, f"HTTP {resp.status_code}"
    return resp.content, ""


def _media_resp_to_bytes(resp: Any) -> tuple[bytes | None, str]:
    """Extract file bytes from a media-download response, or an (err) if it failed."""
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if not content:
        code = getattr(resp, "code", None)
        return None, f"no file content returned (code={code})"
    data = bytes(content)
    # A JSON error body (not a binary file) means the token was rejected.
    if data[:1] in (b"{", b"["):
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            body = json.loads(data.decode("utf-8"))
            if isinstance(body, dict) and body.get("code") not in (0, None):
                return None, f"Feishu API error {body.get('code')}: {body.get('msg', '')}"
    return data, ""


async def _download_media_as_tenant(file_token: str, source_type: str = "media") -> tuple[bytes | None, str]:
    client = _core._get_client()
    if client is None:
        return None, "Feishu app not configured."
    try:
        resp = await client.arequest(_build_media_download_request(file_token, source_type))
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_media_as_user(
    file_token: str, user_key: str, source_type: str = "media"
) -> tuple[bytes | None, str] | None:
    """Download as the user's UAT. None → no usable UAT (caller decides need_auth)."""
    client = _core._get_uat_client()
    if client is None:
        return None
    uat = await _core._get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return None
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(_build_media_download_request(file_token, source_type), option)
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_media_bytes(
    file_token: str, user_key: str = "", source_type: str = "media"
) -> tuple[bytes | None, str]:
    # Tenant-first: try the bot's token, and only if it's denied (and the user has a
    # cached UAT) retry as the user — so we still fetch files the user can see but the
    # bot can't (e.g. a PDF in the user's wiki/drive) without forcing authorization.
    data, err = await _download_media_as_tenant(file_token, source_type)
    if data is not None:
        return data, ""
    key = user_key.strip()
    if not key:
        return None, err
    user_out = await _download_media_as_user(file_token, key, source_type)
    if user_out is None:
        return None, f"{err} — 或需用户授权后重试. (need_auth)"
    return user_out


async def download_file_impl(
    source: str,
    save_path: str,
    is_url: bool = False,
    user_key: str = "",
    source_type: str = "media",
) -> dict[str, Any]:
    """Download a Feishu file to disk, from one of three kinds of source.

    ``source_type`` selects the endpoint: ``media`` (default — an image or attachment
    that lives inside a document), ``file`` (a standalone resource file in Drive, e.g. an
    uploaded PDF), or ``url`` (a direct link, which is what approval-form attachments
    are). ``is_url=True`` is the older spelling of ``source_type="url"`` and still works.

    ``media`` and ``file`` are not interchangeable — the wrong one is a 404, not a
    redirect — and neither serves Feishu's own online documents (docx/sheet/bitable);
    those must be exported (``export_doc_impl``).

    Pass ``user_key`` (ignored for a direct URL) to download as that user — needed for
    files the user can see but the bot can't (e.g. a PDF in the user's wiki/drive).
    """
    if not source or not save_path:
        return _core._error("source and save_path are required.")
    kind = (source_type or "media").strip().lower()
    if is_url:
        kind = "url"
    if kind not in ("media", "file", "url"):
        return _core._error(
            f"source_type must be one of media / file / url, got {source_type!r}. "
            "media = inside a document, file = a resource file in Drive, url = a direct link.",
            source_type=source_type,
        )
    data, err = await (
        _core._download_url_bytes(source) if kind == "url" else _download_media_bytes(source, user_key, kind)
    )
    if data is None:
        extra = {"need_auth": True} if "need_auth" in (err or "") else {}
        return _core._error(err or "download failed", source=source, source_type=kind, **extra)
    path = pathlib.Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
    except OSError as exc:
        return _core._error(f"could not write file: {exc}", path=str(path))
    return {"ok": True, "path": str(path), "bytes": len(data), "source_type": kind}


# ── Export a cloud doc to pdf / docx / xlsx / csv, on disk ─────────────────────
# Three calls, and unlike every other multi-step flow here the middle one has to be
# *repeated*: Feishu builds the file asynchronously and only says so through job_status.
#
#   1. POST drive/v1/export_tasks {token, type, file_extension, sub_id?} → ticket
#   2. GET  drive/v1/export_tasks/:ticket?token=… until job_status == 0 → file_token
#   3. GET  drive/v1/export_tasks/file/:file_token/download → the bytes
#
# Neither the polling nor the disk write can be expressed as an endpoint-table row, which
# is why this is a tool. Step 3 reuses the download path above (same two-stage
# tenant→user fallback), since an export of someone's own document is exactly the case
# where the bot's token may not be enough.
#
# The 10-minute expiry is why the three steps cannot be split across calls: Feishu
# deletes the built file 10 minutes after the task finishes, so a ticket handed back to
# the caller would usually be worthless by the time it came back.

#: Which target extensions each source type can actually produce. A mismatched pair is
#: refused locally rather than spending an HTTP round trip on Feishu's 1069918.
_EXPORT_FORMATS = {
    "docx": ("pdf", "docx"),
    "doc": ("pdf", "docx"),
    "sheet": ("xlsx", "csv"),
    "bitable": ("xlsx", "csv"),
}

#: ``job_status`` values that will never become 0 no matter how long we wait, and what
#: each one actually means. Polling through these would burn the whole budget on a
#: document that cannot be exported at all.
_EXPORT_FATAL = {
    3: "飞书内部错误, 稍后重试",
    107: "文档过大, 导不出来(重试无用)",
    108: "导出超时",
    109: "文档里有块没权限读",
    110: "没有导出权限",
    111: "文档已被删除",
    122: "文档正在创建副本, 此时不允许导出",
    123: "文档不存在",
    6000: "文档图片过多, 导不出来(重试无用)",
}

#: Poll budget. Feishu's own rate limit on the query endpoint is 100/min, and a normal
#: document finishes in a few seconds; a big one that has not finished in ~60s is better
#: reported than waited on inside one tool call.
_EXPORT_POLL_DELAYS = (0.5, 1.0, 1.5, 2.0, 3.0, 3.0, 4.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0)


def _build_export_create_request(token: str, doc_type: str, file_extension: str, sub_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/export_tasks"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"token": token, "type": doc_type, "file_extension": file_extension}
    if sub_id:
        body["sub_id"] = sub_id
    req.body = body
    return req


def _build_export_query_request(ticket: str, token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/drive/v1/export_tasks/:ticket"
    req.paths["ticket"] = ticket
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # The doc's own token rides in the query even though the ticket already identifies
    # the task; omitting it is a 1069904 invalid param, not a lookup by ticket alone.
    req.add_query("token", token)
    return req


def _build_export_download_request(file_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/drive/v1/export_tasks/file/:file_token/download"
    req.paths["file_token"] = file_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _export_format_error(doc_type: str, file_extension: str, sub_id: str) -> dict[str, Any] | None:
    """Refuse a combination Feishu will reject, before spending a request on it."""
    allowed = _EXPORT_FORMATS.get(doc_type)
    if allowed is None:
        return _core._error(
            f"file_type must be one of {', '.join(sorted(_EXPORT_FORMATS))}, got {doc_type!r}.",
            file_type=doc_type,
        )
    if file_extension not in allowed:
        return _core._error(
            f"{doc_type} 只能导出成 {' / '.join(allowed)}, 不能导出成 {file_extension} (飞书会回 1069918 格式不匹配).",
            file_type=doc_type,
            file_extension=file_extension,
        )
    if file_extension == "csv" and not sub_id:
        # A spreadsheet holds several worksheets and a base several tables; one csv can
        # only be one of them, so Feishu requires the caller to say which.
        need = "sheet_id" if doc_type == "sheet" else "table_id"
        return _core._error(
            f"导出 csv 必须给 sub_id (这里是 {need}) —— 一个{'表格' if doc_type == 'sheet' else '多维表格'}"
            f"里有多张表, csv 装不下多张。缺了飞书回 1069904.",
            file_type=doc_type,
            file_extension=file_extension,
        )
    return None


async def _await_export_file_token(ticket: str, token: str, user_key: str) -> dict[str, Any]:
    """Poll one export task until it produces a file_token, or explain why it never will."""
    last_status: Any = None
    for delay in _core._EXPORT_POLL_DELAYS:
        # A factory rather than one request object: `_invoke` mutates what it is given
        # (token_types narrowed by verify), so a polling loop must build a fresh one.
        res = await _core._invoke(
            lambda: _build_export_query_request(ticket, token),
            user_key=user_key or None,
            prefer="tenant",
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
        last_status = result.get("job_status")
        if last_status == 0:
            file_token = str(result.get("file_token") or "")
            if not file_token:
                return _core._error("导出任务完成了, 但响应里没有 file_token.", ticket=ticket)
            return {
                "ok": True,
                "file_token": file_token,
                "file_name": result.get("file_name", ""),
                "file_size": result.get("file_size"),
            }
        if last_status in _EXPORT_FATAL:
            return _core._error(
                f"导出失败: {_EXPORT_FATAL[last_status]} (job_status={last_status})"
                + (f" —— {result.get('job_error_msg')}" if result.get("job_error_msg") else ""),
                ticket=ticket,
                job_status=last_status,
            )
        await anyio.sleep(delay)
    return _core._error(
        f"导出任务还没做完就到了等待上限 (最后 job_status={last_status}). "
        "大文档可以过一会儿重新导一次 —— 导出结果 10 分钟后就会被删, 所以这里不把 ticket 交回去。",
        ticket=ticket,
        job_status=last_status,
    )


async def export_doc_impl(
    token: str,
    file_type: str,
    file_extension: str,
    save_path: str,
    sub_id: str = "",
    user_key: str = "",
) -> dict[str, Any]:
    """Export a Feishu cloud doc to a local file: create task → poll → download."""
    doc = token.strip()
    if not doc or not save_path.strip():
        return _core._error("token and save_path are required.")
    doc_type = (file_type or "").strip().lower()
    extension = (file_extension or "").strip().lower().lstrip(".")
    sub = sub_id.strip()
    if refusal := _export_format_error(doc_type, extension, sub):
        return refusal

    created = await _core._invoke(
        lambda: _build_export_create_request(doc, doc_type, extension, sub),
        user_key=user_key or None,
        prefer="tenant",
    )
    if not created["ok"]:
        return created
    cdata = created["data"] if isinstance(created["data"], dict) else {}
    ticket = str(cdata.get("ticket") or "")
    if not ticket:
        return _core._error("建导出任务成功了, 但响应里没有 ticket.", token=doc)

    ready = await _await_export_file_token(ticket, doc, user_key)
    if not ready["ok"]:
        return ready

    data, err = await _core._download_export_bytes(ready["file_token"], user_key)
    if data is None:
        return _core._error(
            f"{err or 'download failed'} (导出的文件在任务结束 10 分钟后就会被删, 过期了要重新导一次)",
            ticket=ticket,
            file_token=ready["file_token"],
        )
    path = pathlib.Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
    except OSError as exc:
        return _core._error(f"could not write file: {exc}", path=str(path))
    return {
        "ok": True,
        "path": str(path),
        "bytes": len(data),
        "file_extension": extension,
        "file_name": ready.get("file_name", ""),
        "ticket": ticket,
    }


async def _download_export_bytes(file_token: str, user_key: str = "") -> tuple[bytes | None, str]:
    """The export download, with the same tenant→user fallback as a media download."""
    client = _core._get_client()
    if client is None:
        return None, "Feishu app not configured."
    try:
        resp = await client.arequest(_build_export_download_request(file_token))
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    data, err = _media_resp_to_bytes(resp)
    if data is not None:
        return data, ""
    key = user_key.strip()
    if not key:
        return None, err
    uat_client = _core._get_uat_client()
    if uat_client is None:
        return None, err
    uat = await _core._get_valid_uat(key)
    if uat is None or not uat.access_token:
        return None, f"{err} — 或需用户授权后重试. (need_auth)"
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await uat_client.arequest(_build_export_download_request(file_token), option)
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


# ── Message resources — download an image / file attached to a chat message ────
#
# Distinct from the drive medias endpoint above: images and files sent *inside a
# chat message* are fetched via im/v1/messages/:message_id/resources/:file_key,
# keyed by the message they belong to. The channel auto-downloads resources on the
# message that is triggering the agent right now, but an image discovered later in
# history (via the im/v1/messages list endpoint or feishu_thread_read) can only be pulled with
# this endpoint. The file_key is the ``image_key``/``file_key`` inside the
# message's content JSON; ``type`` is "image" for an image message, "file" for a
# file/audio/video/media attachment.


def _build_message_resource_request(message_id: str, file_key: str, resource_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id/resources/:file_key"
    req.paths["message_id"] = message_id
    req.paths["file_key"] = file_key
    req.add_query("type", resource_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _download_msg_resource_as_tenant(
    message_id: str, file_key: str, resource_type: str
) -> tuple[bytes | None, str]:
    client = _core._get_client()
    if client is None:
        return None, "Feishu app not configured."
    try:
        resp = await client.arequest(_build_message_resource_request(message_id, file_key, resource_type))
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_msg_resource_as_user(
    message_id: str, file_key: str, resource_type: str, user_key: str
) -> tuple[bytes | None, str] | None:
    """Download as the user's UAT. None → no usable UAT (caller decides need_auth)."""
    client = _core._get_uat_client()
    if client is None:
        return None
    uat = await _core._get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return None
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(_build_message_resource_request(message_id, file_key, resource_type), option)
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_msg_resource_bytes(
    message_id: str, file_key: str, resource_type: str, user_key: str = ""
) -> tuple[bytes | None, str]:
    # Tenant-first, same policy as _download_media_bytes: the bot's token is tried
    # first and the UAT only if it's denied (and the user has a cached UAT).
    data, err = await _download_msg_resource_as_tenant(message_id, file_key, resource_type)
    if data is not None:
        return data, ""
    key = user_key.strip()
    if not key:
        return None, err
    user_out = await _download_msg_resource_as_user(message_id, file_key, resource_type, key)
    if user_out is None:
        return None, f"{err} — 或需用户授权后重试. (need_auth)"
    return user_out


async def get_message_image_impl(
    message_id: str, file_key: str, save_path: str, resource_type: str = "image", user_key: str = ""
) -> dict[str, Any]:
    """Download an image/file attached to a chat message to disk.

    Fetches via im/v1/messages/:message_id/resources/:file_key. ``file_key`` is the
    ``image_key`` (image message) or ``file_key`` (file/media message) inside the
    message content JSON. Tenant-first; falls back to the user's UAT when the bot
    can't see it and a user_key is given.
    """
    if not message_id or not file_key or not save_path:
        return _core._error("message_id, file_key and save_path are required.")
    rtype = resource_type.strip() or "image"
    data, err = await _download_msg_resource_bytes(message_id, file_key, rtype, user_key)
    if data is None:
        extra = {"need_auth": True} if "need_auth" in (err or "") else {}
        return _core._error(err or "download failed", message_id=message_id, file_key=file_key, **extra)
    path = pathlib.Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
    except OSError as exc:
        return _core._error(f"could not write file: {exc}", path=str(path))
    return {"ok": True, "path": str(path), "bytes": len(data)}
