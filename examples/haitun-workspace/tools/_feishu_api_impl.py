"""Implementation for the generic ``feishu_api`` tool.

Builds a ``BaseRequest`` from plain JSON arguments and hands it to the shared
``_feishu_impl._invoke`` — so the generic path inherits the authenticated client,
the tenant/user token strategy, rate-limit retry, and the error-code hint tables
rather than re-deriving any of it.

What this module adds on top is the *refusals*: a generic entry point can be pointed
at an endpoint whose request shape it cannot express (binary uploads), and it can be
handed a path it shouldn't reach. Failing early with the name of the right tool is
more useful than a Feishu 400 the caller has to decode.
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

dumps_result = _f.dumps_result

_METHODS = {
    "GET": HttpMethod.GET,
    "POST": HttpMethod.POST,
    "PUT": HttpMethod.PUT,
    "PATCH": HttpMethod.PATCH,
    "DELETE": HttpMethod.DELETE,
}

# Endpoints whose body must carry a real file handle. A JSON string can't express one,
# and `Client.arequest` re-derives `request.files` from the body, so a generic caller
# would get a 400 "boundary not found" with nothing pointing at the cause.
_UPLOAD_ENDPOINTS = {
    "/open-apis/im/v1/images": "feishu_message_send_image",
    "/open-apis/im/v1/files": "feishu_message_send_file / _send_audio / _send_video",
    "/open-apis/drive/v1/medias/upload_all": "feishu_drive_upload",
    "/open-apis/drive/v1/files/upload_all": "feishu_drive_upload",
}

# Where a hand-built request is a known foot-gun. Not a block — a warning attached to
# the result, because the endpoint list below is not exhaustive and a hard refusal
# would strand legitimate calls.
_PREFER_DEDICATED = (
    ("/open-apis/sheets/", "飞书表格写入: 裸 `!A1` 区间会静默丢数据, 建议用 feishu_sheet_write / _append"),
    ("/open-apis/bitable/", "多维表格: 列名对不上会被静默丢弃, 建议用 feishu_bitable_* 工具"),
    ("/open-apis/authen/", "OAuth 流程: 用 feishu_auth_* 工具, 它们管着 UAT 存储与回调接收"),
)

_ALL_HINTS: dict[int, str] = {}
for _name in dir(_f):
    if _name.endswith("_HINTS"):
        _table = getattr(_f, _name)
        if isinstance(_table, dict):
            for _code, _text in _table.items():
                if isinstance(_code, int):
                    _ALL_HINTS.setdefault(_code, _text)


def _loads_object(raw: str, what: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Parse a JSON object argument; empty string means "not given"."""
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, _f.error_result(f'{what} is not valid JSON: {exc}. Pass a JSON object, e.g. \'{{"k":"v"}}\'.')
    if not isinstance(parsed, dict):
        return {}, _f.error_result(f"{what} must be a JSON object, got {type(parsed).__name__}.")
    return {str(k): v for k, v in parsed.items()}, None


def _normalize_uri(uri: str) -> tuple[str, dict[str, Any] | None]:
    """Require an absolute Open Platform path — a relative one silently 404s."""
    path = (uri or "").strip()
    if not path:
        return "", _f.error_result("uri is required, e.g. '/open-apis/contact/v3/users/:user_id'.")
    if path.startswith("http://") or path.startswith("https://"):
        return "", _f.error_result(
            "uri must be a path, not a full URL — the host comes from the SDK client. Use '/open-apis/...' instead."
        )
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/open-apis/"):
        return "", _f.error_result(
            f"uri must start with '/open-apis/', got {path!r}. Every Feishu Open Platform endpoint lives under it."
        )
    return path, None


def _check_not_upload(uri: str) -> dict[str, Any] | None:
    """Refuse endpoints that need a file handle, naming the tool that has one."""
    for endpoint, tool in _UPLOAD_ENDPOINTS.items():
        if uri.startswith(endpoint):
            return _f.error_result(
                f"{endpoint} uploads binary content, which this tool cannot send: the body must be a real "
                f"file handle, not JSON. Use {tool} instead — it does the upload and the send together.",
                code="use_dedicated_tool",
                tool=tool,
            )
    return None


def _warning_for(uri: str) -> str:
    for prefix, note in _PREFER_DEDICATED:
        if uri.startswith(prefix):
            return note
    return ""


def _query_pairs(query: dict[str, Any]) -> dict[str, Any]:
    """Stringify query values; keep lists as lists so the SDK repeats the key."""
    out: dict[str, Any] = {}
    for key, value in query.items():
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, list):
            out[key] = [str(v) for v in value]
        elif value is None:
            continue
        else:
            out[key] = str(value)
    return out


def _build_request(
    http_method: HttpMethod,
    uri: str,
    body: dict[str, Any],
    query: dict[str, Any],
    paths: dict[str, Any],
    prefer: str,
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = http_method
    req.uri = uri
    if prefer == "user":
        req.token_types = {AccessTokenType.USER}
    else:
        req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    for key, value in paths.items():
        req.paths[key] = str(value)
    for key, value in _query_pairs(query).items():
        if isinstance(value, list):
            for item in value:
                req.add_query(key, item)
        else:
            req.add_query(key, value)
    if body:
        req.body = body
    return req


async def call_api_impl(
    method: str,
    uri: str,
    body_json: str = "",
    query_json: str = "",
    paths_json: str = "",
    prefer: str = "tenant",
    identity: str = "",
    user_key: str = "",
) -> dict[str, Any]:
    """Send one arbitrary Open Platform request, reusing the shared invoke path."""
    verb = (method or "").strip().upper()
    if verb not in _METHODS:
        return _f.error_result(f"method must be one of {', '.join(sorted(_METHODS))}, got {method!r}.")
    path, err = _normalize_uri(uri)
    if err:
        return err
    if refusal := _check_not_upload(path):
        return refusal

    body, err = _loads_object(body_json, "body_json")
    if err:
        return err
    query, err = _loads_object(query_json, "query_json")
    if err:
        return err
    paths, err = _loads_object(paths_json, "paths_json")
    if err:
        return err

    missing = [name for name in _placeholders(path) if name not in paths]
    if missing:
        return _f.error_result(
            f'uri has unfilled placeholders {missing}; supply them in paths_json, e.g. \'{{"{missing[0]}":"..."}}\'.',
            code="missing_path_params",
        )

    strategy = "user" if (prefer or "").strip().lower() == "user" else "tenant"
    request = _build_request(_METHODS[verb], path, body, query, paths, strategy)
    res = await _f._invoke(
        request,
        user_key=user_key or None,
        prefer=strategy,
        identity=(identity or "").strip(),
    )
    res = _f._with_hint(res, _ALL_HINTS)
    if (note := _warning_for(path)) and not res.get("ok", True):
        res = {**res, "warning": note}
    return res


def _placeholders(uri: str) -> list[str]:
    """``:name`` segments the SDK will substitute from ``request.paths``."""
    return [seg[1:] for seg in uri.split("/") if seg.startswith(":") and len(seg) > 1]
